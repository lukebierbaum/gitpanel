"""File history and selection history (pushed screen)."""

from __future__ import annotations

from typing import Any

from rich.text import Text
from textual import on, work
from textual.app import ComposeResult
from textual.widgets import Static

from ...actions.context import FileRef, Scope, SelectionContext
from ...actions.registry import Registry
from ...git import patch as P
from ...git.graph import GraphRow
from ...git.history import file_history, selection_history
from ..base import ToolScreen
from ..widgets.commit_table import CommitTable, LogRow
from ..widgets.diff_view import DiffView
from ..widgets.key_hints import KeyHints
from ..widgets.splitter import Splitter


class HistoryScreen(ToolScreen):
    DEFAULT_CSS = """
    HistoryScreen { layout: vertical; }
    HistoryScreen #hist-header { height: 1; padding: 0 1; background: $panel; }
    HistoryScreen CommitTable { height: 40%; }
    HistoryScreen DiffView { height: 1fr; }
    """

    def __init__(
        self, path: str, start: int | None = None, end: int | None = None, rev: str | None = None
    ) -> None:
        super().__init__()
        self.path, self.start, self.end, self.rev = path, start, end, rev
        self.paths: dict[str, str] = {}  # sha → path at that revision

    def compose(self) -> ComposeResult:
        yield Static("", id="hist-header")
        yield CommitTable(id="hist-table")
        yield Splitter("history.table", "y", minimum=4)
        yield DiffView(id="hist-diff")
        yield KeyHints(id="hist-hints")

    def on_mount(self) -> None:
        self.query_one(CommitTable).focus()
        self.load()

    @work(exclusive=True, group="history-load")
    async def load(self) -> None:
        git = self.git
        title = Text()
        if self.start is not None and self.end is not None:
            commits = await selection_history(git, self.path, self.start, self.end)
            self.paths = {c.sha: self.path for c in commits}
            title.append(f"History of {self.path}:{self.start}-{self.end}", style="bold")
        else:
            revs = await file_history(git, self.path)
            commits = [r.commit for r in revs]
            self.paths = {r.commit.sha: r.path for r in revs}
            title.append(f"History of {self.path}", style="bold")
        title.append(
            f"   {len(commits)} revision(s) · alt+a annotate · G get · C cherry-pick · esc close",
            style="dim",
        )
        self.query_one("#hist-header", Static).update(title)
        rows = [LogRow(c, GraphRow(c.sha, 0, (("●", 0),))) for c in commits]
        self.query_one(CommitTable).set_rows(rows, has_more=False)
        self.show_diff()

    @on(CommitTable.CursorChanged)
    def _cursor(self) -> None:
        self.show_diff()
        self.gp.refresh_hints()

    @work(exclusive=True, group="history-diff")
    async def show_diff(self) -> None:
        table = self.query_one(CommitTable)
        view = self.query_one(DiffView)
        rows = table.selected_rows()
        if len(rows) != 1:
            view.set_diff(None, None)
            return
        sha = rows[0].commit.sha
        path = self.paths.get(sha, self.path)
        git = self.git
        diffs = await P.load_diff(git, P.CommitDiff(sha), [path])
        diff = next((d for d in diffs if d.path == path), diffs[0] if diffs else None)
        view.set_diff(P.CommitDiff(sha), diff, message="No changes to this file")

    def context(self, base: SelectionContext) -> SelectionContext:
        table = self.query(CommitTable)
        if not table:
            return base.with_(screen="history")
        t = table.first()
        commits = tuple(t.selected_commits())
        kwargs: dict[str, Any] = {"screen": "history", "commits": commits}
        focused = self.focused
        if commits and len(commits) == 1:
            sha = commits[0].sha
            ref = FileRef(self.paths.get(sha, self.path), P.CommitDiff(sha), "commit")
            kwargs["files"] = (ref,)
            if focused is t:
                # COMMIT for cherry-pick; file actions (annotate/get) match on files too
                kwargs["focus"] = Scope.COMMIT
        if isinstance(focused, DiffView) and focused.diff is not None and focused.source:
            sel = focused.selection()
            if sel is not None:
                from ...actions.context import PatchSelection

                kwargs.update(
                    focus=Scope.HUNK, patch=PatchSelection(focused.source, (focused.diff,), sel)
                )
        return base.with_(**kwargs)

    def show_hints(self, registry: Registry, ctx: SelectionContext) -> None:
        hints = self.query(KeyHints)
        if hints:
            file_ctx = ctx.with_(focus=Scope.FILE)
            items = registry.available(ctx) + [
                a for a in registry.available(file_ctx) if a.id.startswith("file.")
            ]
            t = Text(no_wrap=True, overflow="ellipsis")
            for i, a in enumerate(dict.fromkeys(items)):
                key = registry.key_for(a)
                if key and a.screens is not None:
                    t.append(("  " if i else "") + key, style="bold")
                    t.append(f" {a.label}")
            hints.first().update(t)
