"""Command palette entries for every action available in the current context."""

from __future__ import annotations

from functools import partial
from typing import TYPE_CHECKING

from textual.command import DiscoveryHit, Hit, Hits, Provider

from .widgets.key_hints import pretty_key

if TYPE_CHECKING:
    from .app import GitPanelApp


class RegistryCommands(Provider):
    def _items(self):  # type: ignore[no-untyped-def]
        app: GitPanelApp = self.app  # type: ignore[assignment]
        if app.session is None:
            return []
        ctx = app.context()
        out = []
        for action in app.registry.available(ctx):
            if not action.show_in_palette:
                continue
            key = app.registry.key_for(action)
            help_text = f"{action.group} · {pretty_key(key)}" if key else action.group
            out.append((action, ctx, help_text))
        return out

    async def discover(self) -> Hits:
        app: GitPanelApp = self.app  # type: ignore[assignment]
        for action, ctx, help_text in self._items():
            yield DiscoveryHit(
                action.label, partial(app.run_action_in_ctx, action, ctx), help=help_text
            )

    async def search(self, query: str) -> Hits:
        app: GitPanelApp = self.app  # type: ignore[assignment]
        matcher = self.matcher(query)
        for action, ctx, help_text in self._items():
            score = matcher.match(action.label)
            if score > 0:
                yield Hit(
                    score,
                    matcher.highlight(action.label),
                    partial(app.run_action_in_ctx, action, ctx),
                    help=help_text,
                )
