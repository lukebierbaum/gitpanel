"""Unified diff view with hunk and line selection (Line API, scales to large diffs)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from rich.segment import Segment
from rich.style import Style
from textual import events
from textual.actions import SkipAction
from textual.binding import Binding
from textual.geometry import Size
from textual.message import Message
from textual.reactive import reactive
from textual.scroll_view import ScrollView
from textual.strip import Strip

from ...git.patch import DiffSource, FileDiff, Selection
from ..colors import C
from ..fmt import plural

GUTTER = 12  # "12345 12345 "
SPLIT_GUTTER = 7  # "12345 +" on each side of a side-by-side row


@dataclass(frozen=True)
class Row:
    kind: Literal["info", "hunk", "line"]
    text: str
    hunk: int = -1
    line: int = -1
    marker: str = " "
    old_no: int | None = None
    new_no: int | None = None
    pair: Row | None = None  # right-hand side in side-by-side mode

    @property
    def changed(self) -> bool:
        if self.kind != "line":
            return False
        return self.marker in "+-" or (self.pair is not None and self.pair.marker in "+-")


def build_split_rows(diff: FileDiff | None, message: str = "") -> list[Row]:
    """Side-by-side rows: left = old side, `pair` = new side; change blocks are zipped."""
    unified = build_rows(diff, message)
    out: list[Row] = []
    i = 0
    blank = Row("line", "", marker="")
    while i < len(unified):
        r = unified[i]
        if r.kind != "line":
            out.append(r)
            i += 1
            continue
        if r.marker == " ":
            out.append(
                Row(
                    "line",
                    r.text,
                    r.hunk,
                    r.line,
                    " ",
                    r.old_no,
                    None,
                    Row("line", r.text, r.hunk, r.line, " ", None, r.new_no),
                )
            )
            i += 1
            continue
        if r.marker == "\\":
            i += 1
            continue
        removed: list[Row] = []
        added: list[Row] = []
        while i < len(unified) and unified[i].kind == "line" and unified[i].marker in "-+\\":
            u = unified[i]
            if u.marker == "-":
                removed.append(u)
            elif u.marker == "+":
                added.append(u)
            i += 1
        for k in range(max(len(removed), len(added))):
            left = removed[k] if k < len(removed) else None
            right = added[k] if k < len(added) else None
            base = left or Row("line", "", (right or blank).hunk, -1, "")
            out.append(
                Row(
                    "line",
                    base.text,
                    base.hunk,
                    base.line,
                    base.marker,
                    base.old_no,
                    None,
                    right or Row("line", "", base.hunk, -1, ""),
                )
            )
    return out


def build_rows(diff: FileDiff | None, message: str = "") -> list[Row]:
    if diff is None:
        return [Row("info", message)] if message else []
    rows: list[Row] = []
    if diff.binary:
        rows.append(Row("info", "Binary file — only whole-file operations are available"))
    elif not diff.hunks:
        if diff.old_mode != diff.new_mode and diff.old_mode and diff.new_mode:
            rows.append(Row("info", f"Mode change {diff.old_mode} → {diff.new_mode}"))
        elif diff.status == "R":
            rows.append(Row("info", f"Renamed from {diff.old_path}"))
        else:
            rows.append(Row("info", "No content changes"))
    for hi, h in enumerate(diff.hunks):
        rows.append(Row("hunk", h.header, hunk=hi))
        for li, ln in enumerate(h.lines):
            rows.append(
                Row(
                    "line",
                    ln.text.replace("\t", "    ").rstrip("\r"),
                    hunk=hi,
                    line=li,
                    marker=ln.kind,
                    old_no=ln.old_no,
                    new_no=ln.new_no,
                )
            )
    return rows


class DiffView(ScrollView, can_focus=True):
    """Shows one FileDiff. `space` acts on the hunk under the cursor, or on the
    lines of a range selection started with `v` / shift+arrows."""

    COMPONENT_CLASSES = {
        "diff-view--added",
        "diff-view--removed",
        "diff-view--hunk",
        "diff-view--info",
        "diff-view--gutter",
        "diff-view--cursor",
        "diff-view--selected",
        "diff-view--hunk-active",
        "diff-view--nonewline",
    }

    DEFAULT_CSS = """
    DiffView {
        background: $surface;
    }
    DiffView > .diff-view--added { color: $text-success; background: $success 12%; }
    DiffView > .diff-view--removed { color: $text-error; background: $error 12%; }
    DiffView > .diff-view--hunk { color: $text-accent; text-style: bold; }
    DiffView > .diff-view--info { color: $text-muted; text-style: italic; }
    DiffView > .diff-view--gutter { color: $text-muted; }
    DiffView > .diff-view--nonewline { color: $text-muted; text-style: italic; }
    DiffView > .diff-view--cursor { background: $primary 30%; }
    DiffView:focus > .diff-view--cursor { background: $primary 55%; }
    DiffView > .diff-view--selected { background: $accent 35%; }
    DiffView > .diff-view--hunk-active { background: $foreground 6%; }
    """

    BINDINGS = [
        Binding("up,k", "cursor(-1)", "Up", show=False),
        Binding("down,j", "cursor(1)", "Down", show=False),
        Binding("pageup", "page(-1)", "Page up", show=False),
        Binding("pagedown", "page(1)", "Page down", show=False),
        Binding("home", "jump(0)", "Top", show=False),
        Binding("end", "jump(-1)", "Bottom", show=False),
        Binding("shift+up", "extend(-1)", "Extend selection up", show=False),
        Binding("shift+down", "extend(1)", "Extend selection down", show=False),
        Binding("v", "toggle_range", "Select lines", show=False),
        Binding("escape", "clear_range", "Clear selection", show=False),
        Binding("n,f7", "next_hunk(1)", "Next change", show=False),
        Binding("p,shift+f7", "next_hunk(-1)", "Previous change", show=False),
        Binding("s", "toggle_split", "Side-by-side", show=False),
    ]

    cursor: reactive[int] = reactive(0, always_update=True)

    class SelectionChanged(Message):
        def __init__(self, view: DiffView) -> None:
            super().__init__()
            self.view = view

        @property
        def control(self) -> DiffView:
            return self.view

    def __init__(self, *, name: str | None = None, id: str | None = None) -> None:
        super().__init__(name=name, id=id)
        self.source: DiffSource | None = None
        self.diff: FileDiff | None = None
        self.rows: list[Row] = []
        self.anchor: int | None = None
        self.split = False
        self._message = ""
        # (side, line) → mark shown in the gutter, e.g. review comments ("LEFT" = old side)
        self.line_marks: dict[tuple[str, int], str] = {}
        self._max_width = 0
        self._split_text = 0  # longest line on either side in side-by-side mode

    # -- content -------------------------------------------------------------
    def set_diff(
        self,
        source: DiffSource | None,
        diff: FileDiff | None,
        *,
        message: str = "",
        keep_position: bool = False,
    ) -> None:
        same_file = (
            keep_position
            and self.diff is not None
            and diff is not None
            and self.diff.path == diff.path
        )
        old_cursor = self.cursor
        self.source = source
        self.diff = diff
        self._message = message
        self._rebuild()
        if same_file:
            self.cursor = min(old_cursor, max(len(self.rows) - 1, 0))
        else:
            self.cursor = self._first_change()
            self.scroll_to(0, 0, animate=False)
        self.refresh()
        self.post_message(self.SelectionChanged(self))

    def _rebuild(self) -> None:
        if self.split:
            self.rows = build_split_rows(self.diff, self._message)
            self._max_width = max((len(r.text) for r in self.rows if r.kind != "line"), default=0)
            self._split_text = max(
                (
                    len(side.text)
                    for r in self.rows
                    if r.kind == "line"
                    for side in (r, r.pair)
                    if side is not None
                ),
                default=0,
            )
        else:
            self.rows = build_rows(self.diff, self._message)
            self._max_width = max((GUTTER + 1 + len(r.text) for r in self.rows), default=0)
        self.anchor = None
        self._update_virtual_size()

    def _split_halves(self, width: int) -> tuple[int, int]:
        """Widths of the left and right side of a side-by-side row."""
        half = max((width - 1) // 2, 8)
        return half, max(width - half - 1, 0)

    def _update_virtual_size(self) -> None:
        width = self.scrollable_content_region.width or self.size.width
        if self.split:
            # both sides scroll together, far enough to read the longest line on either
            room = min(self._split_halves(width)) - SPLIT_GUTTER
            needed = width + max(self._split_text - room, 0) if width else 0
            self.virtual_size = Size(max(needed, self._max_width), len(self.rows))
        else:
            self.virtual_size = Size(self._max_width, len(self.rows))

    def on_resize(self) -> None:
        self._update_virtual_size()

    def action_toggle_split(self) -> None:
        current = self.rows[self.cursor] if self.rows else None
        self.split = not self.split
        self._rebuild()
        target = 0
        if current is not None:
            for i, r in enumerate(self.rows):
                if r.hunk == current.hunk and (
                    r.line == current.line or (r.pair is not None and r.pair.line == current.line)
                ):
                    target = i
                    break
        self.cursor = target
        self.refresh()

    def _first_change(self) -> int:
        for i, r in enumerate(self.rows):
            if r.changed:
                return i
        return 0

    # -- selection -------------------------------------------------------------
    @property
    def has_range(self) -> bool:
        return self.anchor is not None

    def _range(self) -> tuple[int, int]:
        assert self.anchor is not None
        return min(self.anchor, self.cursor), max(self.anchor, self.cursor)

    def selection(self) -> Selection | None:
        """Lines of the active range, else the hunk under the cursor."""
        diff = self.diff
        if diff is None or not self.rows:
            return None
        if not diff.partial_allowed:
            return Selection.files([diff.path])
        if self.anchor is not None:
            lo, hi = self._range()
            pairs = [
                (r.hunk, r.line)
                for r in self.rows[lo : hi + 1]
                if r.kind == "line" and r.marker in "+-"
            ]
            if not pairs:
                return None
            sel = Selection()
            sel.add_lines(diff.path, pairs)
            return sel
        row = self.rows[min(self.cursor, len(self.rows) - 1)]
        if row.hunk < 0:
            return None
        return Selection.hunks(diff, [row.hunk])

    def describe_selection(self) -> str:
        if self.diff is None:
            return ""
        if not self.diff.partial_allowed:
            return "file"
        if self.anchor is not None:
            sel = self.selection()
            n = len(sel.lines.get(self.diff.path, ())) if sel else 0
            return plural(n, "line")
        return "hunk"

    # -- actions ----------------------------------------------------------------
    def watch_cursor(self, old: int, new: int) -> None:
        self._scroll_cursor_into_view()
        self.refresh()
        self.post_message(self.SelectionChanged(self))

    def validate_cursor(self, value: int) -> int:
        return max(0, min(value, len(self.rows) - 1)) if self.rows else 0

    def _scroll_cursor_into_view(self) -> None:
        h = self.scrollable_content_region.height or self.size.height
        y = self.scroll_offset.y
        if self.cursor < y:
            self.scroll_to(y=self.cursor, animate=False)
        elif h and self.cursor >= y + h:
            self.scroll_to(y=self.cursor - h + 1, animate=False)

    def action_cursor(self, delta: int) -> None:
        self.cursor += delta

    def action_page(self, direction: int) -> None:
        self.cursor += direction * max(self.size.height - 1, 1)

    def action_jump(self, where: int) -> None:
        self.cursor = 0 if where == 0 else len(self.rows) - 1

    def action_extend(self, delta: int) -> None:
        if self.split:
            self.cursor += delta
            return
        if self.anchor is None:
            self.anchor = self.cursor
        self.cursor += delta

    def action_toggle_range(self) -> None:
        if self.split:
            self.notify("Line selection works in the unified view (press s)", severity="warning")
            return
        self.anchor = None if self.anchor is not None else self.cursor
        self.refresh()
        self.post_message(self.SelectionChanged(self))

    def action_clear_range(self) -> None:
        if self.anchor is None:
            raise SkipAction()
        if self.anchor is not None:
            self.anchor = None
            self.refresh()
            self.post_message(self.SelectionChanged(self))

    def action_next_hunk(self, direction: int) -> None:
        """Jump to the next/previous block of changed lines."""
        i = self.cursor
        rows = self.rows

        def is_change(k: int) -> bool:
            return rows[k].changed

        # leave the current block first
        while 0 <= i < len(rows) and is_change(i):
            i += direction
        while 0 <= i < len(rows) and not is_change(i):
            i += direction
        if 0 <= i < len(rows):
            if direction < 0:
                while i > 0 and is_change(i - 1):
                    i -= 1
            self.cursor = i

    def on_click(self, event: events.Click) -> None:
        row = event.y + self.scroll_offset.y
        if 0 <= row < len(self.rows):
            if event.shift:
                if self.anchor is None:
                    self.anchor = self.cursor
            else:
                self.anchor = None
            self.cursor = row
        self.focus()

    # -- rendering ------------------------------------------------------------------
    def render_line(self, y: int) -> Strip:
        scroll_x, scroll_y = self.scroll_offset
        idx = y + scroll_y
        width = self.scrollable_content_region.width or self.size.width
        base = self.rich_style
        if idx >= len(self.rows):
            return Strip.blank(width, base)
        row = self.rows[idx]
        if self.split and row.kind == "line":
            return self._render_split(row, idx, width, base)
        style = self.get_component_rich_style
        gutter_style = style("diff-view--gutter")
        if row.kind == "info":
            text_style = style("diff-view--info")
        elif row.kind == "hunk":
            text_style = style("diff-view--hunk")
        elif row.marker == "+":
            text_style = style("diff-view--added")
        elif row.marker == "-":
            text_style = style("diff-view--removed")
        elif row.marker == "\\":
            text_style = style("diff-view--nonewline")
        else:
            text_style = Style()

        mark_style: Style | None = None
        if row.kind == "line":
            old = "" if row.old_no is None else str(row.old_no)
            new = "" if row.new_no is None else str(row.new_no)
            gutter = f"{old:>5} {new:>5} "
            mark = self._mark_for(row)
            if mark:
                gutter = mark + gutter[1:]
                mark_style = Style(color=C.amber, bold=True)
            body = f"{row.marker}{row.text}" if row.marker != "\\" else f"\\{row.text}"
        else:
            gutter = " " * GUTTER
            body = row.text

        overlay: Style | None = None
        if self.anchor is not None:
            lo, hi = self._range()
            if lo <= idx <= hi:
                overlay = style("diff-view--selected")
        elif row.hunk >= 0 and self.cursor < len(self.rows):
            if self.rows[self.cursor].hunk == row.hunk and self.has_focus:
                overlay = style("diff-view--hunk-active")
        if idx == self.cursor:
            overlay = style("diff-view--cursor")

        segments = [
            *(
                [Segment(gutter[0], base + mark_style), Segment(gutter[1:], base + gutter_style)]
                if mark_style is not None
                else [Segment(gutter, base + gutter_style)]
            ),
            Segment(body, base + text_style),
        ]
        strip = Strip(segments)
        line_style = base + text_style if row.marker in "+-" and row.kind == "line" else base
        strip = strip.crop_extend(scroll_x, scroll_x + width, line_style)
        if overlay is not None and overlay.bgcolor is not None:
            # layer the highlight on top of each segment's own background
            top = Style(bgcolor=overlay.bgcolor)
            strip = Strip(
                [Segment(seg.text, (seg.style or Style()) + top, seg.control) for seg in strip],
                strip.cell_length,
            )
        return strip

    @staticmethod
    def line_ref(row: Row) -> tuple[str, int] | None:
        """(side, line) of a diff row the way review comments address it."""
        if row.kind != "line":
            return None
        if row.marker == "-" and row.old_no is not None:
            return ("LEFT", row.old_no)
        if row.new_no is not None and row.marker in " +":
            return ("RIGHT", row.new_no)
        return None

    def _mark_for(self, row: Row) -> str:
        if not self.line_marks:
            return ""
        ref = self.line_ref(row)
        if ref is None and row.pair is not None:
            ref = self.line_ref(row.pair)
        return self.line_marks.get(ref, "") if ref else ""

    def cursor_line_ref(self) -> tuple[str, int] | None:
        if not self.rows or self.cursor >= len(self.rows):
            return None
        row = self.rows[self.cursor]
        ref = self.line_ref(row)
        if ref is None and row.pair is not None:
            ref = self.line_ref(row.pair)
        return ref

    def _side_segments(self, r: Row | None, w: int, base: Style, offset: int) -> list[Segment]:
        style = self.get_component_rich_style
        if r is None or (r.marker == "" and not r.text):
            return [Segment(" " * w, base + style("diff-view--gutter"))]
        no = r.old_no if r.old_no is not None else r.new_no
        gutter = f"{'' if no is None else no:>5} "
        text_style = {
            "+": style("diff-view--added"),
            "-": style("diff-view--removed"),
        }.get(r.marker, Style())
        # the line number and +/- stay put while the text scrolls sideways
        marker = r.marker if r.marker in "+-" else " "
        room = max(w - SPLIT_GUTTER, 0)
        body = r.text[offset : offset + room].ljust(room)
        return [
            Segment(gutter[: min(w, len(gutter))], base + style("diff-view--gutter")),
            Segment(marker if w > len(gutter) else "", base + text_style),
            Segment(body, base + text_style),
        ]

    def _render_split(self, row: Row, idx: int, width: int, base: Style) -> Strip:
        style = self.get_component_rich_style
        left, right = self._split_halves(width)
        offset = self.scroll_offset.x
        segs = self._side_segments(row, left, base, offset)
        segs.append(Segment("│", base + style("diff-view--gutter")))
        segs += self._side_segments(row.pair, right, base, offset)
        strip = Strip(segs).crop_extend(0, width, base)
        overlay = None
        if idx == self.cursor:
            overlay = style("diff-view--cursor")
        elif self.rows[self.cursor].hunk == row.hunk and row.hunk >= 0 and self.has_focus:
            overlay = style("diff-view--hunk-active")
        if overlay is not None and overlay.bgcolor is not None:
            top = Style(bgcolor=overlay.bgcolor)
            strip = Strip(
                [Segment(sg.text, (sg.style or Style()) + top) for sg in strip], strip.cell_length
            )
        return strip

    def on_mount(self) -> None:
        # diffs opened later (History, Shelf…) also start in the configured mode
        config = getattr(self.app, "config", None)
        if config is not None and config.side_by_side and not self.split:
            self.split = True
            self._rebuild()

    def on_focus(self) -> None:
        self.refresh()

    def on_blur(self) -> None:
        self.refresh()
