"""Base classes for gitpanel's windows and screens.

- `GpMixin`: `self.gp` (the app, typed) and `self.git` for anything inside the app.
- `ToolWindow`: the tool windows behind the digit keys (Commit, Log, Worktrees …). The app
  shows one at a time and talks to it through `focus_default` / `reload` / `context` /
  `hint_extras` / `handle_event`; the defaults here make each of those optional.
- `ToolScreen`: full screens opened on top (History, Annotate, Operation history …):
  `escape` closes, and the hint bar shows the screen's own actions plus `HINT_EXTRAS`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import Screen

from ..actions.context import SelectionContext
from ..actions.registry import Registry
from .widgets.key_hints import KeyHints

if TYPE_CHECKING:
    from ..git.runner import Git
    from .app import GitPanelApp


class GpMixin:
    @property
    def gp(self) -> GitPanelApp:
        return self.app  # type: ignore[attr-defined,no-any-return]

    @property
    def git(self) -> Git:
        """The active worktree's git (only valid while a repository is open)."""
        session = self.gp.session
        assert session is not None, "no repository open"
        return session.git


class ToolWindow(GpMixin, Vertical):
    name_id: ClassVar[str] = ""
    stale = True  # reload when next shown

    def focus_default(self) -> None:
        self.focus()

    def reload(self) -> Any:
        """Refresh from the repository (often a @work method)."""

    def context(self, base: SelectionContext) -> SelectionContext:
        return base.with_(screen=self.name_id)

    def hint_extras(self) -> list[tuple[str, str]]:
        return []

    def handle_event(self, event: str, data: dict[str, Any]) -> None:
        """App events (`ui.post(...)`) such as "worktrees.refresh"; ignored by default."""


class ScreenHints:
    """For screens with their own KeyHints bar: their actions plus `hint_extras()`."""

    HINT_EXTRAS: ClassVar[list[tuple[str, str]]] = [("esc", "close")]

    def hint_extras(self) -> list[tuple[str, str]]:
        return list(self.HINT_EXTRAS)

    def show_hints(self, registry: Registry, ctx: SelectionContext) -> None:
        hints = self.query(KeyHints)  # type: ignore[attr-defined]
        if hints:
            hints.first().show_for(registry, ctx, self.hint_extras(), screen_only=True)


class ToolScreen(GpMixin, ScreenHints, Screen[None]):
    BINDINGS: ClassVar[list[Binding]] = [Binding("escape", "dismiss", "Close")]
