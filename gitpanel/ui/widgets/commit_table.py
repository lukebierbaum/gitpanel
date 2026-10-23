"""Log table: graph | subject with ref/worktree labels | author | date (Line API)."""

from __future__ import annotations

from dataclasses import dataclass, field

from rich.segment import Segment
from rich.style import Style
from textual.geometry import Size
from textual.message import Message
from textual.strip import Strip

from ...git.graph import GraphRow
from ...git.markers import Marker
from ...git.models import Commit
from ..colors import C
from ..fmt import fmt_date
from .cursor_view import CursorView, overlay

# marker kind → (fg, bg, bold, italic) colour names; resolved per theme in marker_style()
MARKER_LOOK = {
    "head": ("ink", "green", True, False),
    "branch": ("green", None, True, False),
    "remote": ("purple", None, False, False),
    "tag": ("amber", None, False, False),
    "worktree": ("worktree", None, False, True),
    "picked": ("muted", None, False, True),
    "unpushed": ("unpushed", None, True, False),
    "incoming": ("incoming", None, False, True),
    "bisect-bad": ("ink", "red", True, False),
    "bisect-good": ("ink", "green", True, False),
    "bisect-skip": ("amber", None, False, True),
    "bisect-current": ("ink", "amber", True, False),
}


def marker_style(kind: str) -> Style:
    look = MARKER_LOOK.get(kind)
    if look is None:
        return Style()
    fg, bg, bold, italic = look
    return Style(color=C[fg], bgcolor=C[bg] if bg else None, bold=bold, italic=italic)


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


class CommitTable(CursorView):
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
    MOVE_CLEARS_RANGE = True

    class CursorChanged(Message):
        def __init__(self, table: CommitTable) -> None:
            super().__init__()
            self.table = table

    class NeedMore(Message):
        pass

    def __init__(self, *, id: str | None = None) -> None:
        super().__init__(id=id)
        self.rows: list[LogRow] = []
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
    @property
    def row_count(self) -> int:
        return len(self.rows)

    def selected_rows(self) -> list[LogRow]:
        if not self.rows:
            return []
        r = self.selection_range()
        return self.rows[r[0] : r[1] + 1] if r else [self.rows[self.cursor]]

    def selected_commits(self) -> list[Commit]:
        return [r.commit for r in self.selected_rows()]

    def cursor_moved(self) -> None:
        self.post_message(self.CursorChanged(self))
        if self.has_more and self.cursor >= len(self.rows) - 50:
            self.post_message(self.NeedMore())

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
        node_color = next((m.node_color for m in row.markers if m.node_color), None)
        for ch, color in row.graph.cells:
            lanes = C.lanes
            st = base + Style(color=lanes[color % len(lanes)]) if color >= 0 else base
            if ch in "●◆◉":
                st += Style(bold=True)
                if node_color:
                    st += Style(color=C[node_color])
            segs.append(Segment(ch, st))
        # two spaces: fonts often draw ● ◆ ◉ wider than one cell, so give them room
        segs.append(Segment(" " * (graph_w - row.graph.width + 2), base))

        right_w = AUTHOR_W + DATE_W + 2
        subject_w = max(width - graph_w - 1 - right_w, 10)
        used = 0
        for m in row.markers:
            if not m.label:
                continue  # colour-only marker (e.g. unpushed)
            label = f" {m.label} " if m.kind == "head" else f"[{m.label}]"
            if used + len(label) + 1 > subject_w // 2:
                break
            segs.append(Segment(label, base + marker_style(m.kind)))
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
        return overlay(
            strip,
            self.row_highlight(
                idx, cstyle("commit-table--cursor"), cstyle("commit-table--selected")
            ),
        )
