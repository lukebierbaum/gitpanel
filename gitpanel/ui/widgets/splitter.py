"""Draggable divider between two panes. Resizes the pane before (or after) it; sizes are
remembered."""

from __future__ import annotations

import contextlib
import json
import os
from pathlib import Path
from typing import Literal

from rich.text import Text
from textual import events
from textual.widget import Widget


def _state_file() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(base) / "gitpanel" / "layout.json"


def load_state() -> dict[str, object]:
    """Remembered UI state (pane sizes, list sort orders), keyed like "prs.list"."""
    try:
        data = json.loads(_state_file().read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_state(key: str, value: object | None) -> None:
    state = load_state()
    if value is None:
        state.pop(key, None)
    else:
        state[key] = value
    path = _state_file()
    with contextlib.suppress(OSError):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state, indent=1, sort_keys=True))


def load_sizes() -> dict[str, int]:
    return {k: v for k, v in load_state().items() if isinstance(v, int)}


def save_size(key: str, cells: int | None) -> None:
    save_state(key, cells)


class Splitter(Widget):
    """A 1-cell bar. Drag to resize the previous sibling; double-click to reset it.

    `axis="x"` sits between side-by-side panes (resizes width), `axis="y"`
    between stacked panes (resizes height). `resizes="next"` sizes the following sibling
    instead, for a pane that sits below or right of a flexible one.
    """

    DEFAULT_CSS = """
    Splitter { color: $panel-lighten-2; }
    Splitter.-x { width: 1; height: 1fr; }
    Splitter.-y { height: 1; width: 1fr; }
    Splitter:hover, Splitter.-dragging { color: $accent; background: $accent 15%; }
    Splitter.-hidden { display: none; }
    """

    def __init__(
        self,
        key: str,
        axis: Literal["x", "y"] = "x",
        *,
        minimum: int = 8,
        resizes: Literal["previous", "next"] = "previous",
        id: str | None = None,
    ) -> None:
        super().__init__(id=id, classes=f"-{axis}")
        self.key = key
        self.axis = axis
        self.resizes = resizes
        self.minimum = minimum
        self._origin: tuple[int, int] | None = None  # (mouse position, pane size) at drag start
        self._default: object = None

    @property
    def pane(self) -> Widget | None:
        siblings = list(self.parent.children) if self.parent else []
        i = siblings.index(self)
        if self.resizes == "next":
            return siblings[i + 1] if i + 1 < len(siblings) else None
        return siblings[i - 1] if i > 0 else None

    def on_mount(self) -> None:
        pane = self.pane
        if pane is None:
            return
        self._default = pane.styles.width if self.axis == "x" else pane.styles.height
        saved = load_sizes().get(self.key)
        if saved:
            self._apply(saved)

    def render(self) -> Text:
        if self.axis == "x":
            return Text("\n".join("│" * 1 for _ in range(max(self.size.height, 1))))
        return Text("─" * max(self.size.width, 1))

    def _limit(self, cells: int) -> int:
        parent = self.parent
        total = (parent.size.width if self.axis == "x" else parent.size.height) if parent else 0
        upper = max(total - self.minimum - 1, self.minimum) if total else cells
        return max(self.minimum, min(cells, upper))

    def _apply(self, cells: int) -> None:
        pane = self.pane
        if pane is None:
            return
        cells = self._limit(cells)
        if self.axis == "x":
            pane.styles.width = cells
        else:
            pane.styles.height = cells

    # -- mouse ------------------------------------------------------------------------
    def on_mouse_down(self, event: events.MouseDown) -> None:
        pane = self.pane
        if pane is None:
            return
        size = pane.outer_size.width if self.axis == "x" else pane.outer_size.height
        pos = event.screen_x if self.axis == "x" else event.screen_y
        self._origin = (pos, size)
        self.capture_mouse()
        self.add_class("-dragging")
        event.stop()

    def on_mouse_move(self, event: events.MouseMove) -> None:
        if self._origin is None:
            return
        start_pos, start_size = self._origin
        pos = event.screen_x if self.axis == "x" else event.screen_y
        delta = pos - start_pos
        self._apply(start_size + (delta if self.resizes == "previous" else -delta))
        event.stop()

    def on_mouse_up(self, event: events.MouseUp) -> None:
        if self._origin is None:
            return
        self._origin = None
        self.release_mouse()
        self.remove_class("-dragging")
        pane = self.pane
        if pane is not None:
            size = pane.outer_size.width if self.axis == "x" else pane.outer_size.height
            save_size(self.key, size)
        event.stop()

    def on_click(self, event: events.Click) -> None:
        if event.chain == 2:  # double-click: back to the default size
            pane = self.pane
            if pane is not None and self._default is not None:
                if self.axis == "x":
                    pane.styles.width = self._default  # type: ignore[assignment]
                else:
                    pane.styles.height = self._default  # type: ignore[assignment]
            save_size(self.key, None)
            event.stop()

    def resize_by(self, delta: int) -> None:
        """Keyboard resizing: grow (positive) or shrink the pane by `delta` cells."""
        pane = self.pane
        if pane is None:
            return
        size = pane.outer_size.width if self.axis == "x" else pane.outer_size.height
        self._apply(size + delta)
        save_size(self.key, self._limit(size + delta))
