"""Operation history: everything gitpanel did, undoable step by step."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import DataTable, Static

from ...actions.context import SelectionContext
from ...actions.registry import Registry
from ...git.journal import JournalEntry
from ..widgets.key_hints import KeyHints

if TYPE_CHECKING:
    from ..app import GitPanelApp

GREEN = "#6CCB3A"
PURPLE = "#A66BE0"


def describe_refs(e: JournalEntry) -> Text:
    t = Text()
    for i, (ref, after) in enumerate(e.refs_after.items()):
        before = e.refs_before.get(ref)
        name = ref.removeprefix("refs/heads/").removeprefix("refs/tags/tag:")
        name = name.removeprefix("refs/remotes/")
        if i:
            t.append("  ")
        if before is None:
            t.append(f"+{name}", style=GREEN)
        elif after is None:
            t.append(f"-{name}", style="#E5484D")
        else:
            t.append(f"{name} ", style="bold")
            t.append(f"{before[:7]}→{after[:7]}", style="dim")
    if e.head_before.ref != e.head_after.ref:
        t.append("  HEAD ", style="bold")
        t.append(
            f"{(e.head_before.ref or 'detached').removeprefix('refs/heads/')}→"
            f"{(e.head_after.ref or 'detached').removeprefix('refs/heads/')}",
            style="dim",
        )
    return t


class JournalScreen(Screen[None]):
    DEFAULT_CSS = """
    JournalScreen { layout: vertical; }
    JournalScreen #jr-header { height: 1; padding: 0 1; background: $panel; }
    JournalScreen DataTable { height: 1fr; background: $surface; }
    """
    BINDINGS = [Binding("escape", "dismiss", "Close")]

    def __init__(self) -> None:
        super().__init__()
        self.entries: list[JournalEntry] = []

    @property
    def gp(self) -> GitPanelApp:
        return self.app  # type: ignore[return-value]

    def compose(self) -> ComposeResult:
        yield Static("", id="jr-header")
        yield DataTable(id="jr-table", cursor_type="row")
        yield KeyHints(id="jr-hints")

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        for label, width in (
            ("#", 5),
            ("When", 17),
            ("Operation", 44),
            ("State", 18),
            ("Changes", None),
        ):
            table.add_column(label, width=width)
        self.reload()
        table.focus()

    def reload(self) -> None:
        session = self.gp.session
        if session is None:
            return
        self.entries = list(reversed(session.journal.entries()))
        table = self.query_one(DataTable)
        prev = table.cursor_row
        table.clear()
        for e in self.entries:
            state = Text()
            if e.undone:
                state.append("undone ", style="italic dim")
            if e.failed:
                state.append("failed ", style="#E5484D")
            if e.backup:
                state.append("⛁ backup", style=PURPLE)
            op = Text(e.op, style="dim strike" if e.undone else "")
            table.add_row(
                str(e.id),
                e.when.astimezone().strftime("%m-%d %H:%M:%S"),
                op,
                state,
                describe_refs(e),
            )
        if self.entries:
            table.move_cursor(row=max(0, min(prev, len(self.entries) - 1)))
        header = Text()
        header.append("Operation history", style="bold")
        header.append(
            f"   {sum(not e.undone for e in self.entries)} undoable · "
            f"{sum(e.undone for e in self.entries)} redoable · enter: undo/redo down "
            "to the selected entry · esc close",
            style="dim",
        )
        self.query_one("#jr-header", Static).update(header)

    @on(DataTable.RowHighlighted)
    def _row(self) -> None:
        self.gp.refresh_hints()

    def handle_event(self, event: str, data: dict[str, Any]) -> None:
        if event == "journal.refresh":
            self.reload()

    def context(self, base: SelectionContext) -> SelectionContext:
        tables = self.query(DataTable)
        form: dict[str, Any] = {}
        if tables and self.entries:
            row = tables.first().cursor_row
            if 0 <= row < len(self.entries):
                form["journal_entry"] = self.entries[row].id
        return SelectionContext(**{**base.__dict__, "screen": "journal", "form": form})

    def show_hints(self, registry: Registry, ctx: SelectionContext) -> None:
        hints = self.query(KeyHints)
        if hints:
            hints.first().show_for(registry, ctx, [("esc", "close")], screen_only=True)
