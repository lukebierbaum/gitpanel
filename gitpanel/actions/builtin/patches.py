"""Patch builder and bisect actions (Log / History)."""

from __future__ import annotations

from collections.abc import AsyncIterator

from ...git import bisect as B
from ...git import patch_builder as PB
from ...git.log import LogFilter, load_page
from ...git.patch import CommitDiff
from ..context import Scope, SelectionContext
from ..registry import Action, ActionEnv, ActionError

SCREENS = frozenset({"log", "history"})
HUNK = frozenset({Scope.HUNK})
COMMIT = frozenset({Scope.COMMIT})


def _commit_patch(ctx: SelectionContext) -> bool:
    return (
        ctx.focus is Scope.HUNK
        and ctx.patch is not None
        and isinstance(ctx.patch.source, CommitDiff)
        and ctx.operation is None
    )


def _sha(env: ActionEnv) -> str:
    return env.ctx.patch.source.sha  # type: ignore[union-attr]


async def _report(env: ActionEnv, coro, label: str) -> None:  # type: ignore[no-untyped-def]
    try:
        res = await coro
    except PB.PatchBuilderError as e:
        raise ActionError(str(e)) from e
    if env.entry:
        env.entry.op = label
    if res.status == "done":
        env.ui.notify(label)
    else:
        env.ui.notify(
            f"{label}: stopped with conflicts — resolve them, then Continue", severity="warning"
        )


async def _remove(env: ActionEnv) -> None:
    sha = _sha(env)
    await _report(
        env,
        PB.remove_from_commit(env.session, sha, env.ctx.patch.selection),
        f"Removed selected changes from {sha[:8]}",
    )


async def _move_new(env: ActionEnv) -> None:
    sha = _sha(env)
    msg = await env.ui.prompt(
        "Message for the new commit", multiline=True, default="Split from " + sha[:8]
    )
    if not msg:
        return
    await _report(
        env,
        PB.move_to_new_commit(env.session, sha, env.ctx.patch.selection, msg),
        f"Moved selected changes out of {sha[:8]} into a new commit",
    )


async def _move_to(env: ActionEnv) -> None:
    sha = _sha(env)
    branch = env.session.active.branch_short
    if not branch:
        raise ActionError("HEAD is detached")
    commits = await load_page(env.session.git, LogFilter(refs=("HEAD",), first_parent=True), 0, 40)
    options = [(c.sha, f"{c.short}  {c.subject}") for c in commits if c.sha != sha]
    target = await env.ui.choose(f"Move the selected changes from {sha[:8]} into…", options)
    if target is None:
        return
    await _report(
        env,
        PB.move_to_commit(env.session, sha, env.ctx.patch.selection, target),
        f"Moved selected changes from {sha[:8]} into {target[:8]}",
    )


async def _move_index(env: ActionEnv) -> None:
    sha = _sha(env)
    await _report(
        env,
        PB.move_to_index(env.session, sha, env.ctx.patch.selection),
        f"Moved selected changes from {sha[:8]} to the index",
    )


async def _revert(env: ActionEnv) -> None:
    sha = _sha(env)
    try:
        await PB.revert_in_worktree(env.session.git, sha, env.ctx.patch.selection)
    except PB.PatchBuilderError as e:
        raise ActionError(str(e)) from e
    env.ui.notify(f"Reverted the selected changes of {sha[:8]} in the working tree")


# -- bisect ---------------------------------------------------------------------------------


async def _mark(env: ActionEnv, verdict: B.Verdict) -> None:
    git = env.session.git
    sha = env.ctx.commits[0].sha if env.ctx.commits else None
    res = await B.mark(git, verdict, sha)
    output = res.text + res.stderr
    first = B.first_bad_in(output)
    if first:
        env.ui.notify(f"Found it: {first[:10]} is the first bad commit")
        env.ui.open_screen("log", branch=first)
        return
    st = await B.state(git)
    if st.bad and st.good:
        env.ui.notify(
            f"Marked {verdict} — now testing {st.current[:8] if st.current else '?'}"
            + (f", ~{st.remaining} left" if st.remaining is not None else "")
        )
    else:
        missing = "good" if st.bad else "bad"
        env.ui.notify(f"Marked {verdict} — now mark a {missing} commit")


async def _bad(env: ActionEnv) -> None:
    await _mark(env, "bad")


async def _good(env: ActionEnv) -> None:
    await _mark(env, "good")


async def _skip(env: ActionEnv) -> None:
    await _mark(env, "skip")


async def _run(env: ActionEnv) -> None:
    cmd = await env.ui.prompt(
        "Bisect run: command that exits 0 for good commits",
        placeholder="pytest -x tests/test_parser.py",
    )
    if not cmd:
        return
    collected: list[str] = []

    async def stream() -> AsyncIterator[str]:
        async for line in B.run(env.session.git, cmd):
            collected.append(line)
            yield line

    await env.ui.show_output(f"git bisect run {cmd}", stream())
    first = B.first_bad_in("\n".join(collected))
    if first:
        env.ui.notify(f"First bad commit: {first[:10]}")


async def _reset(env: ActionEnv) -> None:
    await B.reset(env.session.git)
    env.ui.notify("Bisect finished — back where you started")


def actions() -> list[Action]:
    def p(id_, label, run, key):  # type: ignore[no-untyped-def]
        return Action(
            id_,
            label,
            run,
            scopes=HUNK,
            when=_commit_patch,
            key=key,
            group="Patch",
            screens=SCREENS,
            mutating=True,
        )

    bisecting = lambda c: c.operation is not None and c.operation.kind.value == "bisect"  # noqa: E731
    return [
        p("patch.remove", "Remove Selected Changes from Commit", _remove, "alt+r"),
        p("patch.move_new", "Move Selected Changes into a New Commit…", _move_new, "alt+n"),
        p("patch.move_to", "Move Selected Changes into Another Commit…", _move_to, "alt+m"),
        p("patch.move_index", "Move Selected Changes to the Index", _move_index, "alt+i"),
        Action(
            "patch.revert",
            "Revert Selected Changes in Working Tree",
            _revert,
            scopes=HUNK,
            when=_commit_patch,
            key="alt+v",
            group="Patch",
            screens=SCREENS,
            mutating=True,
            destructive=True,
            silent_backup=True,
            paths=lambda c: c.patch.paths if c.patch else None,
        ),
        Action(
            "bisect.bad",
            "Bisect: Mark Bad",
            _bad,
            scopes=COMMIT,
            key="alt+b",
            group="Bisect",
            screens=SCREENS,
            mutating=True,
            when=lambda c: c.operation is None or bisecting(c),
        ),
        Action(
            "bisect.good",
            "Bisect: Mark Good",
            _good,
            scopes=COMMIT,
            key="alt+g",
            group="Bisect",
            screens=SCREENS,
            mutating=True,
            when=lambda c: c.operation is None or bisecting(c),
        ),
        Action(
            "bisect.skip",
            "Bisect: Skip",
            _skip,
            scopes=COMMIT,
            key="alt+k",
            group="Bisect",
            screens=SCREENS,
            mutating=True,
            when=bisecting,
        ),
        Action(
            "bisect.run",
            "Bisect: Run a Command…",
            _run,
            group="Bisect",
            mutating=True,
            when=bisecting,
        ),
        Action(
            "bisect.reset",
            "Bisect: Finish (Reset)",
            _reset,
            group="Bisect",
            mutating=True,
            when=bisecting,
        ),
    ]
