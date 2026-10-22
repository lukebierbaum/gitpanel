"""File history, annotate (blame) and selection history."""

from __future__ import annotations

from ...git.patch import CommitDiff
from ..context import Scope, SelectionContext
from ..registry import Action, ActionEnv

FILE = frozenset({Scope.FILE})
FILE_SCREENS = frozenset({"commit", "log", "history", "compare"})
ANNOTATE = frozenset({"annotate"})


def _file(ctx: SelectionContext) -> bool:
    return len(ctx.files) == 1 and ctx.files[0].group != "untracked"


def _rev(ctx: SelectionContext) -> str | None:
    src = ctx.files[0].source
    return src.sha if isinstance(src, CommitDiff) else None


async def _history(env: ActionEnv) -> None:
    f = env.ctx.files[0]
    env.ui.open_screen("history", path=f.path)


async def _annotate(env: ActionEnv) -> None:
    f = env.ctx.files[0]
    env.ui.open_screen("annotate", path=f.path, rev=_rev(env.ctx))


def _line(ctx: SelectionContext) -> dict | None:  # type: ignore[type-arg]
    return ctx.form.get("blame_line")


async def _annotate_previous(env: ActionEnv) -> None:
    line = _line(env.ctx)
    assert line is not None
    if line["uncommitted"]:
        env.ui.notify("This line isn't committed yet", severity="warning")
        return
    env.ui.open_screen(
        "annotate", path=line["orig_path"], rev=f"{line['sha']}^", line=line["orig_line"]
    )


async def _show_commit(env: ActionEnv) -> None:
    line = _line(env.ctx)
    assert line is not None
    if line["uncommitted"]:
        env.ui.notify("This line isn't committed yet", severity="warning")
        return
    env.ui.post("popup.close")
    env.ui.open_screen("log", branch=line["sha"])


async def _selection_history(env: ActionEnv) -> None:
    form = env.ctx.form
    env.ui.open_screen(
        "history", path=form["path"], start=form["start"], end=form["end"], rev=form.get("rev")
    )


def actions() -> list[Action]:
    return [
        Action(
            "file.history",
            "Show History",
            _history,
            scopes=FILE,
            when=_file,
            key="alt+h",
            group="File",
            screens=FILE_SCREENS,
        ),
        Action(
            "file.annotate",
            "Annotate with Git Blame",
            _annotate,
            scopes=FILE,
            when=_file,
            key="alt+a",
            group="File",
            screens=FILE_SCREENS,
        ),
        Action(
            "annotate.previous",
            "Annotate Previous Revision",
            _annotate_previous,
            when=lambda c: _line(c) is not None,
            key="p",
            group="Annotate",
            screens=ANNOTATE,
        ),
        Action(
            "annotate.show_commit",
            "Show Commit in Log",
            _show_commit,
            when=lambda c: _line(c) is not None,
            key="enter",
            group="Annotate",
            screens=ANNOTATE,
        ),
        Action(
            "annotate.selection_history",
            "Show History for Selection",
            _selection_history,
            when=lambda c: c.form.get("start") is not None,
            key="alt+h",
            group="Annotate",
            screens=ANNOTATE,
        ),
    ]
