"""Unified diff view with hunk and line selection (Line API, scales to large diffs).

Unchanged stretches the diff leaves out show as `⋯ N unchanged lines` rows: `enter` or a
click expands one (its `⌃` row folds it again), `f` shows the whole file. The text for
them is read in the background (`patch.unchanged_text`) whenever a diff is shown."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from rich.segment import Segment
from rich.style import Style
from textual import events, work
from textual.binding import Binding
from textual.geometry import Size
from textual.message import Message
from textual.strip import Strip

from ...git import patch as P
from ...git.patch import DiffSource, FileDiff, Selection
from ..colors import C
from ..fmt import plural
from .cursor_view import CursorView, overlay

GUTTER = 12  # "12345 12345 "
SPLIT_GUTTER = 7  # "12345 +" on each side of a side-by-side row


@dataclass(frozen=True)
class Row:
    # gap: hidden unchanged lines; fold: header of an expanded gap (both: line = gap number)
    kind: Literal["info", "hunk", "line", "gap", "fold"]
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


def build_split_rows(
    diff: FileDiff | None,
    message: str = "",
    *,
    content: list[str] | None = None,
    expanded: frozenset[int] = frozenset(),
    full: bool = False,
) -> list[Row]:
    """Side-by-side rows: left = old side, `pair` = new side; change blocks are zipped."""
    unified = build_rows(diff, message, content=content, expanded=expanded, full=full)
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


def _plain(text: str) -> str:
    return text.replace("\t", "    ").rstrip("\r")


def build_rows(
    diff: FileDiff | None,
    message: str = "",
    *,
    content: list[str] | None = None,
    expanded: frozenset[int] = frozenset(),
    full: bool = False,
) -> list[Row]:
    """Rows for `diff`. With `content` (the new file's lines), the unchanged stretches
    between hunks are shown as gap rows, or as lines when `full` or listed in `expanded`."""
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
    with_gaps = bool(diff.hunks) and not diff.binary and diff.status not in ("A", "D")
    gaps = P.gaps(diff, len(content) if content is not None else None) if with_gaps else []

    def gap(g: int) -> None:
        if not gaps:
            return
        first, last, offset = gaps[g]
        if last < first:
            return
        count = last - first + 1
        if content is not None and (full or g in expanded):
            if not full:  # a handle to fold the lines away again
                rows.append(Row("fold", f"⌃ {plural(count, 'unchanged line')}", line=g))
            for n in range(first, min(last, len(content)) + 1):
                rows.append(Row("line", _plain(content[n - 1]), old_no=n + offset, new_no=n))
            return
        rows.append(Row("gap", f"⋯ {plural(count, 'unchanged line')}", line=g))

    for hi, h in enumerate(diff.hunks):
        gap(hi)
        if not full:
            rows.append(Row("hunk", h.header, hunk=hi))
        for li, ln in enumerate(h.lines):
            rows.append(
                Row(
                    "line",
                    _plain(ln.text),
                    hunk=hi,
                    line=li,
                    marker=ln.kind,
                    old_no=ln.old_no,
                    new_no=ln.new_no,
                )
            )
    if content is not None:  # how much follows the last hunk is only known with the text
        gap(len(diff.hunks))
    return rows


class DiffView(CursorView):
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
        "diff-view--gap",
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
    DiffView > .diff-view--gap { color: $text-muted; background: $foreground 4%; }
    DiffView > .diff-view--cursor { background: $primary 30%; }
    DiffView:focus > .diff-view--cursor { background: $primary 55%; }
    DiffView > .diff-view--selected { background: $accent 35%; }
    DiffView > .diff-view--hunk-active { background: $foreground 6%; }
    """

    BINDINGS = [  # plus CursorView's movement and selection keys
        Binding("v", "toggle_range", "Select lines", show=False),
        Binding("n,f7", "next_hunk(1)", "Next change", show=False),
        Binding("p,shift+f7", "next_hunk(-1)", "Previous change", show=False),
        Binding("s", "toggle_split", "Side-by-side", show=False),
        Binding("f", "toggle_full", "Whole file", show=False),
    ]

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
        self.split = False
        self._message = ""
        # (side, line) → mark shown in the gutter, e.g. review comments ("LEFT" = old side)
        self.line_marks: dict[tuple[str, int], str] = {}
        self._max_width = 0
        self._split_text = 0  # longest line on either side in side-by-side mode
        self.full_file = False  # show the whole file, not just the changes
        self._content: list[str] | None = None  # the new file's lines, for hidden stretches
        self._expanded: set[int] = set()  # gaps opened with enter / click

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
        if not (
            diff is not None
            and self.diff is not None
            and diff.path == self.diff.path
            and source == self.source
        ):
            self._expanded = set()
            self._content = None  # a different file: its text is loaded below
        self.source = source
        self.diff = diff
        self._message = message
        self._rebuild()
        self._load_unchanged()
        if same_file:
            self.cursor = min(old_cursor, max(len(self.rows) - 1, 0))
        else:
            self.cursor = self._first_change()
            self.scroll_to(0, 0, animate=False)
        self.refresh()
        self.post_message(self.SelectionChanged(self))

    def _rebuild(self, *, keep_cursor: bool = False) -> None:
        """Rebuild `rows`; `keep_cursor` keeps it on the same line. The cursor is clamped to
        the new rows either way: it's only validated when assigned, and the rows can shrink
        under it (side-by-side or whole-file switched on in `on_mount`, text arriving)."""
        current = (
            self.rows[min(self.cursor, len(self.rows) - 1)] if keep_cursor and self.rows else None
        )
        opts = {
            "content": self._content,
            "expanded": frozenset(self._expanded),
            "full": self.full_file,
        }
        if self.split:
            self.rows = build_split_rows(self.diff, self._message, **opts)
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
            self.rows = build_rows(self.diff, self._message, **opts)
            self._max_width = max((GUTTER + 1 + len(r.text) for r in self.rows), default=0)
        self.anchor = None
        self._update_virtual_size()
        if current is not None:
            self.cursor = self._find_row(current)
        elif self.cursor >= len(self.rows):
            self.cursor = max(len(self.rows) - 1, 0)
        self.refresh()

    def _find_row(self, target: Row) -> int:
        """The row showing the same line as `target` after a rebuild (else the nearest)."""

        def numbers(r: Row) -> tuple[int | None, int | None]:
            new = r.new_no if r.new_no is not None else (r.pair.new_no if r.pair else None)
            return r.old_no, new

        t_old, t_new = numbers(target)
        best = 0
        for i, r in enumerate(self.rows):
            if (
                target.hunk >= 0
                and r.hunk == target.hunk
                and target.line >= 0
                and (r.line == target.line or (r.pair is not None and r.pair.line == target.line))
            ):
                return i
            old, new = numbers(r)
            if (t_new is not None and new == t_new) or (t_old is not None and old == t_old):
                return i
            if t_new is not None and new is not None and new < t_new:
                best = i
        return best

    # -- hidden unchanged lines -----------------------------------------------------------
    @work(exclusive=True, group="diff-unchanged")
    async def _load_unchanged(self) -> None:
        session = getattr(self.app, "session", None)
        diff, source = self.diff, self.source
        if session is None or diff is None or source is None:
            return
        text = await P.unchanged_text(session.git, source, diff)
        if self.diff is not diff:
            return  # another file is showing by now
        self._content = text
        self._rebuild(keep_cursor=True)

    def expand_gap(self, gap: int) -> None:
        """Show the unchanged lines of `gap` in place of its `⋯` row."""
        at = next((i for i, r in enumerate(self.rows) if r.kind == "gap" and r.line == gap), None)
        self._expanded.add(gap)
        self._rebuild()
        if at is not None:
            self.cursor = min(at, len(self.rows) - 1)

    def collapse_gap(self, gap: int) -> None:
        """Fold an expanded gap back into its `⋯` row."""
        at = next((i for i, r in enumerate(self.rows) if r.kind == "fold" and r.line == gap), None)
        self._expanded.discard(gap)
        self._rebuild()
        if at is not None:
            self.cursor = min(at, len(self.rows) - 1)

    def _toggle_gap_at(self, row: int) -> bool:
        r = self.rows[row] if 0 <= row < len(self.rows) else None
        if r is not None and r.kind == "gap":
            self.expand_gap(r.line)
        elif r is not None and r.kind == "fold":
            self.collapse_gap(r.line)
        else:
            return False
        return True

    def action_toggle_full(self) -> None:
        self.full_file = not self.full_file
        if not self.full_file:
            self._expanded.clear()  # back to just the changes
        self._rebuild(keep_cursor=True)
        self.notify("Whole file" if self.full_file else "Changes only", timeout=2)

    def on_key(self, event: events.Key) -> None:
        # enter on a `⋯` / `⌃` row opens / folds it; anywhere else enter keeps its usual meaning
        if event.key == "enter" and self._toggle_gap_at(self.cursor):
            event.stop()
            event.prevent_default()

    def on_click(self, event: events.Click) -> None:
        self._toggle_gap_at(event.y + self.scroll_offset.y)

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
        self.split = not self.split
        self._rebuild(keep_cursor=True)

    def _first_change(self) -> int:
        for i, r in enumerate(self.rows):
            if r.changed:
                return i
        return 0

    # -- selection -------------------------------------------------------------
    @property
    def has_range(self) -> bool:
        return self.anchor is not None

    @property
    def row_count(self) -> int:
        return len(self.rows)

    def _range(self) -> tuple[int, int]:
        r = self.selection_range()
        assert r is not None
        return r

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
    def cursor_moved(self) -> None:
        self.post_message(self.SelectionChanged(self))

    def action_extend(self, delta: int) -> None:
        if self.split:  # line ranges only in the unified view
            self.cursor += delta
            return
        super().action_extend(delta)

    def action_toggle_range(self) -> None:
        if self.split:
            self.notify("Line selection works in the unified view (press s)", severity="warning")
            return
        super().action_toggle_range()

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
        elif row.kind in ("gap", "fold"):
            text_style = style("diff-view--gap")
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

        highlight = self.row_highlight(
            idx, style("diff-view--cursor"), style("diff-view--selected")
        ) or self._hunk_highlight(row)

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
        return overlay(strip, highlight)

    def _hunk_highlight(self, row: Row) -> Style | None:
        """A faint band over the hunk the cursor is in (while focused, without a range)."""
        if self.anchor is not None or row.hunk < 0 or self.cursor >= len(self.rows):
            return None
        if self.has_focus and self.rows[self.cursor].hunk == row.hunk:
            return self.get_component_rich_style("diff-view--hunk-active")
        return None

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
        if idx == self.cursor:
            return overlay(strip, style("diff-view--cursor"))
        return overlay(strip, self._hunk_highlight(row))

    def on_mount(self) -> None:
        # diffs opened later (History, Shelf…) also start in the configured mode
        config = getattr(self.app, "config", None)
        if config is not None and config.side_by_side and not self.split:
            self.split = True
            self._rebuild()
        if config is not None and getattr(config, "diff_full_file", False):
            self.full_file = True
            self._rebuild()

    def on_focus(self) -> None:
        self.refresh()

    def on_blur(self) -> None:
        self.refresh()
