"""Commit tool window actions: stage/unstage, commit/amend, rollback, ignore."""

from __future__ import annotations

from ...git import commit as C
from ...git import patch as P
from ...git.session import ChangeKind
from ..context import FileRef, Scope, SelectionContext
from ..registry import Action, ActionEnv, ActionError

SCREEN = frozenset({"commit"})
FILE_OR_HUNK = frozenset({Scope.FILE, Scope.HUNK})


def _files(ctx: SelectionContext, *groups: str) -> list[FileRef]:
    return [f for f in ctx.files if f.group in groups]


def _patch_from(ctx: SelectionContext, source_type: type) -> bool:
    return (
        ctx.focus is Scope.HUNK
        and ctx.patch is not None
        and isinstance(ctx.patch.source, source_type)
        and not ctx.patch.selection.is_empty()
    )


# -- stage / unstage -----------------------------------------------------------


def _can_stage(ctx: SelectionContext) -> bool:
    if ctx.focus is Scope.HUNK:
        return _patch_from(ctx, P.WorkingTree)
    return bool(_files(ctx, "unstaged", "untracked", "conflict"))


async def _stage(env: ActionEnv) -> None:
    ctx, git = env.ctx, env.session.git
    if ctx.focus is Scope.HUNK and ctx.patch:
        patch = P.build_patch(ctx.patch.diffs, ctx.patch.selection)
        await P.apply(git, patch, to="index")
    else:
        await C.stage_paths(git, [f.path for f in _files(ctx, "unstaged", "untracked", "conflict")])


def _can_unstage(ctx: SelectionContext) -> bool:
    if ctx.focus is Scope.HUNK:
        return _patch_from(ctx, P.Staged)
    return bool(_files(ctx, "staged"))


async def _unstage(env: ActionEnv) -> None:
    ctx, git = env.ctx, env.session.git
    if ctx.focus is Scope.HUNK and ctx.patch:
        patch = P.build_patch(ctx.patch.diffs, ctx.patch.selection, reverse=True)
        await P.apply(git, patch, to="index", reverse=True)
    else:
        await C.unstage_paths(git, [f.path for f in _files(ctx, "staged")])


async def _stage_all(env: ActionEnv) -> None:
    await C.stage_all(env.session.git)


async def _unstage_all(env: ActionEnv) -> None:
    await C.unstage_all(env.session.git)


# -- commit ----------------------------------------------------------------------


def _subject(message: str) -> str:
    first = message.strip().splitlines()[0] if message.strip() else ""
    return first if len(first) <= 60 else first[:57] + "…"


async def _commit(env: ActionEnv) -> None:
    form = env.ctx.form
    git = env.session.git
    message = str(form.get("message", ""))
    amend = bool(form.get("amend", False))
    if not message.strip():
        raise ActionError("Enter a commit message")
    staged = await git.run("diff", "--cached", "--quiet", check=False, read_only=True)
    merging = env.ctx.operation is not None
    if staged.ok and not amend and not merging and not form.get("allow_empty"):
        raise ActionError("Nothing staged to commit")
    opts = C.CommitOptions(
        amend=amend,
        signoff=bool(form.get("signoff", False)),
        author=(str(form.get("author") or "").strip() or None),
        allow_empty=bool(form.get("allow_empty", False)),
    )
    await C.commit(git, message, opts)
    C.MessageHistory(env.session.private_dir() / "messages.json").add(message)
    subject = _subject(message)
    if env.entry is not None:
        env.entry.op = f"{'Amend' if amend else 'Commit'}: {subject}"
    env.ui.notify(f"{'Amended' if amend else 'Committed'}: {subject}")
    env.ui.post("commit.done")


async def _toggle_amend(env: ActionEnv) -> None:
    env.ui.post("commit.toggle_amend")


# -- rollback / ignore ------------------------------------------------------------


def _can_rollback(ctx: SelectionContext) -> bool:
    if ctx.focus is Scope.HUNK:
        return _patch_from(ctx, P.WorkingTree) or _patch_from(ctx, P.Staged)
    return bool(_files(ctx, "staged", "unstaged", "untracked"))


