"""History editing: interactive rebase editor, squash, drop, fixup from the Log."""

from __future__ import annotations

from ...git import scripted_rebase as SR
from ...git.log import commit_details, is_ancestor
from ..context import Scope, SelectionContext
from ..registry import Action, ActionEnv, ActionError

LOG = frozenset({"log"})
EDITOR = frozenset({"rebase"})
COMMIT = frozenset({Scope.COMMIT})


async def _base_and_todo(env: ActionEnv, oldest_sha: str) -> tuple[str | None, list[SR.TodoItem]]:
    git = env.session.git
    head = await git.try_rev_parse("HEAD")
    if not head or not await is_ancestor(git, oldest_sha, head):
        raise ActionError(f"{oldest_sha[:8]} is not on the current branch")
    details = await commit_details(git, oldest_sha)
    base = details.parents[0] if details.parents else None
    try:
        return base, await SR.todo_for(git, base)
    except SR.RebaseError as e:
        raise ActionError(str(e)) from e


def _oldest(env: ActionEnv) -> str:
    # the Log lists newest first
    return env.ctx.commits[-1].sha


async def run_plan(
    env: ActionEnv,
    base: str | None,
    todo: list[SR.TodoItem],
    messages: dict[str, str],
    label: str,
) -> None:
    try:
        SR.validate(todo)
    except SR.RebaseError as e:
        raise ActionError(str(e)) from e
    res = await SR.run(env.session, base, todo, messages=messages)
    if env.entry:
        env.entry.op = label
    if res.status == "done":
        env.ui.notify(f"{label} — done")
    elif res.status == "conflict":
        env.ui.notify(
            "Rebase stopped with conflicts — resolve them, then Continue", severity="warning"
        )
    else:
        env.ui.notify("Rebase paused for editing — amend, then Continue", severity="warning")


async def _interactive(env: ActionEnv) -> None:
    base, todo = await _base_and_todo(env, _oldest(env))
    env.ui.open_screen("rebase", base=base, todo=todo)


async def _start(env: ActionEnv) -> None:
    form = env.ctx.form
    todo: list[SR.TodoItem] = list(form["todo"])
    await run_plan(
        env, form.get("base"), todo, dict(form.get("messages", {})), "Interactive rebase"
    )
    env.ui.post("rebase.done")


async def _squash(env: ActionEnv) -> None:
    shas = [c.sha for c in env.ctx.commits]
    base, todo = await _base_and_todo(env, _oldest(env))
    try:
        new, message_sha = SR.squash_plan(todo, shas)
    except SR.RebaseError as e:
        raise ActionError(str(e)) from e
    git = env.session.git
    bodies = [(await commit_details(git, s)).body.strip() for s in reversed(shas)]
    message = await env.ui.prompt(
        f"Squash {len(shas)} commits — message", default="\n\n".join(bodies), multiline=True
    )
    if message is None:
        return
    await run_plan(env, base, new, {message_sha: message}, f"Squash {len(shas)} commits")


async def _drop(env: ActionEnv) -> None:
    shas = {c.sha for c in env.ctx.commits}
    base, todo = await _base_and_todo(env, _oldest(env))
    for item in todo:
        if item.sha in shas:
            item.action = "drop"
    await run_plan(env, base, todo, {}, f"Drop {len(shas)} commit(s)")


async def _fixup_staged(env: ActionEnv) -> None:
    git = env.session.git
    target = env.ctx.commits[0]
    staged = await git.run("diff", "--cached", "--quiet", check=False, read_only=True)
    if staged.ok:
        raise ActionError("Stage the changes you want to fold into the commit first")
    base, _ = await _base_and_todo(env, target.sha)
    await git.run("commit", "--quiet", f"--fixup={target.sha}", "--no-verify")
    fixup_sha = await git.rev_parse("HEAD")
    todo = await SR.todo_for(git, base)
    await run_plan(
        env, base, SR.fixup_plan(todo, target.sha, fixup_sha), {}, f"Fixup into {target.short}"
    )


def _on_branch(ctx: SelectionContext) -> bool:
    return bool(ctx.commits) and ctx.session.active.branch is not None and ctx.operation is None


def actions() -> list[Action]:
    return [
        Action(
            "log.interactive_rebase",
            "Interactively Rebase from Here…",
            _interactive,
            scopes=COMMIT,
            when=lambda c: _on_branch(c) and len(c.commits) == 1,
            key="I",
            group="Edit history",
            screens=LOG,
        ),
        Action(
            "log.squash",
            "Squash Commits…",
            _squash,
            scopes=COMMIT,
            when=lambda c: _on_branch(c) and len(c.commits) >= 2,
            key="S",
            group="Edit history",
            screens=LOG,
            mutating=True,
        ),
        Action(
            "log.drop",
            "Drop Commits",
            _drop,
            scopes=COMMIT,
            when=_on_branch,
            key="D",
            group="Edit history",
            screens=LOG,
            mutating=True,
            confirm="Drop {count} commit(s) from the current branch? Undo with z.",
        ),
        Action(
            "log.fixup_staged",
            "Fixup Staged Changes into This Commit",
            _fixup_staged,
            scopes=COMMIT,
            when=lambda c: _on_branch(c) and len(c.commits) == 1,
            key="alt+f",
            group="Edit history",
            screens=LOG,
            mutating=True,
        ),
        Action(
            "rebase.start",
            "Start Rebasing",
            _start,
            key="ctrl+s",
            group="Rebase",
            screens=EDITOR,
            mutating=True,
            when=lambda c: bool(c.form.get("todo")),
        ),
    ]
