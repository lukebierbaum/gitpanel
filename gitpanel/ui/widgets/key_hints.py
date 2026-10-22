"""Footer listing the keyed actions available in the current context (from the registry)."""

from __future__ import annotations

from rich.text import Text
from textual.widgets import Static

from ...actions.context import SelectionContext
from ...actions.registry import Registry

# Spelled out on purpose: Mac symbols (⌥ ⇧) read as Command/Super on Linux.
KEY_DISPLAY = {
    "question_mark": "?",
    "full_stop": ".",
    "ctrl+": "^",
}


def pretty_key(key: str) -> str:
    out = key
    for k, v in KEY_DISPLAY.items():
        out = out.replace(k, v)
    return out


class KeyHints(Static):
    DEFAULT_CSS = """
    KeyHints {
        height: 1;
        background: $panel;
        color: $text-muted;
        padding: 0 1;
    }
    """

    def show_for(
        self,
        registry: Registry,
        ctx: SelectionContext,
        extra: list[tuple[str, str]],
        *,
        screen_only: bool = False,
    ) -> None:
        t = Text(no_wrap=True, overflow="ellipsis")
        items: list[tuple[str, str]] = []
        for action in registry.available(ctx):
            if screen_only and action.screens is None:
                continue
            keys = registry.keys_for(action)
            if keys:
                items.append((keys[0], action.label_for(ctx)))
        items.extend(extra)
        for i, (key, label) in enumerate(items):
            if i:
                t.append("  ")
            t.append(pretty_key(key), style="bold")
            t.append(f" {label}")
        self.update(t)
