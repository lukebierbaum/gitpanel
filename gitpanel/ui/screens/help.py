"""Key map: every action with its key, grouped; ones available right now are highlighted."""

from __future__ import annotations

from rich.text import Text
from textual import on
from textual.binding import Binding
from textual.widgets import Input, OptionList
from textual.widgets.option_list import Option

from ...actions.context import SelectionContext
from ..base import FilterPopup
from ..colors import C
from ..widgets.key_hints import pretty_key


class HelpScreen(FilterPopup):
    POPUP_TITLE = (
        "Keys  ·  bold = available here  ·  remap in ~/.config/gitpanel/config.toml [keys]"
    )
    PLACEHOLDER = "Filter actions"
    DEFAULT_CSS = """
    HelpScreen { align: center middle; }
    HelpScreen .fp-box { width: 110; height: 90%; margin-top: 0; }
    """
    BINDINGS = [Binding("escape,question_mark", "dismiss", "Close")]

    def __init__(self, ctx: SelectionContext) -> None:
        super().__init__()
        self.ctx = ctx

    def on_mount(self) -> None:
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
            ol.add_option(Option(Text(group, style=f"bold {C.purple}"), disabled=True))
            for a in sorted(groups[group], key=lambda a: a.label):
                shown = " / ".join(pretty_key(k) for k in registry.keys_for(a))
                t = Text()
                t.append(f"  {shown:<14}", style=f"bold {C.green}")
                t.append(a.label, style="bold" if a.id in available else "dim")
                t.append(f"   {a.id}", style="dim italic")
                if a.screens:
                    t.append(f"   ({', '.join(sorted(a.screens))})", style="dim")
                ol.add_option(Option(t, id=a.id))
