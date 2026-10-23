"""The list window building block: a bar (controls · filter · status) over a table that
filters as you type and sorts by column.

Every table-like list in gitpanel uses this, so they all behave the same:
`/` filter · `escape` clear · `>` sort by the next column · `<` reverse · click a header
to sort by it (again to reverse). The flexible column takes the width the others leave,
the cursor stays on the same item across refreshes, and the sort order is remembered.

A screen describes its columns (`Col`) and how an item matches the filter; it sets items
with `set_items()` and reads the highlighted one from `selected`.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

from rich.console import RenderableType
from rich.text import Text
from textual import events, on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.widget import Widget
from textual.widgets import DataTable, Input, Static

from .splitter import load_state, save_state

T = TypeVar("T")


@dataclass(frozen=True)
class Col(Generic[T]):
    key: str
    label: str
    width: int | None  # None = flexible: takes the width the other columns leave
    cell: Callable[[T], RenderableType]
    sort: Callable[[T], Any] | None = None  # None = can't sort by this column
    descending_first: bool = False  # e.g. dates and numbers: newest first


def words_match(text: str, haystack: str) -> bool:
    """The default filter: every word of `text` appears in `haystack` (case-insensitive)."""
    hay = haystack.lower()
    return all(w in hay for w in text.lower().split())


class _Table(DataTable):
    """DataTable whose flexible column fills the remaining width."""

    flex_key: str | None = None
    fixed: int = 0  # total width of the fixed columns
    min_flex: int = 8

    def on_resize(self) -> None:
        self.fit()

    def fit(self) -> None:
        if self.flex_key is None or self.flex_key not in self.columns:
            return
        padding = 2 * self.cell_padding * len(self.columns)
        # leave room for the vertical scrollbar: it appears without a resize event
        width = self.size.width - self.styles.scrollbar_size_vertical
        column = self.columns[self.flex_key]  # type: ignore[index]
        column.auto_width = False
        column.width = max(width - self.fixed - padding, self.min_flex)
        # DataTable has no public "column width changed" hook; these are what add_column uses
        self._require_update_dimensions = True
        self._clear_caches()
        self.refresh()


class ListTable(Vertical, Generic[T]):
    """A filterable, sortable list with a bar on top. See the module docstring."""

    DEFAULT_CSS = """
    ListTable { height: 1fr; }
    ListTable > .lt-bar { height: 1; padding: 0 1; }
    ListTable > .lt-bar > * { margin-right: 1; }
    ListTable > .lt-bar > .lt-filter { width: 1fr; }
    ListTable > .lt-bar > .lt-status { width: auto; max-width: 60; color: $text-muted;
                                       margin-right: 0; }
    ListTable > .lt-message { height: auto; padding: 1 2; color: $text-muted; display: none; }
    ListTable > .lt-message.-shown { display: block; }
    ListTable > DataTable { height: 1fr; background: $surface; }
    """

    class Highlighted(Message):
        """The cursor moved to `item` (None: the list is empty)."""

        def __init__(self, list_table: ListTable[Any], item: Any) -> None:
            super().__init__()
            self.list_table = list_table
            self.item = item

        @property
        def control(self) -> ListTable[Any]:
            return self.list_table

    class FilterChanged(Message):
        def __init__(self, list_table: ListTable[Any], text: str) -> None:
            super().__init__()
            self.list_table = list_table
            self.text = text

        @property
        def control(self) -> ListTable[Any]:
            return self.list_table

    class FilterSubmitted(FilterChanged):
        """`enter` in the filter box (the table gets focus either way)."""

    def __init__(
        self,
        columns: Sequence[Col[T]],
        *,
        key: Callable[[T], str],
        matches: Callable[[T, str], bool] | None = None,
        placeholder: str = "Filter",
        controls: Sequence[Widget] = (),
        empty: Callable[[str], str] | None = None,
        state_key: str | None = None,
        table_id: str | None = None,
        zebra: bool = True,
        id: str | None = None,
    ) -> None:
        super().__init__(id=id)
        self.columns = list(columns)
        self._key = key
        self._matches = matches
        self._placeholder = placeholder
        self._controls = list(controls)
        self._empty = empty or (lambda text: f"Nothing matches “{text}”." if text else "")
        self._state_key = state_key
        self._table_id = table_id
        self._zebra = zebra
        self.items: list[T] = []
        self.shown: list[T] = []
        self.sort: tuple[str, bool] | None = None  # (column key, descending); None = as given
        self.note = ""  # shown after the count, e.g. "updated 08:37" or "loading…"
        self._message: str | None = None
        if state_key:
            saved = load_state().get(f"{state_key}.sort")
            if isinstance(saved, list) and len(saved) == 2:
                col, desc = saved
                if any(c.key == col and c.sort for c in self.columns):
                    self.sort = (str(col), bool(desc))

    # -- parts ------------------------------------------------------------------------------
    def compose(self) -> ComposeResult:
        with Horizontal(classes="lt-bar"):
            yield from self._controls
            yield Input(placeholder=self._placeholder, classes="lt-filter", compact=True)
            yield Static("", classes="lt-status")
        yield Static("", classes="lt-message")
        yield _Table(id=self._table_id, cursor_type="row", zebra_stripes=self._zebra)

    @property
    def table(self) -> DataTable:
        return self.query_one(_Table)

    @property
    def filter_input(self) -> Input:
        return self.query_one(".lt-filter", Input)

    @property
    def filter_text(self) -> str:
        return self.filter_input.value.strip()

    def on_mount(self) -> None:
        table = self.query_one(_Table)
        for c in self.columns:
            table.add_column(Text(c.label), width=c.width, key=c.key)
        flex = [c for c in self.columns if c.width is None]
        if flex:
            table.flex_key = flex[0].key
            table.fixed = sum(c.width or 0 for c in self.columns if c.width is not None)
        self._update_headers()
        table.fit()

    # -- content ---------------------------------------------------------------------------
    @property
    def selected(self) -> T | None:
        table = self.table
        row = table.cursor_row
        return self.shown[row] if self.shown and 0 <= row < len(self.shown) else None

    def set_items(self, items: Sequence[T], *, keep: str | None = None) -> None:
        """Replace the items; the cursor stays on `keep` (default: the current item)."""
        self.items = list(items)
        self.refresh_rows(keep=keep)

    def refresh_rows(self, *, keep: str | None = None) -> None:
        """Rebuild the rows (filter and sort again), e.g. after item data changed."""
        current = self.selected
        if keep is None and current is not None:
            keep = self._key(current)
        text = self.filter_text
        shown = [i for i in self.items if not text or self._match(i, text)]
        if self.sort is not None:
            col = next(c for c in self.columns if c.key == self.sort[0])
            assert col.sort is not None
            shown.sort(key=col.sort, reverse=self.sort[1])
        self.shown = shown
        table = self.table
        table.clear()
        for item in shown:
            table.add_row(*(c.cell(item) for c in self.columns), key=self._key(item))
        if shown:
            row = next((i for i, it in enumerate(shown) if self._key(it) == keep), 0)
            table.move_cursor(row=row)
        self._update_status()
        self.post_message(self.Highlighted(self, self.selected))

    def show_message(self, text: str | None) -> None:
        """A message in place of the rows (e.g. "not configured"); None = back to normal."""
        self._message = text
        self._update_status()

    def set_note(self, note: str) -> None:
        self.note = note
        self._update_status()

    def _match(self, item: T, text: str) -> bool:
        if self._matches is not None:
            return self._matches(item, text)
        return words_match(text, " ".join(str(c.cell(item)) for c in self.columns))

    def _update_status(self) -> None:
        text = self.filter_text
        count = f"{len(self.shown)} of {len(self.items)}" if text else str(len(self.items))
        status = " · ".join(x for x in (count if self.items else "", self.note) if x)
        self.query_one(".lt-status", Static).update(status)
        message = self._message
        if message is None and not self.shown:
            message = self._empty(text if self.items else "")
        widget = self.query_one(".lt-message", Static)
        widget.set_class(bool(message), "-shown")
        widget.update(message or "")

    # -- sorting -----------------------------------------------------------------------------
    def sort_by(self, column: str | None, descending: bool | None = None) -> None:
        """Sort by `column` (None: the order the items came in). Without `descending`, a new
        column starts in its natural direction and the current one flips."""
        if column is None:
            self.sort = None
        else:
            col = next(c for c in self.columns if c.key == column)
            if col.sort is None:
                return
            if descending is None:
                if self.sort is not None and self.sort[0] == column:
                    descending = not self.sort[1]
                else:
                    descending = col.descending_first
            self.sort = (column, descending)
        if self._state_key:
            save_state(f"{self._state_key}.sort", list(self.sort) if self.sort else None)
        self._update_headers()
        self.refresh_rows()

    def next_sort(self) -> None:
        """`>`: sort by the next sortable column; after the last, back to the original order."""
        keys = [c.key for c in self.columns if c.sort is not None]
        i = keys.index(self.sort[0]) + 1 if self.sort is not None else 0
        self.sort_by(keys[i] if i < len(keys) else None)
        label = next((c.label for c in self.columns if self.sort and c.key == self.sort[0]), "")
        self.app.notify(f"Sorted by {label}" if label else "Original order", timeout=2)

    def reverse_sort(self) -> None:
        """`<`: flip the direction of the current sort."""
        if self.sort is not None:
            self.sort_by(self.sort[0], not self.sort[1])

    def _update_headers(self) -> None:
        table = self.table
        for c in self.columns:
            arrow = ""
            if self.sort is not None and self.sort[0] == c.key:
                arrow = " ▼" if self.sort[1] else " ▲"
            table.columns[c.key].label = Text(c.label + arrow)  # type: ignore[index]
        table.refresh()

    @on(DataTable.HeaderSelected)
    def _header(self, event: DataTable.HeaderSelected) -> None:
        event.stop()
        self.sort_by(str(event.column_key.value))

    # -- events & keys -------------------------------------------------------------------------
    @on(DataTable.RowHighlighted)
    def _row(self, event: DataTable.RowHighlighted) -> None:
        event.stop()
        self.post_message(self.Highlighted(self, self.selected))

    @on(Input.Changed, ".lt-filter")
    def _filter_changed(self, event: Input.Changed) -> None:
        event.stop()
        self.refresh_rows()
        self.post_message(self.FilterChanged(self, self.filter_text))

    @on(Input.Submitted, ".lt-filter")
    def _filter_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        self.table.focus()
        self.post_message(self.FilterSubmitted(self, self.filter_text))

    def on_key(self, event: events.Key) -> None:
        focused = self.screen.focused
        on_table = focused is self.table
        if on_table and event.key == "slash":
            self.filter_input.focus()
        elif on_table and event.character == ">":
            self.next_sort()
        elif on_table and event.character == "<":
            self.reverse_sort()
        elif focused is self.filter_input and event.key == "escape":
            self.filter_input.value = ""
            self.table.focus()
        else:
            return
        event.stop()
        event.prevent_default()

    def hints(self) -> list[tuple[str, str]]:
        """Key hints for the screen's hint bar while the table has focus."""
        if self.screen.focused is self.table:
            return [("/", "filter"), ("> <", "sort / reverse")]
        return []
