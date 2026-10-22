"""Branches popup actions (PyCharm's Git Branches popup)."""

from __future__ import annotations

from pathlib import Path

from ...git import refs as R
from ...git import remote as RM
from ...git import tags as T
from ...git import worktrees as W
from ...git.runner import GitError
from ..context import Scope, SelectionContext
from ..registry import Action, ActionEnv, ActionError
from ..ui_protocol import Field
from .network import with_auth_retry
from .remote import push_flow

SCREEN = frozenset({"branches"})
BRANCH = frozenset({Scope.BRANCH})


def _ref(ctx: SelectionContext):  # type: ignore[no-untyped-def]
    return ctx.refs[0] if ctx.refs else None


def _is_current(ctx: SelectionContext) -> bool:
    r = _ref(ctx)
    return r is not None and r.name == ctx.session.active.branch


def _local(ctx: SelectionContext) -> bool:
    r = _ref(ctx)
    return r is not None and r.is_local


def _remote(ctx: SelectionContext) -> bool:
    r = _ref(ctx)
    return r is not None and r.is_remote


def _tag(ctx: SelectionContext) -> bool:
    r = _ref(ctx)
    return r is not None and r.is_tag


def _branch(ctx: SelectionContext) -> bool:
    return _local(ctx) or _remote(ctx)


def _not_current_branch(ctx: SelectionContext) -> bool:
    return _branch(ctx) and not _is_current(ctx)


def _close(env: ActionEnv) -> None:
    env.ui.post("popup.close")


async def _conflict_aware(env: ActionEnv, res, what: str) -> None:  # type: ignore[no-untyped-def]
    if res.ok:
        return
    if "CONFLICT" in res.text + res.stderr:
        env.ui.notify(
            f"{what} stopped with conflicts — resolve them, then Continue", severity="warning"
        )
        return
    raise GitError(res)


# -- checkout -----------------------------------------------------------------------


async def _checkout(env: ActionEnv) -> None:
    ref = _ref(env.ctx)
    git = env.session.git
    if ref is None:
        return
    if ref.is_tag:
        await git.run("switch", "--detach", "-q", ref.name)
        env.ui.notify(f"HEAD detached at {ref.short}")
        _close(env)
        return
    if ref.is_remote:
        local = await R.checkout_remote(git, ref.name)
        env.ui.notify(f"Checked out {local} tracking {ref.short}")
        _close(env)
        return
    if ref.name == env.session.active.branch:
        env.ui.notify(f"{ref.short} is already checked out")
        return
    holder = W.for_branch(await env.session.worktrees(), ref.name)
    if holder is not None and holder.path.resolve() != env.session.active.path.resolve():
        if await env.ui.confirm(
            "Branch is checked out in another worktree",
            f"{ref.short} is checked out in {holder.path}.\nSwitch gitpanel to that worktree?",
        ):
            await env.session.switch(holder.path)
            env.ui.notify(f"Switched to worktree {holder.path.name}")
            _close(env)
        return
    await R.checkout(git, ref.short)
    if env.entry:
        env.entry.op = f"Checkout {ref.short}"
    env.ui.notify(f"Checked out {ref.short}")
    _close(env)


async def _new_branch(env: ActionEnv) -> None:
    ref = _ref(env.ctx)
    start = ref.name if ref else "HEAD"
    title = f"New branch from {ref.short if ref else 'HEAD'}"
    answer = await env.ui.form(
        title,
        [
            Field("name", "Name", placeholder="feature/…"),
            Field("checkout", "Checkout branch", "bool", True),
        ],
        ok_label="Create",
    )
    if not answer or not str(answer["name"]).strip():
        return
    name = str(answer["name"]).strip()
    await R.create(env.session.git, name, start, checkout=bool(answer["checkout"]))
    if env.entry:
        env.entry.op = f"New branch {name}"
    env.ui.notify(f"Created {name}")
    if answer["checkout"]:
        _close(env)


async def _checkout_rebase(env: ActionEnv) -> None:
    ref = _ref(env.ctx)
    current = env.session.active.branch_short
    if not current:
        raise ActionError("HEAD is detached")
    res = await R.checkout_and_rebase_onto_current(env.session.git, ref.short, current)
    await _conflict_aware(env, res, "Rebase")
    _close(env)


