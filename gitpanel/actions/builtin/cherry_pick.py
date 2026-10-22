"""Cherry-pick actions (Log and Compare windows)."""

from __future__ import annotations

from ...git import cherry_pick as CP
from ...git import refs as R
from ..context import Scope, SelectionContext
from ..registry import Action, ActionEnv, ActionError
from ..ui_protocol import Field

SCREENS = frozenset({"log", "compare"})
COMMIT = frozenset({Scope.COMMIT})


def _some(ctx: SelectionContext) -> bool:
    return bool(ctx.commits)


def _picked(ctx: SelectionContext) -> set[str]:
    return set(ctx.form.get("picked", ()))


async def _mainline(env: ActionEnv) -> int | None:
    merges = [c for c in env.ctx.commits if c.is_merge]
    if not merges:
        return None
    choice = await env.ui.choose(
        f"{merges[0].short} is a merge — pick the changes relative to which parent?",
        [(str(i + 1), f"Parent {i + 1}: {p[:8]}") for i, p in enumerate(merges[0].parents)],
    )
    if choice is None:
        raise ActionError("Cherry-pick cancelled")
    return int(choice)


def _report(env: ActionEnv, res: CP.PickResult, n: int, onto: str) -> None:
    if res.status == "conflict":
        where = f" in {res.worktree}" if res.worktree else ""
        env.ui.notify(
            f"Cherry-pick stopped with conflicts{where} — resolve them, then Continue",
            severity="warning",
        )
    else:
        env.ui.notify(f"Cherry-picked {n} commit{'s' if n != 1 else ''} onto {onto}")


def _selection(env: ActionEnv) -> list[str]:
    picked = _picked(env.ctx)
    shas = [c.sha for c in env.ctx.commits]
    fresh = [s for s in shas if s not in picked]
    if fresh and len(fresh) < len(shas):
        env.ui.notify(f"Skipping {len(shas) - len(fresh)} commit(s) already on this branch")
    return fresh or shas


async def _pick(
    env: ActionEnv,
    *,
    record_origin: bool = False,
    no_commit: bool = False,
    mainline: int | None = None,
) -> None:
    shas = _selection(env)
    res = await CP.cherry_pick(
        env.session, shas, record_origin=record_origin, no_commit=no_commit, mainline=mainline
    )
    if env.entry:
        env.entry.op = f"Cherry-pick {', '.join(s[:8] for s in shas)}"
    _report(env, res, len(shas), env.session.active.branch_short or "HEAD")


async def _cherry_pick(env: ActionEnv) -> None:
    await _pick(env, mainline=await _mainline(env))


async def _cherry_pick_options(env: ActionEnv) -> None:
    answer = await env.ui.form(
        f"Cherry-pick {len(env.ctx.commits)} commit(s)",
        [
            Field("record_origin", 'Append "(cherry picked from commit …)" (-x)', "bool", True),
            Field("no_commit", "Don't commit — leave the changes staged", "bool", False),
        ],
        body="\n".join(f"{c.short}  {c.subject}" for c in env.ctx.commits[:20]),
        ok_label="Cherry-pick",
    )
    if not answer:
        return
    await _pick(
        env,
        record_origin=bool(answer["record_origin"]),
        no_commit=bool(answer["no_commit"]),
        mainline=await _mainline(env),
    )


async def _cherry_pick_to(env: ActionEnv) -> None:
    current = env.session.active.branch_short
    locals_ = [b for b in await R.branches(env.session.git) if b.is_local and b.short != current]
    if not locals_:
        raise ActionError("No other local branches")
    target = await env.ui.choose(
        "Cherry-pick onto branch",
        [(b.short, b.short + (f"   ⎇ {b.worktree.name}" if b.worktree else "")) for b in locals_],
    )
    if target is None:
        return
    shas = [c.sha for c in env.ctx.commits]
    try:
        res = await CP.pick_onto_branch(env.session, target, shas, mainline=await _mainline(env))
    except CP.PickError as e:
        raise ActionError(str(e)) from e
    if env.entry:
        env.entry.op = f"Cherry-pick {len(shas)} commit(s) onto {target}"
    _report(env, res, len(shas), target)
    if (
        res.status == "conflict"
        and res.worktree is not None
        and await env.ui.confirm(
            "Resolve conflicts",
            f"The pick onto {target} stopped with conflicts in {res.worktree}.\n"
            "Switch gitpanel to that worktree to resolve them?",
        )
    ):
        await env.session.switch(res.worktree)
        env.ui.open_screen("commit")


async def _apply_changes(env: ActionEnv) -> None:
    patch = env.ctx.patch
    assert patch is not None
    sha = patch.source.sha  # type: ignore[union-attr]
    await CP.apply_from_commit(env.session.git, sha, patch.selection)
    env.ui.notify(f"Applied the selected changes from {sha[:8]} to the working tree")


async def _get_from_revision(env: ActionEnv) -> None:
    f = env.ctx.files[0]
    sha = f.source.sha  # type: ignore[union-attr]
    await CP.get_file_from_commit(env.session.git, sha, [f.path])
    env.ui.notify(f"{f.path} now matches {sha[:8]}")


def _commit_patch(ctx: SelectionContext) -> bool:
    from ...git.patch import CommitDiff

    return (
        ctx.focus is Scope.HUNK
        and ctx.patch is not None
        and isinstance(ctx.patch.source, CommitDiff)
    )


def _commit_file(ctx: SelectionContext) -> bool:
    return ctx.focus is Scope.FILE and bool(ctx.files) and ctx.files[0].group == "commit"


def actions() -> list[Action]:
    return [
        Action(
            "log.cherry_pick",
            "Cherry-Pick",
            _cherry_pick,
            scopes=COMMIT,
            when=_some,
            key="C",
            group="Cherry-pick",
            screens=SCREENS,
            mutating=True,
        ),
        Action(
            "log.cherry_pick_options",
            "Cherry-Pick with Options…",
            _cherry_pick_options,
            scopes=COMMIT,
            when=_some,
            key="alt+c",
            group="Cherry-pick",
            screens=SCREENS,
            mutating=True,
        ),
        Action(
            "log.cherry_pick_to",
            "Cherry-Pick to Branch…",
            _cherry_pick_to,
            scopes=COMMIT,
            when=_some,
            key="ctrl+alt+c",
            group="Cherry-pick",
            screens=SCREENS,
            mutating=True,
        ),
        Action(
            "log.apply_changes",
            "Apply Selected Changes",
            _apply_changes,
            scopes=frozenset({Scope.HUNK}),
            when=_commit_patch,
            key="A",
            group="Cherry-pick",
            screens=SCREENS,
            mutating=True,
            destructive=True,
            paths=lambda c: c.patch.paths if c.patch else None,
        ),
        Action(
            "log.get_from_revision",
            "Get from Revision",
            _get_from_revision,
            scopes=frozenset({Scope.FILE}),
            when=_commit_file,
            key="G",
            group="Cherry-pick",
            screens=SCREENS,
            mutating=True,
            destructive=True,
            confirm="Replace {file} in the working tree with its version from this commit?",
            paths=lambda c: [f.path for f in c.files],
        ),
    ]
