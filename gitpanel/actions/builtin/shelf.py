"""Shelve / unshelve and git stash."""

from __future__ import annotations

from ...git import shelf as SH
from ...git import stash as ST
from ...git.runner import GitError
from ..context import Scope, SelectionContext
from ..registry import Action, ActionEnv, ActionError
from ..ui_protocol import Field

COMMIT = frozenset({"commit"})
SHELF_SCREEN = frozenset({"shelf"})


def _changed_paths(ctx: SelectionContext) -> list[str]:
    return sorted({f.path for f in ctx.files if f.group in ("staged", "unstaged", "untracked")})


async def _shelve(env: ActionEnv) -> None:
    paths = _changed_paths(env.ctx) or list(env.ctx.form.get("all_changed", ()))
    if not paths:
        raise ActionError("No changes to shelve")
    name = await env.ui.prompt(
        f"Shelve {len(paths)} file(s)",
        placeholder="name",
        default=str(env.ctx.form.get("message", "")).split("\n")[0],
    )
    if name is None:
        return
    try:
        shelf = await SH.shelve(env.session, name, paths)
    except ValueError as e:
        raise ActionError(str(e)) from e
    if env.entry:
        env.entry.op = f"Shelve {shelf.name}"
    env.ui.notify(f"Shelved {len(shelf.paths)} file(s) as “{shelf.name}”")


async def _stash(env: ActionEnv) -> None:
    answer = await env.ui.form(
        "Stash changes",
        [
            Field("message", "Message", placeholder="WIP on …"),
            Field("untracked", "Include untracked files", "bool", True),
            Field("keep_index", "Keep staged changes in the index", "bool", False),
        ],
        ok_label="Stash",
    )
    if not answer:
        return
    await ST.push(
        env.session.git,
        str(answer["message"]),
        include_untracked=bool(answer["untracked"]),
        keep_index=bool(answer["keep_index"]),
    )
    if env.entry:
        env.entry.op = "Stash"
    env.ui.notify("Changes stashed")


def _shelf(ctx: SelectionContext) -> SH.Shelf | None:
    return ctx.form.get("shelf")


async def _unshelve(env: ActionEnv) -> None:
    shelf = _shelf(env.ctx)
    assert shelf is not None
    paths = env.ctx.form.get("shelf_paths")
    try:
        await SH.unshelve(env.session, shelf, paths)
    except GitError as e:
        raise ActionError(f"Couldn't unshelve cleanly: {e}") from e
    if env.entry:
        env.entry.op = f"Unshelve {shelf.name}"
    env.ui.notify(f"Unshelved “{shelf.name}”")
    env.ui.post("shelf.refresh")


async def _delete_shelf(env: ActionEnv) -> None:
    shelf = _shelf(env.ctx)
    assert shelf is not None
    SH.delete(shelf)
    env.ui.post("shelf.refresh")


async def _stash_apply(env: ActionEnv, pop: bool) -> None:
    s = env.ctx.stash
    assert s is not None
    fn = ST.pop if pop else ST.apply
    res = await fn(env.session.git, s.ref, restore_index=True)
    if not res.ok:
        res = await fn(env.session.git, s.ref)  # index couldn't be restored: plain apply
    if not res.ok:
        if "CONFLICT" in res.text + res.stderr:
            env.ui.notify("Stash applied with conflicts — resolve them", severity="warning")
            return
        raise GitError(res)
    env.ui.notify(f"{'Popped' if pop else 'Applied'} {s.ref}")
    env.ui.post("shelf.refresh")


async def _stash_apply_only(env: ActionEnv) -> None:
    await _stash_apply(env, pop=False)


async def _stash_pop(env: ActionEnv) -> None:
    await _stash_apply(env, pop=True)


async def _stash_drop(env: ActionEnv) -> None:
    s = env.ctx.stash
    assert s is not None
    await ST.drop(env.session.git, s.ref)
    env.ui.post("shelf.refresh")


async def _stash_branch(env: ActionEnv) -> None:
    s = env.ctx.stash
    assert s is not None
    name = await env.ui.prompt(f"New branch from {s.ref}", placeholder="branch-name")
    if not name:
        return
    await ST.branch(env.session.git, name.strip(), s.ref)
    env.ui.post("shelf.refresh")


def actions() -> list[Action]:
    stash_scope = frozenset({Scope.STASH})
    shelf_scope = frozenset({Scope.SHELF})
    has_stash = lambda c: c.stash is not None  # noqa: E731
    return [
        Action(
            "shelf.shelve",
            "Shelve Changes…",
            _shelve,
            key="alt+s",
            group="Shelf",
            screens=COMMIT,
            mutating=True,
            destructive=True,
            silent_backup=True,
            paths=lambda c: _changed_paths(c) or list(c.form.get("all_changed", ())),
            when=lambda c: bool(_changed_paths(c) or c.form.get("all_changed")),
        ),
        Action(
            "stash.push",
            "Stash Changes…",
            _stash,
            key="alt+t",
            group="Shelf",
            screens=COMMIT,
            mutating=True,
            destructive=True,
            silent_backup=True,
            when=lambda c: bool(c.form.get("all_changed")),
        ),
        Action(
            "shelf.unshelve",
            "Unshelve",
            _unshelve,
            scopes=shelf_scope,
            when=lambda c: _shelf(c) is not None,
            key="enter",
            group="Shelf",
            screens=SHELF_SCREEN,
            mutating=True,
            destructive=True,
            silent_backup=True,
        ),
        Action(
            "shelf.delete",
            "Delete Shelf",
            _delete_shelf,
            scopes=shelf_scope,
            when=lambda c: _shelf(c) is not None,
            key="d",
            group="Shelf",
            screens=SHELF_SCREEN,
            confirm="Delete this shelf permanently?",
        ),
        Action(
            "stash.pop",
            "Pop Stash",
            _stash_pop,
            scopes=stash_scope,
            when=has_stash,
            key="enter",
            group="Stash",
            screens=SHELF_SCREEN,
            mutating=True,
        ),
        Action(
            "stash.apply",
            "Apply Stash (keep it)",
            _stash_apply_only,
            scopes=stash_scope,
            when=has_stash,
            key="a",
            group="Stash",
            screens=SHELF_SCREEN,
            mutating=True,
        ),
        Action(
            "stash.drop",
            "Drop Stash",
            _stash_drop,
            scopes=stash_scope,
            when=has_stash,
            key="d",
            group="Stash",
            screens=SHELF_SCREEN,
            mutating=True,
            confirm="Drop this stash? Undo with z.",
        ),
        Action(
            "stash.branch",
            "New Branch from Stash…",
            _stash_branch,
            scopes=stash_scope,
            when=has_stash,
            key="B",
            group="Stash",
            screens=SHELF_SCREEN,
            mutating=True,
        ),
    ]