async def _rebase_onto(env: ActionEnv) -> None:
    ref = _ref(env.ctx)
    res = await R.rebase_current_onto(env.session.git, ref.short)
    await _conflict_aware(env, res, "Rebase")
    if env.entry:
        env.entry.op = f"Rebase onto {ref.short}"
    _close(env)


async def _merge(env: ActionEnv) -> None:
    ref = _ref(env.ctx)
    res = await R.merge_into_current(env.session.git, ref.short)
    await _conflict_aware(env, res, "Merge")
    if env.entry:
        env.entry.op = f"Merge {ref.short}"
    _close(env)


async def _compare(env: ActionEnv) -> None:
    ref = _ref(env.ctx)
    env.ui.open_screen("log", branch=f"HEAD...{ref.short}")
    _close(env)


async def _push(env: ActionEnv) -> None:
    ref = _ref(env.ctx)
    if ref.is_tag:
        remotes = await RM.remotes(env.session.git)
        if not remotes:
            raise ActionError("No remotes configured")
        await with_auth_retry(
            env,
            lambda: T.push_tag(env.session.git, remotes[0].name, ref.short),
            ["push", remotes[0].name, f"refs/tags/{ref.short}"],
        )
        env.ui.notify(f"Pushed tag {ref.short}")
        return
    await push_flow(env, ref.short)


async def _rename(env: ActionEnv) -> None:
    ref = _ref(env.ctx)
    new = await env.ui.prompt(f"Rename {ref.short}", default=ref.short)
    if not new or new.strip() == ref.short:
        return
    await R.rename(env.session.git, ref.short, new.strip())
    if env.entry:
        env.entry.op = f"Rename {ref.short} → {new.strip()}"


async def _delete(env: ActionEnv) -> None:
    ref = _ref(env.ctx)
    git = env.session.git
    if ref.is_tag:
        if not await env.ui.confirm("Delete tag", f"Delete tag {ref.short}?", danger=True):
            return
        await T.delete(git, ref.short)
        return
    if ref.is_remote:
        if not await env.ui.confirm(
            "Delete remote branch", f"Delete {ref.short} on the remote?", danger=True
        ):
            return
        await with_auth_retry(
            env,
            lambda: RM.delete_remote_branch(git, ref.remote, ref.remote_branch),
            ["push", ref.remote or "", "--delete", ref.remote_branch or ""],
        )
        env.ui.notify(f"Deleted {ref.short}")
        return
    if _is_current(env.ctx):
        raise ActionError("Cannot delete the checked-out branch")
    holder = W.for_branch(await env.session.worktrees(), ref.name)
    if holder is not None:
        raise ActionError(f"{ref.short} is checked out in worktree {holder.path}")
    merged = await R.is_merged(git, ref.short)
    body = f"Delete {ref.short}?" + ("" if merged else "\n\nIt is not fully merged into HEAD.")
    if not await env.ui.confirm(
        "Delete branch", body + "\nYou can undo this with z.", danger=not merged
    ):
        return
    await R.delete(git, ref.short, force=True)
    if env.entry:
        env.entry.op = f"Delete branch {ref.short}"
    env.ui.notify(f"Deleted {ref.short} — press z to restore")


async def _favorite(env: ActionEnv) -> None:
    ref = _ref(env.ctx)
    on = R.Favorites(env.session.private_dir(shared=True) / "favorites.json").toggle(ref.name)
    env.ui.post("branches.refresh")
    env.ui.notify(f"{'Added' if on else 'Removed'} {ref.short} {'to' if on else 'from'} favourites")


async def _set_upstream(env: ActionEnv) -> None:
    ref = _ref(env.ctx)
    remotes = [b for b in await R.branches(env.session.git) if b.is_remote]
    if not remotes:
        raise ActionError("No remote branches")
    choice = await env.ui.choose(f"Upstream for {ref.short}", [(b.short, b.short) for b in remotes])
    if choice:
        await R.set_upstream(env.session.git, ref.short, choice)


