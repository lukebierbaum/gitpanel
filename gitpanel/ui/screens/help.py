"""Key map: every action with its key, grouped; ones available right now are highlighted."""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, Label, OptionList
from textual.widgets.option_list import Option

from ...actions.context import SelectionContext
from ..widgets.key_hints import pretty_key

if TYPE_CHECKING:
    from ..app import GitPanelApp


class HelpScreen(ModalScreen[None]):
    DEFAULT_CSS = """
    HelpScreen { align: center middle; }
    HelpScreen #help-box { width: 110; max-width: 95%; height: 90%; border: thick $primary;
                           background: $panel; padding: 0 1; }
    HelpScreen #help-title { text-style: bold; color: $accent; }
    HelpScreen OptionList { height: 1fr; background: $panel; border: none; }
    """
    BINDINGS = [Binding("escape,question_mark", "dismiss", "Close")]

    def __init__(self, ctx: SelectionContext) -> None:
        super().__init__()
        self.ctx = ctx

    @property
    def gp(self) -> GitPanelApp:
        return self.app  # type: ignore[return-value]

    def compose(self) -> ComposeResult:
        with Vertical(id="help-box"):
            yield Label(
                "Keys  ·  bold = available here  ·  remap in ~/.config/gitpanel/config.toml [keys]",
                id="help-title",
            )
            yield Input(placeholder="Filter actions", id="help-filter")
            yield OptionList(id="help-list")

    def on_mount(self) -> None:
        self.query_one(Input).focus()
        self.rebuild()

    @on(Input.Changed)
    def rebuild(self) -> None:
        registry = self.gp.registry
        query = self.query_one(Input).value.lower().strip()
        available = {a.id for a in registry.available(self.ctx)}
        groups: dict[str, list] = {}  # type: ignore[type-arg]
        for a in registry.all():
            hay = f"{a.label} {a.id} {a.group} {registry.key_for(a) or ''}".lower()
            if query and query not in hay:
                continue
            groups.setdefault(a.group or "Other", []).append(a)
        ol = self.query_one(OptionList)
        ol.clear_options()
        for group in sorted(groups, key=lambda g: (g != "App", g)):
            ol.add_option(Option(Text(group, style="bold #A66BE0"), disabled=True))
            for a in sorted(groups[group], key=lambda a: a.label):
                shown = " / ".join(pretty_key(k) for k in registry.keys_for(a))
                t = Text()
                t.append(f"  {shown:<14}", style="bold #6CCB3A")
                t.append(a.label, style="bold" if a.id in available else "dim")
                t.append(f"   {a.id}", style="dim italic")
                if a.screens:
                    t.append(f"   ({', '.join(sorted(a.screens))})", style="dim")
                ol.add_option(Option(t, id=a.id))

    def on_key(self, event) -> None:  # type: ignore[no-untyped-def]
        if event.key in ("up", "down", "pageup", "pagedown") and isinstance(self.focused, Input):
            ol = self.query_one(OptionList)
            {
                "up": ol.action_cursor_up,
                "down": ol.action_cursor_down,
                "pageup": ol.action_page_up,
                "pagedown": ol.action_page_down,
            }[event.key]()
            event.stop()
