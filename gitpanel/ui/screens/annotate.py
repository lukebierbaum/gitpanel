"""Annotate (git blame) view: gutter with commit, author and age; line range selection."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from rich.segment import Segment
from rich.style import Style
from rich.text import Text
from textual import events, work
from textual.actions import SkipAction
from textual.app import ComposeResult
from textual.binding import Binding
from textual.geometry import Size
from textual.reactive import reactive
from textual.scroll_view import ScrollView
from textual.strip import Strip

from ...actions.context import SelectionContext
from ...git.history import BlameLine, blame
from ..base import ToolScreen
from ..colors import C
from ..widgets.key_hints import KeyHints
from ..widgets.title_bar import TitleBar

# age colours: newest green → oldest purple
AGE_STEPS = 7


def _blend(a: str, b: str, f: float) -> str:
    ca = [int(a[i : i + 2], 16) for i in (1, 3, 5)]
    cb = [int(b[i : i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * f):02X}" for x, y in zip(ca, cb, strict=True))


def age_color(t: datetime | None, newest: datetime, oldest: datetime) -> str:
    if t is None or newest <= oldest:
        return C.green
    frac = (newest - t).total_seconds() / max((newest - oldest).total_seconds(), 1)
    step = min(int(frac * AGE_STEPS), AGE_STEPS - 1)
    return _blend(C.green, C.purple, step / (AGE_STEPS - 1))


class BlameView(ScrollView, can_focus=True):
    COMPONENT_CLASSES = {"blame--cursor", "blame--range", "blame--gutter"}
    DEFAULT_CSS = """
    BlameView { background: $surface; }
    BlameView > .blame--cursor { background: $primary 50%; }
    BlameView > .blame--range { background: $accent 25%; }
    BlameView > .blame--gutter { color: $text-muted; }
    """
    BINDINGS = [
        Binding("up,k", "move(-1)", show=False),
        Binding("down,j", "move(1)", show=False),
        Binding("pageup", "move(-20)", show=False),
        Binding("pagedown", "move(20)", show=False),
        Binding("shift+up", "extend(-1)", show=False),
        Binding("shift+down", "extend(1)", show=False),
        Binding("v", "toggle_range", show=False),
        Binding("escape", "clear", show=False),
    ]
    cursor: reactive[int] = reactive(0, always_update=True)

    def __init__(self, *, id: str | None = None) -> None:
        super().__init__(id=id)
        self.lines: list[BlameLine] = []
        self.anchor: int | None = None
        self._newest = datetime.now(UTC)
        self._oldest = datetime.now(UTC)

    def set_lines(self, lines: list[BlameLine], focus_line: int | None = None) -> None:
        self.lines = lines
        times = [ln.time for ln in lines if ln.time is not None]
        if times:
            self._newest, self._oldest = max(times), min(times)
        self.virtual_size = Size(self.size.width, len(lines))
        self.cursor = max(0, min((focus_line or 1) - 1, len(lines) - 1))
        self.refresh()

    def validate_cursor(self, v: int) -> int:
        return max(0, min(v, len(self.lines) - 1)) if self.lines else 0

    def watch_cursor(self) -> None:
        h = self.size.height or 20
        y = self.scroll_offset.y
        if self.cursor < y:
            self.scroll_to(y=self.cursor, animate=False)
        elif self.cursor >= y + h:
            self.scroll_to(y=self.cursor - h + 1, animate=False)
        self.refresh()
        self.screen.refresh_hints()  # type: ignore[attr-defined]

    def action_move(self, d: int) -> None:
        self.cursor += d

    def action_extend(self, d: int) -> None:
        if self.anchor is None:
            self.anchor = self.cursor
        self.cursor += d

    def action_toggle_range(self) -> None:
        self.anchor = None if self.anchor is not None else self.cursor
        self.refresh()
        self.screen.refresh_hints()  # type: ignore[attr-defined]

    def action_clear(self) -> None:
        if self.anchor is None:
            raise SkipAction()
        self.anchor = None
        self.refresh()

    def range(self) -> tuple[int, int] | None:
        if self.anchor is None:
            return None
        lo, hi = sorted((self.anchor, self.cursor))
        return lo + 1, hi + 1

    def on_click(self, event: events.Click) -> None:
        row = event.y + self.scroll_offset.y
        if 0 <= row < len(self.lines):
            self.cursor = row
        self.focus()

    def render_line(self, y: int) -> Strip:
        idx = y + self.scroll_offset.y
        width = self.size.width
        base = self.rich_style
        if idx >= len(self.lines):
            return Strip.blank(width, base)
        ln = self.lines[idx]
        prev = self.lines[idx - 1] if idx else None
        first_of_block = prev is None or prev.sha != ln.sha
        cs = self.get_component_rich_style
        if ln.uncommitted:
            gutter = "  not committed yet          " if first_of_block else " " * 29
            gstyle = Style(color=C.amber, italic=True)
        elif first_of_block:
            date = ln.time.astimezone().strftime("%Y-%m-%d") if ln.time else ""
            gutter = f" {ln.sha[:8]} {ln.author[:8]:<8} {date} "
            gstyle = Style(color=age_color(ln.time, self._newest, self._oldest))
        else:
            gutter = " " * 29
            gstyle = Style()
        segs = [
            Segment(gutter[:29].ljust(29), base + gstyle),
            Segment(f"{ln.line:>5} ", base + cs("blame--gutter")),
            Segment(ln.text.replace("\t", "    "), base),
        ]
        strip = Strip(segs).crop_extend(0, width, base)
        overlay = None
        r = self.range()
        if r and r[0] - 1 <= idx <= r[1] - 1:
            overlay = cs("blame--range")
        if idx == self.cursor:
            overlay = cs("blame--cursor")
        if overlay is not None and overlay.bgcolor is not None:
            top = Style(bgcolor=overlay.bgcolor)
            strip = Strip(
                [Segment(s.text, (s.style or Style()) + top) for s in strip], strip.cell_length
            )
        return strip


class AnnotateScreen(ToolScreen):
    DEFAULT_CSS = """
    AnnotateScreen { layout: vertical; }
    AnnotateScreen BlameView { height: 1fr; }
    """
    HINT_EXTRAS = [("v", "select lines"), ("esc", "close")]

    def __init__(self, path: str, rev: str | None = None, line: int | None = None) -> None:
        super().__init__()
        self.path, self.rev, self.line = path, rev, line

    def compose(self) -> ComposeResult:
        yield TitleBar(id="ann-header")
        yield BlameView(id="blame")
        yield KeyHints(id="ann-hints")

    def on_mount(self) -> None:
        self.query_one(BlameView).focus()
        self.load()

    @work(exclusive=True, group="annotate-load")
    async def load(self) -> None:
        git = self.git
        try:
            lines = await blame(git, self.path, self.rev)
        except Exception as e:
            self.gp.notify(f"Annotate failed: {e}", severity="error")
            self.dismiss()
            return
        self.query_one(BlameView).set_lines(lines, self.line)
        self.update_header()

    def update_header(self) -> None:
        view = self.query_one(BlameView)
        line = Text()
        if view.lines:
            ln = view.lines[view.cursor]
            if ln.uncommitted:
                line.append("Not committed yet", style=C.warn)
            else:
                line.append(f"{ln.sha[:10]} ", style=f"bold {C.sha}")
                line.append(f"{ln.author} · {ln.summary}", style="dim")
        self.query_one("#ann-header", TitleBar).set(
            f"Annotate {self.path}",
            f"@ {self.rev[:12]}" if self.rev else "(working tree)",
            below=line,
        )

    def refresh_hints(self) -> None:
        if self.is_mounted:
            self.update_header()
            self.gp.refresh_hints()

    def context(self, base: SelectionContext) -> SelectionContext:
        views = self.query(BlameView)
        form: dict[str, Any] = {"path": self.path, "rev": self.rev}
        if views and views.first().lines:
            view = views.first()
            ln = view.lines[view.cursor]
            form["blame_line"] = {
                "sha": ln.sha,
                "orig_line": ln.orig_line,
                "orig_path": ln.orig_path,
                "uncommitted": ln.uncommitted,
            }
            r = view.range()
            if r:
                form["start"], form["end"] = r
        return base.with_(screen="annotate", form=form)