async def _unset_upstream(env: ActionEnv) -> None:
    ref = _ref(env.ctx)
    await R.unset_upstream(env.session.git, ref.short)


async def _pull_into(env: ActionEnv, mode: str) -> None:
    ref = _ref(env.ctx)
    git = env.session.git
    res = await (
        git.run("merge", "--no-edit", ref.short, check=False)
        if mode == "merge"
        else git.run("rebase", ref.short, check=False)
    )
    await _conflict_aware(env, res, mode.capitalize())
    _close(env)


async def _pull_merge(env: ActionEnv) -> None:
    await _pull_into(env, "merge")


async def _pull_rebase(env: ActionEnv) -> None:
    await _pull_into(env, "rebase")


async def _new_worktree(env: ActionEnv) -> None:
    ref = _ref(env.ctx)
    if _is_current(env.ctx):
        raise ActionError(
            f"{ref.short} is checked out here; create the worktree from another branch"
        )
    main = (await env.session.worktrees())[0].path
    local = ref.remote_branch if ref.is_remote else ref.short
    answer = await env.ui.form(
        f"New worktree for {ref.short}",
        [
            Field("path", "Path", default=str(W.default_path(main, local or ref.short))),
            Field("switch", "Switch to it", "bool", True),
        ],
        ok_label="Create",
    )
    if not answer:
        return
    path = Path(str(answer["path"])).expanduser()
    git = env.session.git
    if ref.is_remote:
        exists = await git.try_rev_parse(f"refs/heads/{local}")
        if exists:
            await W.add(git, path, branch=local)
        else:
            await W.add(git, path, new_branch=local, start=ref.short)
            await R.set_upstream(git, local, ref.short)
    else:
        await W.add(git, path, branch=ref.short)
    if env.entry:
        env.entry.op = f"New worktree {path.name}"
    env.ui.notify(f"Created worktree {path}")
    if answer["switch"]:
        await env.session.switch(path)
    _close(env)


def actions() -> list[Action]:
    def a(id_: str, label: str, run, when, key=None, group="Branch", mutating=True, **kw):  # type: ignore[no-untyped-def]
        return Action(
            id_,
            label,
            run,
            scopes=BRANCH,
            when=when,
            key=key,
            group=group,
            screens=SCREEN,
            mutating=mutating,
            **kw,
        )

    return [
        a("branch.checkout", "Checkout", _checkout, lambda c: _ref(c) is not None, key="enter"),
        a("branch.new_from", "New Branch from Here…", _new_branch, lambda c: True, key="ctrl+n"),
        a(
            "branch.checkout_rebase",
            "Checkout and Rebase onto Current",
            _checkout_rebase,
            lambda c: _local(c) and not _is_current(c) and c.session.active.branch is not None,
        ),
        a(
            "branch.compare",
            "Compare with Current",
            _compare,
            _not_current_branch,
            key="ctrl+e",
            mutating=False,
        ),
        a("branch.rebase_onto", "Rebase Current onto Selected", _rebase_onto, _not_current_branch),
        a("branch.merge", "Merge into Current", _merge, _not_current_branch),
        a("branch.pull_merge", "Pull into Current Using Merge", _pull_merge, _remote),
        a("branch.pull_rebase", "Pull into Current Using Rebase", _pull_rebase, _remote),
        a("branch.push", "Push…", _push, lambda c: _local(c) or _tag(c), key="ctrl+k"),
        a("branch.rename", "Rename…", _rename, _local, key="f2"),
        a("branch.delete", "Delete", _delete, lambda c: _ref(c) is not None, key="ctrl+d"),
        a(
            "branch.favorite",
            "Add to / Remove from Favourites",
            _favorite,
            _branch,
            key="ctrl+f",
            mutating=False,
        ),
        a("branch.set_upstream", "Set Upstream…", _set_upstream, _local),
        a(
            "branch.unset_upstream",
            "Unset Upstream",
            _unset_upstream,
            lambda c: _local(c) and getattr(_ref(c), "upstream", None) is not None,
        ),
        a(
            "branch.new_worktree",
            "New Worktree from Branch…",
            _new_worktree,
            _branch,
            key="ctrl+w",
            group="Worktree",
        ),
    ]
