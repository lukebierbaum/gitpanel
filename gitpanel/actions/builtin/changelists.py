"""Changelists in the Commit window."""

from __future__ import annotations

from ...git.changelists import DEFAULT, Changelists
from ...git.commit import stage_paths
from ...git.session import ChangeKind
from ..context import Scope, SelectionContext
from ..registry import Action, ActionEnv, ActionError

COMMIT = frozenset({"commit"})


def _store(env: ActionEnv) -> Changelists:
    return Changelists.load(env.session.private_dir() / "changelists.json")


def _unstaged(ctx: SelectionContext) -> list[str]:
    return [f.path for f in ctx.files if f.group == "unstaged"]


def _list_name(ctx: SelectionContext) -> str | None:
    return ctx.form.get("changelist")


def _refresh(env: ActionEnv) -> None:
    env.session.emit(ChangeKind.WORKTREE)


async def _move(env: ActionEnv) -> None:
    cl = _store(env)
    paths = _unstaged(env.ctx)
    options = [(n, n + ("  (active)" if n == cl.active else "")) for n in cl.lists]
    options.append(("__new__", "New changelist…"))
    target = await env.ui.choose(f"Move {len(paths)} file(s) to changelist", options)
    if target is None:
        return
    if target == "__new__":
        target = (await env.ui.prompt("New changelist", placeholder="name") or "").strip()
        if not target:
            return
    try:
        cl.assign(paths, target)
    except ValueError as e:
        raise ActionError(str(e)) from e
    cl.save()
    _refresh(env)


async def _new(env: ActionEnv) -> None:
    name = (await env.ui.prompt("New changelist", placeholder="name") or "").strip()
    if not name:
        return
    cl = _store(env)
    try:
        cl.add(name)
    except ValueError as e:
        raise ActionError(str(e)) from e
    cl.save()
    _refresh(env)


async def _rename(env: ActionEnv) -> None:
    old = _list_name(env.ctx)
    assert old is not None
    new = (await env.ui.prompt(f"Rename changelist {old}", default=old) or "").strip()
    if not new or new == old:
        return
    cl = _store(env)
    try:
        cl.rename(old, new)
    except ValueError as e:
        raise ActionError(str(e)) from e
    cl.save()
    _refresh(env)


async def _delete(env: ActionEnv) -> None:
    name = _list_name(env.ctx)
    assert name is not None
    cl = _store(env)
    try:
        cl.remove(name)
    except ValueError as e:
        raise ActionError(str(e)) from e
    cl.save()
    _refresh(env)


async def _set_active(env: ActionEnv) -> None:
    name = _list_name(env.ctx)
    assert name is not None
    cl = _store(env)
    cl.set_active(name, env.ctx.form.get("all_changed", ()))
    cl.save()
    env.ui.notify(f"New changes now go to “{name}”")
    _refresh(env)


async def _stage_list(env: ActionEnv) -> None:
    await stage_paths(env.session.git, _unstaged(env.ctx))


def actions() -> list[Action]:
    on_list = lambda c: _list_name(c) is not None  # noqa: E731
    custom = lambda c: _list_name(c) not in (None, DEFAULT)  # noqa: E731
    file_scope = frozenset({Scope.FILE})
    return [
        Action(
            "changelist.move",
            "Move to Another Changelist…",
            _move,
            scopes=file_scope,
            when=lambda c: bool(_unstaged(c)),
            key="f6",
            group="Changelists",
            screens=COMMIT,
        ),
        Action("changelist.new", "New Changelist…", _new, group="Changelists", screens=COMMIT),
        Action(
            "changelist.rename",
            "Rename Changelist…",
            _rename,
            scopes=file_scope,
            when=custom,
            group="Changelists",
            screens=COMMIT,
        ),
        Action(
            "changelist.delete",
            "Delete Changelist",
            _delete,
            scopes=file_scope,
            when=custom,
            group="Changelists",
            screens=COMMIT,
            confirm="Delete the changelist? Its files move to the default list.",
        ),
        Action(
            "changelist.set_active",
            "Set Active Changelist",
            _set_active,
            scopes=file_scope,
            when=on_list,
            group="Changelists",
            screens=COMMIT,
        ),
        Action(
            "changelist.stage",
            "Stage Changelist",
            _stage_list,
            scopes=file_scope,
            when=lambda c: on_list(c) and bool(_unstaged(c)),
            group="Changelists",
            screens=COMMIT,
            mutating=True,
        ),
    ]
