"""Compare window: commits only in one branch or the other, with already-picked marks."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from rich.text import Text
from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Label, Static

from ...actions.context import Scope, SelectionContext
from ...git import patch as P
from ...git.cherry_pick import compare
from ...git.graph import GraphRow
from ...git.markers import Marker
from ..colors import C
from ..widgets.commit_table import CommitTable, LogRow, fmt_date
from ..widgets.diff_view import DiffView
from ..widgets.file_list import FileList
from ..widgets.splitter import Splitter

if TYPE_CHECKING:
    from ..app import GitPanelApp

PICKED = Marker("picked", "✓ picked", "picked", dims_row=True, order=90)


class CompareView(Vertical):
    name_id = "compare"
    stale = False

    DEFAULT_CSS = """
    CompareView { height: 1fr; }
    CompareView #cmp-header { height: 1; padding: 0 1; background: $panel; }
    CompareView #cmp-lists { height: 50%; }
    CompareView #cmp-lists > Vertical { width: 1fr; }
    CompareView #cmp-lists Label { padding: 0 1; color: $accent; text-style: bold; }
    CompareView CommitTable { height: 1fr; }
    CompareView #cmp-bottom { height: 1fr; }
    CompareView #cmp-left-list { width: 50%; }
    CompareView #cmp-details { width: 40%; }
    CompareView #cmp-details-scroll { height: auto; max-height: 50%; padding: 0 1; }
    CompareView #cmp-files { height: 1fr; border-top: tall $panel; }
    CompareView DiffView { height: 1fr; }
    """
    BINDINGS = [
        Binding("x", "swap", "Swap sides", show=False),
        Binding("h", "toggle_hide", "Hide picked", show=False),
    ]

    def __init__(self) -> None:
        super().__init__(id="compare-view")
        self.a = "HEAD"
        self.b: str | None = None
        self.hide_picked = False
        self.picked: set[str] = set()

    @property
    def gp(self) -> GitPanelApp:
        return self.app  # type: ignore[return-value]

    def compose(self) -> ComposeResult:
        yield Static(
            "Open from the Branches popup (b → ctrl+e) to compare a branch with HEAD",
            id="cmp-header",
        )
        with Horizontal(id="cmp-lists"):
            with Vertical(id="cmp-left-list"):
                yield Label("", id="cmp-b-label")
                yield CommitTable(id="cmp-b")
            yield Splitter("compare.sides", "x", minimum=20)
            with Vertical():
                yield Label("", id="cmp-a-label")
                yield CommitTable(id="cmp-a")
        yield Splitter("compare.lists", "y", minimum=4)
        with Horizontal(id="cmp-bottom"):
            with Vertical(id="cmp-details"):
                with VerticalScroll(id="cmp-details-scroll"):
                    yield Static("", id="cmp-details-text")
                yield FileList(id="cmp-files")
            yield Splitter("compare.details", "x", minimum=20)
            yield DiffView(id="cmp-diff")

    def focus_default(self) -> None:
        self.query_one("#cmp-b", CommitTable).focus()

    def handle_event(self, event: str, data: dict[str, Any]) -> None:
        if event == "open" and "compare" in data:
            self.a, self.b = "HEAD", str(data["compare"])
            self.reload()

    def action_swap(self) -> None:
        if self.b is not None:
            self.a, self.b = self.b, self.a
            self.reload()

    def action_toggle_hide(self) -> None:
        self.hide_picked = not self.hide_picked
        self.reload()

    @work(exclusive=True, group="compare-load")
    async def reload(self) -> None:
        session = self.gp.session
        if session is None or self.b is None:
            return
        git = session.git
        result = await compare(git, self.a, self.b)
        current = session.active.branch_short or "HEAD"
        a_name = current if self.a == "HEAD" else self.a
        b_name = current if self.b == "HEAD" else self.b
        self.picked = {c.sha for c, p in result.only_a + result.only_b if p}
        n_picked = sum(1 for _, p in result.only_b if p)
        header = Text()
        header.append(f"{a_name}", style=f"bold {C.green}")
        header.append("  ↔  ")
        header.append(f"{b_name}", style=f"bold {C.purple}")
        header.append(
            f"   {len(result.only_b)} only in {b_name} ({n_picked} already picked) · "
            f"{len(result.only_a)} only in {a_name}",
            style="dim",
        )
        header.append("   x swap · h hide picked · C pick · ctrl+alt+c pick to…", style="dim")
        self.query_one("#cmp-header", Static).update(header)
        self.query_one("#cmp-b-label", Label).update(f"In {b_name}, not in {a_name}")
        self.query_one("#cmp-a-label", Label).update(f"In {a_name}, not in {b_name}")
        for table_id, rows in (("#cmp-b", result.only_b), ("#cmp-a", result.only_a)):
            shown = [(c, p) for c, p in rows if not (self.hide_picked and p)]
            table = self.query_one(table_id, CommitTable)
            table.set_rows(
                [
                    LogRow(
                        c,
                        GraphRow(c.sha, 0, (("●", 1 if table_id == "#cmp-b" else 0),)),
                        [PICKED] if p else [],
                    )
                    for c, p in shown
                ],
                has_more=False,
            )
        self.show_details()

    def _focused_table(self) -> CommitTable:
        focused = self.screen.focused
        if isinstance(focused, CommitTable):
            return focused
        return self.query_one("#cmp-b", CommitTable)

    @on(CommitTable.CursorChanged)
    def _cursor(self) -> None:
        self.show_details()
        self.gp.refresh_hints()

    @work(exclusive=True, group="compare-details")
    async def show_details(self) -> None:
        session = self.gp.session
        table = self._focused_table()
        rows = table.selected_rows()
        text = self.query_one("#cmp-details-text", Static)
        view = self.query_one(DiffView)
        files = self.query_one("#cmp-files", FileList)
        if session is None or len(rows) != 1:
            text.update(f"{len(rows)} commits selected" if rows else "")
            files.set_files(None, [])
            view.set_diff(None, None)
            return
        c = rows[0].commit
        t = Text()
        t.append(c.subject + "\n", style="bold")
        t.append(f"{c.short}  {c.author_name}  {fmt_date(c.author_date)}\n", style="dim")
        if c.sha in self.picked:
            t.append("✓ already on the other side (same change)\n", style=C.green)
        diffs = await P.load_diff(session.git, P.CommitDiff(c.sha))
        text.update(t)
        files.set_files(P.CommitDiff(c.sha), diffs)
        if not diffs:
            view.set_diff(None, None, message="No file changes")

    @on(FileList.Selected, "#cmp-files")
    def _file(self, event: FileList.Selected) -> None:
        if event.diff is not None:
            self.query_one(DiffView).set_diff(event.source, event.diff)

    def context(self, base: SelectionContext) -> SelectionContext:
        table = self._focused_table()
        kwargs: dict[str, Any] = {
            "screen": "compare",
            "commits": tuple(table.selected_commits()),
            "form": {"picked": tuple(self.picked)},
        }
        if self.screen.focused is table and table.rows:
            kwargs["focus"] = Scope.COMMIT
        return SelectionContext(**{**base.__dict__, **kwargs})

    def hint_extras(self) -> list[tuple[str, str]]:
        return [("x", "swap"), ("h", "hide picked"), ("tab", "other side")]
