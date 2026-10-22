"""3-pane merge conflict resolver: ours | result | theirs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from rich.segment import Segment
from rich.style import Style
from rich.text import Text
from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.geometry import Size
from textual.message import Message
from textual.reactive import reactive
from textual.screen import Screen
from textual.scroll_view import ScrollView
from textual.strip import Strip
from textual.widgets import Static

from ...actions.context import Scope, SelectionContext
from ...actions.registry import Registry
from ...git import conflicts as CF
from ..widgets.key_hints import KeyHints

if TYPE_CHECKING:
    from ..app import GitPanelApp

GREEN = "#6CCB3A"
PURPLE = "#A66BE0"


@dataclass(frozen=True)
class Row:
    left: str | None
    mid: str | None
    right: str | None
    chunk: int = -1  # index into model.chunks, -1 for common text
    unresolved: bool = False


def build_rows(model: CF.MergeModel) -> list[Row]:
    rows: list[Row] = []
    ci = -1
    for seg in model.segments:
        if isinstance(seg, CF.Common):
            rows += [Row(ln, ln, ln) for ln in seg.lines]
            continue
        ci += 1
        result = seg.lines()
        mid = result if result is not None else ["⟪ unresolved — ← ours · → theirs · e edit ⟫\n"]
        n = max(len(seg.ours), len(seg.theirs), len(mid), 1)
        for k in range(n):
            rows.append(
                Row(
                    seg.ours[k] if k < len(seg.ours) else None,
                    mid[k] if k < len(mid) else None,
                    seg.theirs[k] if k < len(seg.theirs) else None,
                    chunk=ci,
                    unresolved=result is None,
                )
            )
    return rows


class ThreeWayView(ScrollView, can_focus=True):
    COMPONENT_CLASSES = {
        "three--common",
        "three--chunk",
        "three--current",
        "three--unresolved",
        "three--resolved",
        "three--sep",
    }
    DEFAULT_CSS = """
    ThreeWayView { background: $surface; }
    ThreeWayView > .three--common { color: $text-muted; }
    ThreeWayView > .three--chunk { background: $primary 18%; }
    ThreeWayView > .three--current { background: $primary 45%; }
    ThreeWayView > .three--unresolved { color: $warning; text-style: italic; }
    ThreeWayView > .three--resolved { color: $success; }
    ThreeWayView > .three--sep { color: $primary; }
    """
    BINDINGS = [
        Binding("n,f7,down", "chunk(1)", "Next conflict", show=False),
        Binding("p,shift+f7,up", "chunk(-1)", "Previous conflict", show=False),
        Binding("pageup", "scroll_page_up", show=False),
        Binding("pagedown", "scroll_page_down", show=False),
    ]

    current: reactive[int] = reactive(0, always_update=True)

    class ChunkChanged(Message):
        pass

    def __init__(self, *, id: str | None = None) -> None:
        super().__init__(id=id)
        self.model: CF.MergeModel | None = None
        self.rows: list[Row] = []

    def set_model(self, model: CF.MergeModel) -> None:
        self.model = model
        self.rebuild()

    def rebuild(self) -> None:
        if self.model is None:
            return
        self.rows = build_rows(self.model)
        self.virtual_size = Size(self.size.width, len(self.rows))
        n = len(self.model.chunks)
        if n and self.current >= n:
            self.current = n - 1
        self.refresh()

    def action_chunk(self, delta: int) -> None:
        if self.model is None or not self.model.chunks:
            return
        self.current = (self.current + delta) % len(self.model.chunks)

    def next_unresolved(self) -> None:
        if self.model is None:
            return
        chunks = self.model.chunks
        for k in range(1, len(chunks) + 1):
            i = (self.current + k) % len(chunks)
            if not chunks[i].resolved:
                self.current = i
                return

    def watch_current(self) -> None:
        first = next((i for i, r in enumerate(self.rows) if r.chunk == self.current), 0)
        h = self.size.height or 20
        self.scroll_to(y=max(first - h // 3, 0), animate=False)
        self.refresh()
        self.post_message(self.ChunkChanged())

    def render_line(self, y: int) -> Strip:
        idx = y + self.scroll_offset.y
        width = self.size.width
        base = self.rich_style
        if idx >= len(self.rows):
            return Strip.blank(width, base)
        row = self.rows[idx]
        cs = self.get_component_rich_style
        col = max((width - 2) // 3, 8)
        widths = (col, width - 2 - 2 * col, col)
        if row.chunk < 0:
            style = base + cs("three--common")
            bg = None
        else:
            bg = cs("three--current" if row.chunk == self.current else "three--chunk")
            style = base
        segs: list[Segment] = []
        for i, (text, w) in enumerate(zip((row.left, row.mid, row.right), widths, strict=True)):
            if i:
                segs.append(Segment("│", base + cs("three--sep")))
            body = (text or "").rstrip("\r\n").replace("\t", "    ")
            body = body[:w].ljust(w)
            st = style
            if i == 1 and row.chunk >= 0:
                st = st + cs("three--unresolved" if row.unresolved else "three--resolved")
            if bg is not None and bg.bgcolor is not None:
                st = st + Style(bgcolor=bg.bgcolor)
            segs.append(Segment(body, st))
        return Strip(segs).crop_extend(0, width, base)


class ResolverScreen(Screen[None]):
    DEFAULT_CSS = """
    ResolverScreen { layout: vertical; }
    ResolverScreen #res-header { height: 2; padding: 0 1; background: $panel; }
    ResolverScreen ThreeWayView { height: 1fr; }
    """
    BINDINGS = [Binding("escape", "close", "Close")]

    def __init__(self, path: str) -> None:
        super().__init__()
        self.path = path
        self.model: CF.MergeModel | None = None
        self.labels: CF.Labels | None = None

    @property
    def gp(self) -> GitPanelApp:
        return self.app  # type: ignore[return-value]

    def compose(self) -> ComposeResult:
        yield Static("", id="res-header")
        yield ThreeWayView(id="three")
        yield KeyHints(id="res-hints")

    def on_mount(self) -> None:
        self.query_one(ThreeWayView).focus()
        self.load()

    @work(exclusive=True, group="resolver-load")
    async def load(self) -> None:
        git = self.gp.session.git  # type: ignore[union-attr]
        st = await CF.stages(git, self.path)
        if st.binary or st.ours is None or st.theirs is None:
            self.gp.notify(
                f"{self.path} can't be merged line by line (binary or deleted on one side) — "
                "use Accept Yours / Accept Theirs",
                severity="warning",
            )
            self.dismiss()
            return
        self.model = await CF.merge_model(git, self.path, st)
        self.labels = await CF.labels(git)
        self.query_one(ThreeWayView).set_model(self.model)
        self.update_header()

    def update_header(self) -> None:
        if self.model is None or self.labels is None:
            return
        t = Text(no_wrap=True, overflow="ellipsis")
        t.append(f"{self.path}", style="bold")
        n = len(self.model.chunks)
        un = self.model.unresolved
        t.append(f"   {n - un}/{n} resolved", style=GREEN if un == 0 else "#E3B23C")
        if self.model.chunks:
            t.append(f"   chunk {self.query_one(ThreeWayView).current + 1}", style="dim")
        t.append("\n")
        w = max((self.size.width - 4) // 3, 8)  # header has 1 cell of padding each side
        t.append(self.labels.ours[:w].ljust(w), style=f"bold {GREEN}")
        t.append(" ")
        t.append("Result"[:w].center(w), style="bold")
        t.append(" ")
        t.append(self.labels.theirs[:w].rjust(w), style=f"bold {PURPLE}")
        self.query_one("#res-header", Static).update(t)

    def on_three_way_view_chunk_changed(self) -> None:
        self.update_header()
        self.gp.refresh_hints()

    def handle_event(self, event: str, data: dict[str, Any]) -> None:
        view = self.query_one(ThreeWayView)
        if event == "resolver.changed":
            view.rebuild()
            if data.get("advance"):
                view.next_unresolved()
            self.update_header()
            self.gp.refresh_hints()
        elif event == "resolver.done":
            self.dismiss()

    def on_resize(self) -> None:
        self.update_header()

    def action_close(self) -> None:
        self.dismiss()

    # -- registry integration -------------------------------------------------------------
    def context(self, base: SelectionContext) -> SelectionContext:
        view = self.query(ThreeWayView)
        kwargs: dict[str, Any] = {"screen": "resolver", "form": {"merge": self.model}}
        if view and self.model is not None and self.model.chunks:
            kwargs["form"] = {"merge": self.model, "chunk": view.first().current}
            kwargs["focus"] = Scope.CONFLICT
        return SelectionContext(**{**base.__dict__, **kwargs})

    def show_hints(self, registry: Registry, ctx: SelectionContext) -> None:
        hints = self.query(KeyHints)
        if hints:
            hints.first().show_for(
                registry, ctx, [("n/p", "next/prev"), ("esc", "close")], screen_only=True
            )