def _rollback_paths(ctx: SelectionContext) -> list[str] | None:
    if ctx.focus is Scope.HUNK and ctx.patch:
        return ctx.patch.paths
    return sorted(
        {f.path for f in ctx.files}
        | {f.status.orig_path for f in ctx.files if f.status and f.status.orig_path}
    )


async def _rollback(env: ActionEnv) -> None:
    ctx, git = env.ctx, env.session.git
    if ctx.focus is Scope.HUNK and ctx.patch:
        patch = P.build_patch(ctx.patch.diffs, ctx.patch.selection, reverse=True)
        target = "worktree" if isinstance(ctx.patch.source, P.WorkingTree) else "both"
        await P.apply(git, patch, to=target, reverse=True)
        return
    untracked = [f.path for f in _files(ctx, "untracked")]
    tracked = sorted(
        {f.path for f in _files(ctx, "staged", "unstaged")}
        | {f.status.orig_path for f in ctx.files if f.status and f.status.orig_path}
    )
    await C.rollback(git, tracked)
    for p in untracked:
        target = git.cwd / p
        if target.is_file() or target.is_symlink():
            target.unlink()
    n = len(tracked) + len(untracked)
    env.ui.notify(f"Rolled back {n} file{'s' if n != 1 else ''}")


async def _ignore(env: ActionEnv) -> None:
    root = env.session.active.path
    for f in _files(env.ctx, "untracked"):
        C.add_to_gitignore(root, f.path)
    env.session.emit(ChangeKind.WORKTREE | ChangeKind.INDEX)


def actions() -> list[Action]:
    return [
        Action(
            "commit.stage",
            "Stage",
            _stage,
            scopes=FILE_OR_HUNK,
            when=_can_stage,
            key="space",
            group="Staging",
            screens=SCREEN,
            mutating=True,
            description="Stage selected files, hunks or lines",
        ),
        Action(
            "commit.unstage",
            "Unstage",
            _unstage,
            scopes=FILE_OR_HUNK,
            when=_can_unstage,
            key="space",
            group="Staging",
            screens=SCREEN,
            mutating=True,
        ),
        Action(
            "commit.stage_all",
            "Stage all changes",
            _stage_all,
            key="a",
            group="Staging",
            screens=SCREEN,
            mutating=True,
        ),
        Action(
            "commit.unstage_all",
            "Unstage all",
            _unstage_all,
            key="A",
            group="Staging",
            screens=SCREEN,
            mutating=True,
        ),
        Action(
            "commit.commit",
            "Commit",
            _commit,
            key="ctrl+k",  # PyCharm
            group="Commit",
            screens=SCREEN,
            mutating=True,
            reset_mode="soft",  # undo leaves the changes staged
        ),
        Action(
            "commit.amend_toggle",
            "Toggle amend",
            _toggle_amend,
            key="alt+m",  # ctrl+m is Enter in terminals
            group="Commit",
            screens=SCREEN,
        ),
        Action(
            "commit.commit_and_push",
            "Commit and Push…",
            _commit,
            when=lambda ctx: False,  # enabled in M3 together with push
            key="ctrl+alt+k",  # PyCharm
            group="Commit",
            screens=SCREEN,
            mutating=True,
            reset_mode="soft",
        ),
        Action(
            "commit.rollback",
            "Rollback",
            _rollback,
            scopes=FILE_OR_HUNK,
            when=_can_rollback,
            key="ctrl+alt+z",  # PyCharm
            group="Changes",
            screens=SCREEN,
            mutating=True,
            destructive=True,
            confirm="Discard local changes in {count} selection(s)? You can undo this with z.",
            confirm_title="Rollback changes",
            paths=_rollback_paths,
        ),
        Action(
            "commit.ignore",
            "Add to .gitignore",
            _ignore,
            scopes=frozenset({Scope.FILE}),
            when=lambda ctx: bool(_files(ctx, "untracked")),
            group="Changes",
            screens=SCREEN,
        ),
    ]
