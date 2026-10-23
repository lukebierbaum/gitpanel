"""Base classes for gitpanel's windows and screens.

- `GpMixin`: `self.gp` (the app, typed) and `self.git` for anything inside the app.
- `ToolWindow`: the tool windows behind the digit keys (Commit, Log, Worktrees …). The app
  shows one at a time and talks to it through `focus_default` / `reload` / `context` /
  `hint_extras` / `handle_event`; the defaults here make each of those optional.
- `FilterPopup`: a centred box with a title, a search box and a list (Branches, Help);
  arrow keys move the list while you type.
- `ToolScreen`: full screens opened on top (History, Annotate, Operation history …):
  `escape` closes, and the hint bar shows the screen's own actions plus `HINT_EXTRAS`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

from textual import events
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen, Screen
from textual.widgets import Input, Label, OptionList

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


class FilterPopup(GpMixin, ModalScreen[None]):
    POPUP_TITLE: ClassVar[str] = ""
    PLACEHOLDER: ClassVar[str] = "Filter"
    DEFAULT_CSS = """
    FilterPopup { align: center top; background: $background 40%; }
    FilterPopup .fp-box {
        width: 100; max-width: 95%; height: 85%; margin-top: 2;
        border: thick $primary; background: $panel; padding: 0 1;
    }
    FilterPopup .fp-title { text-style: bold; color: $accent; }
    FilterPopup .fp-search { margin: 1 0 0 0; }
    FilterPopup .fp-list { height: 1fr; background: $panel; border: none; }
    FilterPopup KeyHints { background: $panel; }
    """
    BINDINGS: ClassVar[list[Binding]] = [Binding("escape", "dismiss", "Close")]

    def compose(self) -> ComposeResult:
        with Vertical(classes="fp-box"):
            yield Label(self.POPUP_TITLE, classes="fp-title")
            yield Input(placeholder=self.PLACEHOLDER, classes="fp-search")
            yield OptionList(classes="fp-list")
            yield from self.compose_footer()

    def compose_footer(self) -> ComposeResult:
        """Extra widgets under the list (e.g. a KeyHints bar)."""
        yield from ()

    def on_mount(self) -> None:
        self.search.focus()

    @property
    def search(self) -> Input:
        return self.query_one(".fp-search", Input)

    @property
    def option_list(self) -> OptionList:
        return self.query_one(".fp-list", OptionList)

    def on_key(self, event: events.Key) -> None:
        # typing goes to the search box; the arrow keys still move the list
        if event.key in ("up", "down", "pageup", "pagedown") and isinstance(self.focused, Input):
            ol = self.option_list
            {
                "up": ol.action_cursor_up,
                "down": ol.action_cursor_down,
                "pageup": ol.action_page_up,
                "pagedown": ol.action_page_down,
            }[event.key]()
            event.stop()
            event.prevent_default()
