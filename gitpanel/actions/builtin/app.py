"""App-wide actions: refresh, quit, undo/redo, in-progress operation control."""

from __future__ import annotations

from ...git.operations import OpKind
from ...git.session import ChangeKind
from ..context import SelectionContext
from ..registry import Action, ActionEnv


async def _refresh(env: ActionEnv) -> None:
    env.session.emit(ChangeKind.ALL)


async def _quit(env: ActionEnv) -> None:
    env.ui.post("app.quit")


def _undo_label(ctx: SelectionContext) -> str:
    e = ctx.session.journal.can_undo()
    return f"Undo “{e.op}”" if e else "Undo"


def _redo_label(ctx: SelectionContext) -> str:
    e = ctx.session.journal.can_redo()
    return f"Redo “{e.op}”" if e else "Redo"


async def _journal(env: ActionEnv) -> None:
    env.ui.open_screen("journal")


async def _undo_to(env: ActionEnv) -> None:
    entry_id = env.ctx.form["journal_entry"]
    journal = env.session.journal
    undone = next((e.undone for e in journal.entries() if e.id == entry_id), False)
    done = await (journal.redo_to(entry_id) if undone else journal.undo_to(entry_id))
    verb = "Redid" if undone else "Undid"
    env.ui.notify(f"{verb} {len(done)} operation{'s' if len(done) != 1 else ''}")
    env.ui.post("journal.refresh")


async def _undo(env: ActionEnv) -> None:
    entry = await env.session.journal.undo()
    env.ui.notify(f"Undid: {entry.op}")


async def _redo(env: ActionEnv) -> None:
    entry = await env.session.journal.redo()
    env.ui.notify(f"Redid: {entry.op}")


_OP_COMMAND = {
    OpKind.REBASE: "rebase",
    OpKind.MERGE: "merge",
    OpKind.CHERRY_PICK: "cherry-pick",
    OpKind.REVERT: "revert",
    OpKind.AM: "am",
}


def _op(ctx: SelectionContext, *kinds: OpKind) -> bool:
    return ctx.operation is not None and ctx.operation.kind in kinds


async def _continue(env: ActionEnv) -> None:
    op = env.ctx.operation
    assert op is not None
    if op.kind is OpKind.REBASE:
        # resume() keeps gitpanel's staged reword/squash messages for later commits
        from ...git import scripted_rebase as SR

        res = await SR.resume(env.session)
        if res.status == "conflict":
            env.ui.notify("Rebase stopped with conflicts again", severity="warning")
        elif res.status == "stopped":
            env.ui.notify("Rebase paused for editing — amend, then Continue", severity="warning")
        else:
            env.ui.notify("Rebase finished")
        return
    await env.session.git.run(_OP_COMMAND[op.kind], "--continue")
    env.ui.notify(f"{op.kind} continued")


async def _skip(env: ActionEnv) -> None:
    op = env.ctx.operation
    assert op is not None
    await env.session.git.run(_OP_COMMAND[op.kind], "--skip")


async def _abort(env: ActionEnv) -> None:
    op = env.ctx.operation
    assert op is not None
    if op.kind is OpKind.BISECT:
        await env.session.git.run("bisect", "reset")
    else:
        await env.session.git.run(_OP_COMMAND[op.kind], "--abort")
    env.ui.notify(f"{op.kind} aborted")


async def _branches(env: ActionEnv) -> None:
    env.ui.open_screen("branches")


async def _worktrees(env: ActionEnv) -> None:
    env.ui.open_screen("worktrees")


async def _shelf(env: ActionEnv) -> None:
    env.ui.open_screen("shelf")


async def _context_menu(env: ActionEnv) -> None:
    env.ui.post("context_menu")


def actions() -> list[Action]:
    resumable = (OpKind.REBASE, OpKind.MERGE, OpKind.CHERRY_PICK, OpKind.REVERT, OpKind.AM)
    return [
        Action("app.refresh", "Refresh", _refresh, key="ctrl+r", group="App"),
        Action("app.quit", "Quit", _quit, key="q", group="App"),
        Action("app.branches", "Branches…", _branches, key="b", group="Tool windows"),
        Action("app.show_worktrees", "Worktrees", _worktrees, key="alt+8", group="Tool windows"),
        Action("app.show_shelf", "Shelf and Stashes", _shelf, key="alt+5", group="Tool windows"),
        Action(
            "app.worktrees",
            "Worktrees",
            _worktrees,
            key="w",
            group="Tool windows",
            show_in_palette=False,
        ),
        Action(
            "app.context_menu",
            "Actions…",
            _context_menu,
            key="alt+enter",
            group="App",
            show_in_palette=False,
        ),
        Action(
            "app.undo",
            "Undo last operation",
            _undo,
            when=lambda ctx: ctx.session.journal.can_undo() is not None,
            key="z",
            group="App",
            label_fn=_undo_label,
        ),
        Action(
            "app.redo",
            "Redo",
            _redo,
            when=lambda ctx: ctx.session.journal.can_redo() is not None,
            key="Z",
            group="App",
            label_fn=_redo_label,
        ),
        Action("app.journal", "Operation History…", _journal, key="alt+z", group="App"),
        Action(
            "journal.undo_to",
            "Undo / Redo to Here",
            _undo_to,
            when=lambda ctx: ctx.form.get("journal_entry") is not None,
            key="enter",
            group="Journal",
            screens=frozenset({"journal"}),
        ),
        Action(
            "op.continue",
            "Continue",
            _continue,
            when=lambda ctx: _op(ctx, *resumable) and ctx.operation.conflicts == 0,  # type: ignore[union-attr]
            group="Operation",
            mutating=True,
        ),
        Action(
            "op.skip",
            "Skip commit",
            _skip,
            when=lambda ctx: _op(ctx, OpKind.REBASE, OpKind.CHERRY_PICK, OpKind.REVERT, OpKind.AM),
            group="Operation",
            mutating=True,
        ),
        Action(
            "op.abort",
            "Abort",
            _abort,
            when=lambda ctx: ctx.operation is not None,
            group="Operation",
            mutating=True,
            confirm="Abort the {op} and return to the state before it started?",
        ),
    ]
