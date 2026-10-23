"""Row-cursor scroll views: a cursor row, an optional range (anchor … cursor), keys to
move/extend/clear it, shift-click, and a helper to paint highlights over a row.

DiffView, CommitTable and the annotate view build on this; each says how many rows it has
(`row_count`) and reacts to changes in `cursor_moved()` / `range_changed()`.
"""

from __future__ import annotations

from typing import ClassVar

from rich.segment import Segment
from rich.style import Style
from textual import events
from textual.actions import SkipAction
from textual.binding import Binding, BindingType
from textual.reactive import reactive
from textual.scroll_view import ScrollView
from textual.strip import Strip


def overlay(strip: Strip, style: Style | None) -> Strip:
    """`strip` with `style`'s background laid over every segment (keeping their colours)."""
    if style is None or style.bgcolor is None:
        return strip
    top = Style(bgcolor=style.bgcolor)
    return Strip(
        [Segment(s.text, (s.style or Style()) + top, s.control) for s in strip], strip.cell_length
    )


class CursorView(ScrollView, can_focus=True):
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("up,k", "move(-1)", "Up", show=False),
        Binding("down,j", "move(1)", "Down", show=False),
        Binding("pageup", "page(-1)", "Page up", show=False),
        Binding("pagedown", "page(1)", "Page down", show=False),
        Binding("home", "jump(0)", "Top", show=False),
        Binding("end", "jump(-1)", "Bottom", show=False),
        Binding("shift+up", "extend(-1)", "Extend selection up", show=False),
        Binding("shift+down", "extend(1)", "Extend selection down", show=False),
        Binding("escape", "clear_range", "Clear selection", show=False),
    ]
    # plain moves drop the range (lists), or keep extending it from the anchor (text views)
    MOVE_CLEARS_RANGE: ClassVar[bool] = False

    cursor: reactive[int] = reactive(0, always_update=True)

    def __init__(self, *, name: str | None = None, id: str | None = None) -> None:
        super().__init__(name=name, id=id)
        self.anchor: int | None = None

    # -- for subclasses ------------------------------------------------------------------
    @property
    def row_count(self) -> int:
        raise NotImplementedError

    def cursor_moved(self) -> None:
        """Called after the cursor moved (and was scrolled into view)."""

    def range_changed(self) -> None:
        """Called when the range was started, toggled or cleared without moving."""
        self.cursor_moved()

    # -- cursor & range --------------------------------------------------------------------
    def validate_cursor(self, value: int) -> int:
        return max(0, min(value, self.row_count - 1)) if self.row_count else 0

    def watch_cursor(self) -> None:
        self.scroll_cursor_into_view()
        self.refresh()
        self.cursor_moved()

    def scroll_cursor_into_view(self) -> None:
        h = self.scrollable_content_region.height or self.size.height
        y = self.scroll_offset.y
        if self.cursor < y:
            self.scroll_to(y=self.cursor, animate=False)
        elif h and self.cursor >= y + h:
            self.scroll_to(y=self.cursor - h + 1, animate=False)

    def selection_range(self) -> tuple[int, int] | None:
        """(first, last) row of the range, inclusive; None without one."""
        if self.anchor is None:
            return None
        lo, hi = sorted((self.anchor, self.cursor))
        return lo, hi

    def in_range(self, row: int) -> bool:
        r = self.selection_range()
        return r is not None and r[0] <= row <= r[1]

    def row_highlight(self, row: int, cursor: Style, selected: Style) -> Style | None:
        """The highlight for `row`: the cursor's, the range's, or none."""
        if row == self.cursor:
            return cursor
        return selected if self.in_range(row) else None

    # -- actions -----------------------------------------------------------------------------
    def _plain_move(self) -> None:
        if self.MOVE_CLEARS_RANGE:
            self.anchor = None

    def action_move(self, delta: int) -> None:
        self._plain_move()
        self.cursor += delta

    def action_page(self, direction: int) -> None:
        self._plain_move()
        self.cursor += direction * max(self.size.height - 1, 1)

    def action_jump(self, where: int) -> None:
        self._plain_move()
        self.cursor = 0 if where == 0 else self.row_count - 1

    def action_extend(self, delta: int) -> None:
        if self.anchor is None:
            self.anchor = self.cursor
        self.cursor += delta

    def action_toggle_range(self) -> None:
        self.anchor = None if self.anchor is not None else self.cursor
        self.refresh()
        self.range_changed()

    def action_clear_range(self) -> None:
        if self.anchor is None:
            raise SkipAction()  # let escape reach the screen (e.g. to close it)
        self.anchor = None
        self.refresh()
        self.range_changed()

    def on_click(self, event: events.Click) -> None:
        row = event.y + self.scroll_offset.y
        if 0 <= row < self.row_count:
            if event.shift:
                if self.anchor is None:
                    self.anchor = self.cursor
            else:
                self.anchor = None
            self.cursor = row
        self.focus()
