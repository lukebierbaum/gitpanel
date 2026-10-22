"""Worktrees tool window actions."""

from __future__ import annotations

import os
from pathlib import Path

from ...git import worktrees as W
from ..context import Scope, SelectionContext
from ..registry import Action, ActionEnv, ActionError
from ..ui_protocol import Field

SCREEN = frozenset({"worktrees"})
WT = frozenset({Scope.WORKTREE})


def _wt(ctx: SelectionContext) -> bool:
    return ctx.worktree is not None and not ctx.worktree.bare


def _is_active(ctx: SelectionContext) -> bool:
    return ctx.worktree is not None and (
        ctx.worktree.path.resolve() == ctx.session.active.path.resolve()
    )


async def _switch(env: ActionEnv) -> None:
    wt = env.ctx.worktree
    assert wt is not None
    if _is_active(env.ctx):
        env.ui.open_screen("commit")
        return
    if not wt.path.exists():
        raise ActionError(f"{wt.path} no longer exists — prune it")
    await env.session.switch(wt.path)
    env.ui.notify(f"Switched to worktree {wt.name}")
    env.ui.open_screen("commit")


async def new_worktree_flow(env: ActionEnv, *, at: str | None = None) -> None:
    """Create a worktree: new branch, existing branch, or detached at a commit."""
    git = env.session.git
    main = (await env.session.worktrees())[0].path
    kinds = [("new", "New branch"), ("existing", "Existing branch"), ("detached", "Detached HEAD")]
    answer = await env.ui.form(
        "New worktree",
        [
            Field("kind", "Create from", "choice", "new", options=kinds),
            Field("name", "Branch (new or existing)", placeholder="feature/…"),
            Field("start", "Start point / commit", default=at or "HEAD"),
            Field("path", "Path (empty = ../<repo>.worktrees/<branch>)"),
            Field("switch", "Switch to it", "bool", True),
        ],
        ok_label="Create",
    )
    if not answer:
        return
    kind = answer["kind"]
    name = str(answer["name"]).strip()
    start = str(answer["start"]).strip() or "HEAD"
    if kind in ("new", "existing") and not name:
        raise ActionError("Enter a branch name")
    label = name or start
    path = (
        Path(str(answer["path"])).expanduser()
        if str(answer["path"]).strip()
        else W.default_path(main, label, env.setting("worktree_template", W.DEFAULT_TEMPLATE))
    )
    if kind == "new":
        await W.add(git, path, new_branch=name, start=start)
    elif kind == "existing":
        await W.add(git, path, branch=name)
    else:
        await W.add(git, path, detach=start)
    if env.entry:
        env.entry.op = f"New worktree {path.name}"
    env.ui.notify(f"Created worktree {path}")
    env.ui.post("worktrees.refresh")
    if answer["switch"]:
        await env.session.switch(path)
        env.ui.open_screen("commit")


async def _new(env: ActionEnv) -> None:
    await new_worktree_flow(env)


async def _remove(env: ActionEnv) -> None:
    wt = env.ctx.worktree
    assert wt is not None
    if wt.is_main:
        raise ActionError("The main worktree cannot be removed")
    if _is_active(env.ctx):
        raise ActionError("Switch to another worktree before removing this one")
    summary = await W.summary(wt)
    force = False
    if summary.dirty:
        force = await env.ui.confirm(
            "Worktree has local changes",
            f"{wt.path} has uncommitted changes ({summary.staged} staged, {summary.unstaged} "
            f"modified, {summary.untracked} untracked).\nRemove it anyway? Those changes are lost.",
            danger=True,
        )
        if not force:
            return
    elif not await env.ui.confirm("Remove worktree", f"Remove {wt.path}?\nThe branch is kept."):
        return
    if wt.locked is not None and not await env.ui.confirm(
        "Worktree is locked",
        f"{wt.name} is locked{': ' + wt.locked if wt.locked else ''}.\nRemove it anyway?",
    ):
        return
    await W.remove(env.session.git, wt.path, force=force, locked=wt.locked is not None)
    env.ui.notify(f"Removed worktree {wt.name}")
    env.ui.post("worktrees.refresh")


async def _move(env: ActionEnv) -> None:
    wt = env.ctx.worktree
    assert wt is not None
    new = await env.ui.prompt(f"Move {wt.name} to", default=str(wt.path))
    if not new or Path(new).expanduser() == wt.path:
        return
    await W.move(env.session.git, wt.path, Path(new).expanduser())
    env.ui.post("worktrees.refresh")


async def _lock_toggle(env: ActionEnv) -> None:
    wt = env.ctx.worktree
    assert wt is not None
    if wt.locked is not None:
        await W.unlock(env.session.git, wt.path)
        env.ui.notify(f"Unlocked {wt.name}")
    else:
        reason = await env.ui.prompt(f"Lock {wt.name}", placeholder="reason (optional)")
        if reason is None:
            return
        await W.lock(env.session.git, wt.path, reason)
        env.ui.notify(f"Locked {wt.name}")
    env.ui.post("worktrees.refresh")


async def _prune(env: ActionEnv) -> None:
    out = await W.prune(env.session.git)
    env.ui.notify(out or "Nothing to prune")
    env.ui.post("worktrees.refresh")


async def _repair(env: ActionEnv) -> None:
    await W.repair(env.session.git)
    env.ui.notify("Worktree administrative files repaired")
    env.ui.post("worktrees.refresh")


async def _shell(env: ActionEnv) -> None:
    wt = env.ctx.worktree
    assert wt is not None
    await env.ui.run_in_terminal([os.environ.get("SHELL", "/bin/sh")], wt.path)


async def _copy_path(env: ActionEnv) -> None:
    wt = env.ctx.worktree
    assert wt is not None
    env.ui.post("clipboard", text=str(wt.path))
    env.ui.notify(f"Copied {wt.path}")


def actions() -> list[Action]:
    def a(id_, label, run, when=_wt, key=None, mutating=False, **kw):  # type: ignore[no-untyped-def]
        return Action(
            id_,
            label,
            run,
            scopes=WT,
            when=when,
            key=key,
            group="Worktree",
            screens=SCREEN,
            mutating=mutating,
            **kw,
        )

    return [
        a("worktree.switch", "Switch to Worktree", _switch, key="enter"),
        Action(
            "worktree.new",
            "New Worktree…",
            _new,
            key="n",
            group="Worktree",
            screens=SCREEN,
            mutating=True,
        ),
        a("worktree.remove", "Remove Worktree…", _remove, key="d"),
        a("worktree.move", "Move Worktree…", _move, key="m"),
        a("worktree.lock", "Lock / Unlock", _lock_toggle, key="l"),
        Action("worktree.prune", "Prune Stale Worktrees", _prune, group="Worktree", screens=SCREEN),
        Action("worktree.repair", "Repair Worktrees", _repair, group="Worktree", screens=SCREEN),
        a("worktree.shell", "Open Shell Here", _shell, key="s"),
        a("worktree.copy_path", "Copy Path", _copy_path, key="y"),
    ]
