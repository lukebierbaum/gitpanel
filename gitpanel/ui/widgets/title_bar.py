"""The one-line bar at the top of a window or screen: title · info · key reminders."""

from __future__ import annotations

from collections.abc import Sequence

from rich.text import Text
from textual.widgets import Static

from .key_hints import pretty_key


class TitleBar(Static):
    DEFAULT_CSS = """
    TitleBar { height: auto; min-height: 1; padding: 0 1; background: $panel; }
    """

    def set(
        self,
        title: str | Text,
        *info: str | Text,
        keys: Sequence[tuple[str, str]] = (),
        below: Text | None = None,
    ) -> None:
        """`title` in bold (or as styled), each `info` part dimmed (or as styled), then the
        `keys` as "key label" like the hint bar; `below` goes on a second line."""
        t = Text(no_wrap=True, overflow="ellipsis")
        t.append_text(title if isinstance(title, Text) else Text(title, style="bold"))
        for part in info:
            if part:
                t.append("   ")
                t.append_text(part if isinstance(part, Text) else Text(part, style="dim"))
        for i, (key, label) in enumerate(keys):
            t.append("   " if i == 0 else "  ")
            t.append(pretty_key(key), style="bold")
            t.append(f" {label}", style="dim")
        if below is not None:
            t.append("\n")
            t.append_text(below)
        self.update(t)
