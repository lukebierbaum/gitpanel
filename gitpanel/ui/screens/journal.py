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
from ..widgets.list_table import Col, ListTable

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


def _state(e: JournalEntry) -> Text:
    state = Text()
    if e.undone:
        state.append("undone ", style="italic dim")
    if e.failed:
        state.append("failed ", style="#E5484D")
    if e.backup:
        state.append("⛁ backup", style=PURPLE)
    return state


class JournalScreen(Screen[None]):
    DEFAULT_CSS = """
    JournalScreen { layout: vertical; }
    JournalScreen #jr-header { height: 1; padding: 0 1; background: $panel; }
    """
    BINDINGS = [Binding("escape", "dismiss", "Close")]

    def __init__(self) -> None:
        super().__init__()

    @property
    def gp(self) -> GitPanelApp:
        return self.app  # type: ignore[return-value]

    def compose(self) -> ComposeResult:
        yield Static("", id="jr-header")
        yield ListTable[JournalEntry](
            [
                Col("id", "#", 5, lambda e: str(e.id), sort=lambda e: e.id, descending_first=True),
                Col(
                    "when",
                    "When",
                    17,
                    lambda e: e.when.astimezone().strftime("%m-%d %H:%M:%S"),
                    sort=lambda e: e.when,
                    descending_first=True,
                ),
                Col(
                    "op",
                    "Operation",
                    44,
                    lambda e: Text(e.op, style="dim strike" if e.undone else ""),
                    sort=lambda e: e.op.casefold(),
                ),
                Col("state", "State", 18, _state, sort=lambda e: str(_state(e))),
                Col("changes", "Changes", None, describe_refs),
            ],  # fmt: skip
            key=lambda e: str(e.id),
            placeholder="/ Filter operations (commit, rebase, undone, branch names …)",
            empty=lambda text: f"No operations match “{text}”." if text else "Nothing yet.",
            state_key="journal.list",
            table_id="jr-table",
            zebra=False,
        )
        yield KeyHints(id="jr-hints")

    @property
    def entries(self) -> list[JournalEntry]:
        return self.query_one(ListTable).items

    def on_mount(self) -> None:
        self.reload()
        self.query_one("#jr-table", DataTable).focus()

    def reload(self) -> None:
        session = self.gp.session
        if session is None:
            return
        entries = list(reversed(session.journal.entries()))
        self.query_one(ListTable).set_items(entries)
        header = Text()
        header.append("Operation history", style="bold")
        header.append(
            f"   {sum(not e.undone for e in entries)} undoable · "
            f"{sum(e.undone for e in entries)} redoable · enter: undo/redo down "
            "to the selected entry · esc close",
            style="dim",
        )
        self.query_one("#jr-header", Static).update(header)

    @on(ListTable.Highlighted)
    def _row(self) -> None:
        self.gp.refresh_hints()

    def handle_event(self, event: str, data: dict[str, Any]) -> None:
        if event == "journal.refresh":
            self.reload()

    def context(self, base: SelectionContext) -> SelectionContext:
        form: dict[str, Any] = {}
        lists = self.query(ListTable)
        selected = lists.first().selected if lists else None
        if selected is not None:
            form["journal_entry"] = selected.id
        return SelectionContext(**{**base.__dict__, "screen": "journal", "form": form})

    def show_hints(self, registry: Registry, ctx: SelectionContext) -> None:
        hints = self.query(KeyHints)
        if hints:
            extras = [*self.query_one(ListTable).hints(), ("esc", "close")]
            hints.first().show_for(registry, ctx, extras, screen_only=True)
