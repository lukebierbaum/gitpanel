"""Log tool window actions: checkout, branch/tag here, reset, revert, reword, copy hash."""

from __future__ import annotations

from ...git import scripted_rebase as SR
from ...git.log import commit_details, is_ancestor
from ...git.reset_revert import checkout_detached, reset, revert
from ...git.runner import GitError
from ..context import Scope, SelectionContext
from ..registry import Action, ActionEnv, ActionError

SCREEN = frozenset({"log"})
COMMIT = frozenset({Scope.COMMIT})


def _one(ctx: SelectionContext) -> bool:
    return len(ctx.commits) == 1


def _some(ctx: SelectionContext) -> bool:
    return bool(ctx.commits)


async def _checkout(env: ActionEnv) -> None:
    c = env.ctx.commits[0]
    await checkout_detached(env.session.git, c.sha)
    if env.entry:
        env.entry.op = f"Checkout {c.short}"
    env.ui.notify(f"HEAD detached at {c.short}")


async def _new_branch(env: ActionEnv) -> None:
    c = env.ctx.commits[0]
    name = await env.ui.prompt(f"New branch from {c.short}", placeholder="branch-name")
    if not name or not name.strip():
        return
    await env.session.git.run("switch", "-c", name.strip(), c.sha)
    if env.entry:
        env.entry.op = f"New branch {name.strip()}"
    env.ui.notify(f"Switched to new branch {name.strip()}")


async def _new_tag(env: ActionEnv) -> None:
    c = env.ctx.commits[0]
    name = await env.ui.prompt(f"New tag on {c.short}", placeholder="v1.2.3")
    if not name or not name.strip():
        return
    await env.session.git.run("tag", name.strip(), c.sha)
    if env.entry:
        env.entry.op = f"Tag {name.strip()}"


RESET_MODES = [
    ("soft", "Soft — move the branch, keep changes staged"),
    ("mixed", "Mixed — move the branch, keep changes unstaged"),
    ("keep", "Keep — move the branch, keep local changes"),
    ("hard", "Hard — discard all changes"),
]


async def _reset(env: ActionEnv) -> None:
    c = env.ctx.commits[0]
    mode = await env.ui.choose(f"Reset current branch to {c.short}", RESET_MODES)
    if mode is None:
        return
    await reset(env.session.git, c.sha, mode)  # type: ignore[arg-type]
    if env.entry:
        env.entry.op = f"Reset ({mode}) to {c.short}"
        env.entry.reset_mode = mode  # type: ignore[assignment]


async def _revert(env: ActionEnv) -> None:
    commits = env.ctx.commits  # newest first, as shown in the log
    mainline = 1 if any(c.is_merge for c in commits) else None
    res = await revert(env.session.git, [c.sha for c in commits], mainline=mainline)
    if env.entry:
        env.entry.op = f"Revert {', '.join(c.short for c in commits)}"
    if not res.ok:
        if "CONFLICT" in res.text + res.stderr:
            env.ui.notify(
                "Revert stopped with conflicts — resolve them, then Continue", severity="warning"
            )
            return
        raise GitError(res)
    env.ui.notify(f"Reverted {len(commits)} commit{'s' if len(commits) != 1 else ''}")


async def _reword(env: ActionEnv) -> None:
    c = env.ctx.commits[0]
    git = env.session.git
    details = await commit_details(git, c.sha)
    message = await env.ui.prompt(
        f"Edit message of {c.short}", default=details.body, multiline=True
    )
    if message is None or message.strip() == details.body.strip():
        return
    if not message.strip():
        raise ActionError("The commit message cannot be empty")
    head = await git.try_rev_parse("HEAD")
    if env.entry:
        env.entry.op = f"Reword {c.short}"
    if c.sha == head:
        await git.run(
            "commit",
            "--amend",
            "--only",
            "--allow-empty",
            "--cleanup=strip",
            "-F",
            "-",
            input=message.encode(),
        )
        return
    if not head or not await is_ancestor(git, c.sha, head):
        raise ActionError(f"{c.short} is not on the current branch")
    base = c.parents[0] if c.parents else None
    try:
        todo = await SR.todo_for(git, base)
    except SR.RebaseError as e:
        raise ActionError(str(e)) from e
    for item in todo:
        if item.sha == c.sha:
            item.action = "reword"
    res = await SR.run(env.session, base, todo, messages={c.sha: message})
    if res.status != "done":
        env.ui.notify("Rebase stopped — resolve conflicts, then Continue", severity="warning")


async def _copy_hash(env: ActionEnv) -> None:
    text = " ".join(c.sha for c in env.ctx.commits)
    env.ui.post("clipboard", text=text)
    env.ui.notify(f"Copied {text[:12]}{'…' if len(text) > 12 else ''}")


async def _new_worktree(env: ActionEnv) -> None:
    from .worktrees import new_worktree_flow

    await new_worktree_flow(env, at=env.ctx.commits[0].sha)


async def _show_commit(env: ActionEnv) -> None:
    env.ui.open_screen("commit")


async def _show_log(env: ActionEnv) -> None:
    env.ui.open_screen("log")


def actions() -> list[Action]:
    return [
        Action("app.show_commit", "Commit window", _show_commit, key="alt+0", group="Tool windows"),
        Action("app.show_log", "Git Log", _show_log, key="alt+9", group="Tool windows"),
        Action(
            "log.checkout_revision",
            "Checkout Revision",
            _checkout,
            scopes=COMMIT,
            when=_one,
            group="Commit",
            screens=SCREEN,
            mutating=True,
        ),
        Action(
            "log.new_branch",
            "New Branch…",
            _new_branch,
            scopes=COMMIT,
            when=_one,
            key="B",
            group="Commit",
            screens=SCREEN,
            mutating=True,
        ),
        Action(
            "log.new_tag",
            "New Tag…",
            _new_tag,
            scopes=COMMIT,
            when=_one,
            key="T",
            group="Commit",
            screens=SCREEN,
            mutating=True,
        ),
        Action(
            "log.reset",
            "Reset Current Branch to Here…",
            _reset,
            scopes=COMMIT,
            when=_one,
            group="Commit",
            screens=SCREEN,
            mutating=True,
            destructive=True,
            confirm="Reset the current branch to {short}? Uncommitted changes are backed up "
            "and the reset can be undone with z.",
            confirm_title="Reset current branch",
        ),
        Action(
            "log.revert",
            "Revert Commit",
            _revert,
            scopes=COMMIT,
            when=_some,
            group="Commit",
            screens=SCREEN,
            mutating=True,
            confirm="Revert {count} commit(s)? New commits undoing them will be created.",
        ),
        Action(
            "log.reword",
            "Edit Commit Message…",
            _reword,
            scopes=COMMIT,
            when=_one,
            key="R",
            group="Edit history",
            screens=SCREEN,
            mutating=True,
        ),
        Action(
            "log.new_worktree",
            "New Worktree at This Commit…",
            _new_worktree,
            scopes=COMMIT,
            when=_one,
            key="W",
            group="Worktree",
            screens=SCREEN,
            mutating=True,
        ),
        Action(
            "log.copy_hash",
            "Copy Revision Number",
            _copy_hash,
            scopes=COMMIT,
            when=_some,
            key="y",
            group="Commit",
            screens=SCREEN,
        ),
    ]
