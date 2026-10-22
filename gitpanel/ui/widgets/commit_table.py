"""Log table: graph | subject with ref/worktree labels | author | date (Line API)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from rich.segment import Segment
from rich.style import Style
from textual import events
from textual.binding import Binding
from textual.geometry import Size
from textual.message import Message
from textual.reactive import reactive
from textual.scroll_view import ScrollView
from textual.strip import Strip

from ...git.graph import GraphRow
from ...git.markers import Marker
from ...git.models import Commit

# lane colours: Hulk greens and purples first, then contrasting hues
LANE_COLORS = [
    "#6CCB3A", "#A66BE0", "#3FB8AF", "#E3B23C", "#E06C9F",
    "#4FA3E0", "#9BDB4D", "#C792EA", "#F78C6C", "#7FDBCA",
]  # fmt: skip

MARKER_STYLES = {
    "head": Style(color="#101410", bgcolor="#6CCB3A", bold=True),
    "branch": Style(color="#6CCB3A", bold=True),
    "remote": Style(color="#A66BE0"),
    "tag": Style(color="#E3B23C"),
    "worktree": Style(color="#3FB8AF", italic=True),
    "picked": Style(color="#8A8F87", italic=True),
}

AUTHOR_W = 16
DATE_W = 16


@dataclass
class LogRow:
    commit: Commit
    graph: GraphRow
    markers: list[Marker] = field(default_factory=list)

    @property
    def dimmed(self) -> bool:
        return any(m.dims_row for m in self.markers)

    @property
    def is_head(self) -> bool:
        return any(m.kind == "head" or m.label == "HEAD" for m in self.markers)


def fmt_date(d: datetime | None) -> str:
    return d.astimezone().strftime("%Y-%m-%d %H:%M") if d else ""


class CommitTable(ScrollView, can_focus=True):
    COMPONENT_CLASSES = {
        "commit-table--cursor",
        "commit-table--selected",
        "commit-table--meta",
        "commit-table--dim",
    }
    DEFAULT_CSS = """
    CommitTable { background: $surface; }
    CommitTable > .commit-table--cursor { background: $primary 35%; }
    CommitTable:focus > .commit-table--cursor { background: $primary 60%; }
    CommitTable > .commit-table--selected { background: $accent 25%; }
    CommitTable > .commit-table--meta { color: $text-muted; }
    CommitTable > .commit-table--dim { color: $text-disabled; }
    """
    BINDINGS = [
        Binding("up,k", "move(-1)", show=False),
        Binding("down,j", "move(1)", show=False),
        Binding("pageup", "page(-1)", show=False),
        Binding("pagedown", "page(1)", show=False),
        Binding("home", "jump(0)", show=False),
        Binding("end", "jump(-1)", show=False),
        Binding("shift+up", "extend(-1)", show=False),
        Binding("shift+down", "extend(1)", show=False),
        Binding("escape", "clear_selection", show=False),
    ]

    cursor: reactive[int] = reactive(0, always_update=True)

    class CursorChanged(Message):
        def __init__(self, table: CommitTable) -> None:
            super().__init__()
            self.table = table

    class NeedMore(Message):
        pass

    def __init__(self, *, id: str | None = None) -> None:
        super().__init__(id=id)
        self.rows: list[LogRow] = []
        self.anchor: int | None = None
        self.has_more = False
        self._graph_w = 2

    def set_rows(self, rows: list[LogRow], *, keep_sha: str | None = None, has_more: bool) -> None:
        self.rows = rows
        self.has_more = has_more
        self.anchor = None
        self._recalc()
        idx = 0
        if keep_sha:
            idx = next((i for i, r in enumerate(rows) if r.commit.sha == keep_sha), 0)
        self.cursor = idx
        if idx == 0:
            self.scroll_to(0, 0, animate=False)
        self.refresh()

    def append_rows(self, rows: list[LogRow], *, has_more: bool) -> None:
        self.rows.extend(rows)
        self.has_more = has_more
        self._recalc()
        self.refresh()

    def update_markers(self, markers: dict[str, list[Marker]]) -> None:
        for r in self.rows:
            r.markers = markers.get(r.commit.sha, [])
        self.refresh()

    def _recalc(self) -> None:
        self._graph_w = max((r.graph.width for r in self.rows), default=2)
        self.virtual_size = Size(self.size.width, len(self.rows))

    # -- selection ------------------------------------------------------------------
    def selected_rows(self) -> list[LogRow]:
        if not self.rows:
            return []
        if self.anchor is None:
            return [self.rows[self.cursor]]
        lo, hi = sorted((self.anchor, self.cursor))
        return self.rows[lo : hi + 1]

    def selected_commits(self) -> list[Commit]:
        return [r.commit for r in self.selected_rows()]

    def watch_cursor(self) -> None:
        h = self.scrollable_content_region.height or self.size.height
        y = self.scroll_offset.y
        if self.cursor < y:
            self.scroll_to(y=self.cursor, animate=False)
        elif h and self.cursor >= y + h:
            self.scroll_to(y=self.cursor - h + 1, animate=False)
        self.refresh()
        self.post_message(self.CursorChanged(self))
        if self.has_more and self.cursor >= len(self.rows) - 50:
            self.post_message(self.NeedMore())

    def validate_cursor(self, value: int) -> int:
        return max(0, min(value, len(self.rows) - 1)) if self.rows else 0

    def action_move(self, delta: int) -> None:
        self.anchor = None
        self.cursor += delta

    def action_page(self, direction: int) -> None:
        self.anchor = None
        self.cursor += direction * max(self.size.height - 1, 1)

    def action_jump(self, where: int) -> None:
        self.anchor = None
        self.cursor = 0 if where == 0 else len(self.rows) - 1

    def action_extend(self, delta: int) -> None:
        if self.anchor is None:
            self.anchor = self.cursor
        self.cursor += delta

    def action_clear_selection(self) -> None:
        if self.anchor is not None:
            self.anchor = None
            self.refresh()
            self.post_message(self.CursorChanged(self))

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

    def on_resize(self) -> None:
        self._recalc()

    # -- rendering ---------------------------------------------------------------------
    def render_line(self, y: int) -> Strip:
        idx = y + self.scroll_offset.y
        width = self.scrollable_content_region.width or self.size.width
        base = self.rich_style
        if idx >= len(self.rows):
            return Strip.blank(width, base)
        row = self.rows[idx]
        cstyle = self.get_component_rich_style
        meta = cstyle("commit-table--meta")
        dim = cstyle("commit-table--dim") if row.dimmed else None

        segs: list[Segment] = []
        graph_w = self._graph_w
        for ch, color in row.graph.cells:
            st = base + Style(color=LANE_COLORS[color % len(LANE_COLORS)]) if color >= 0 else base
            if ch in "●◆◉":
                st += Style(bold=True)
            segs.append(Segment(ch, st))
        segs.append(Segment(" " * (graph_w - row.graph.width + 1), base))

        right_w = AUTHOR_W + DATE_W + 2
        subject_w = max(width - graph_w - 1 - right_w, 10)
        used = 0
        for m in row.markers:
            label = f" {m.label} " if m.kind == "head" else f"[{m.label}]"
            if used + len(label) + 1 > subject_w // 2:
                break
            segs.append(Segment(label, base + MARKER_STYLES.get(m.kind, Style())))
            segs.append(Segment(" ", base))
            used += len(label) + 1
        subject = row.commit.subject
        room = subject_w - used
        if len(subject) > room:
            subject = subject[: max(room - 1, 0)] + "…"
        sub_style = base + (Style(bold=True) if row.is_head else Style())
        if dim:
            sub_style += dim
        segs.append(Segment(subject.ljust(room), sub_style))
        author = row.commit.author_name[:AUTHOR_W].ljust(AUTHOR_W)
        segs.append(Segment(" " + author + " ", base + meta))
        segs.append(Segment(fmt_date(row.commit.author_date).ljust(DATE_W), base + meta))

        strip = Strip(segs).crop_extend(0, width, base)
        overlay: Style | None = None
        if self.anchor is not None:
            lo, hi = sorted((self.anchor, self.cursor))
            if lo <= idx <= hi:
                overlay = cstyle("commit-table--selected")
        if idx == self.cursor:
            overlay = cstyle("commit-table--cursor")
        if overlay is not None and overlay.bgcolor is not None:
            top = Style(bgcolor=overlay.bgcolor)
            strip = Strip(
                [Segment(s.text, (s.style or Style()) + top) for s in strip], strip.cell_length
            )
        return strip
