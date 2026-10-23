"""Footer listing the keyed actions available in the current context (from the registry).

The help key is always pinned to the right-hand end, so it's there even when the hints
don't fit."""

from __future__ import annotations

from rich.text import Text
from textual.widget import Widget

from ...actions.context import Scope, SelectionContext
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


HELP_ACTION = "app.help"


class KeyHints(Widget):
    DEFAULT_CSS = """
    KeyHints {
        height: 1;
        background: $panel;
        color: $text-muted;
        padding: 0 1;
    }
    """

    def __init__(self, *, id: str | None = None) -> None:
        super().__init__(id=id)
        self._hints = Text()

    @property
    def text(self) -> str:
        """All hints as plain text (the bar itself may cut them off)."""
        return self._hints.plain

    def update(self, hints: Text) -> None:
        """Show `hints` as they are (screens that compose their own)."""
        self._hints = hints
        self.refresh()

    def _help_badge(self) -> Text:
        registry: Registry | None = getattr(self.app, "registry", None)
        badge = Text()
        try:
            keys = registry.keys_for(registry.get(HELP_ACTION)) if registry else []
        except KeyError:
            keys = []
        if keys:
            accent = self.app.current_theme.accent or "magenta"
            badge.append(f" {pretty_key(keys[0])} ", style=f"bold reverse {accent}")
            badge.append(" help")
        return badge

    def render(self) -> Text:
        help_badge = self._help_badge()
        width = self.content_region.width or self.size.width
        room = max(width - help_badge.cell_len - 2, 0)
        line = self._hints.copy()
        line.truncate(room, overflow="ellipsis", pad=True)
        line.append("  ")
        line.append_text(help_badge)
        return line

    def show_for(
        self,
        registry: Registry,
        ctx: SelectionContext,
        extra: list[tuple[str, str]],
        *,
        screen_only: bool = False,
    ) -> None:
        t = Text(no_wrap=True, overflow="ellipsis")
        # this window's keys first, app-wide ones (refresh, quit, tool windows) after, so a
        # narrow bar cuts off the ones you know rather than the ones you're looking for
        local: list[tuple[str, str]] = []
        app_wide: list[tuple[str, str]] = []
        for action in registry.available(ctx):
            if action.id == HELP_ACTION or (screen_only and action.screens is None):
                continue
            keys = registry.keys_for(action)
            if keys:
                app_level = action.screens is None and Scope.GLOBAL in action.scopes
                (app_wide if app_level else local).append((keys[0], action.label_for(ctx)))
        items = [*local, *extra, *app_wide]
        for i, (key, label) in enumerate(items):
            if i:
                t.append("  ")
            t.append(pretty_key(key), style="bold")
            t.append(f" {label}")
        self.update(t)
