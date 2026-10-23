"""PyCharm-style Branches popup: search, Recent / Local / Remote / Tags, favourites."""

from __future__ import annotations

from typing import Any

from rich.text import Text
from textual import events, on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, Label, OptionList
from textual.widgets.option_list import Option

from ...actions.context import Scope, SelectionContext
from ...git import refs as R
from ...git import tags as T
from ...git.models import Ref
from ..base import GpMixin, ScreenHints
from ..colors import C
from ..widgets.key_hints import KeyHints


def _matches(query: str, name: str) -> bool:
    """Case-insensitive subsequence match (PyCharm's speed search)."""
    if not query:
        return True
    name = name.lower()
    pos = 0
    for ch in query.lower():
        pos = name.find(ch, pos)
        if pos < 0:
            return False
        pos += 1
    return True


class BranchesPopup(GpMixin, ScreenHints, ModalScreen[None]):
    DEFAULT_CSS = """
    BranchesPopup { align: center top; background: $background 40%; }
    BranchesPopup #branches-box {
        width: 96; max-width: 95%; height: 80%; margin-top: 2;
        border: thick $primary; background: $panel; padding: 0 1;
    }
    BranchesPopup #branches-title { text-style: bold; color: $accent; }
    BranchesPopup #branch-search { margin: 1 0 0 0; }
    BranchesPopup #branch-list { height: 1fr; background: $panel; border: none; }
    BranchesPopup KeyHints { background: $panel; }
    """
    BINDINGS = [Binding("escape", "dismiss", "Close")]
    # only its own actions in the hint bar (plain letters go to the search box)
    HINT_EXTRAS = [("alt+enter", "more actions"), ("esc", "close")]

    def __init__(self) -> None:
        super().__init__()
        self._refs: dict[str, Any] = {}
        self._data: dict[str, Any] = {}

    def compose(self) -> ComposeResult:
        with Vertical(id="branches-box"):
            yield Label("Git Branches", id="branches-title")
            yield Input(placeholder="Search branches and tags", id="branch-search")
            yield OptionList(id="branch-list")
            yield KeyHints(id="branch-hints")

    def on_mount(self) -> None:
        self.query_one(Input).focus()
        self.reload()

    # -- data -----------------------------------------------------------------------
    @work(exclusive=True, group="branches-load")
    async def reload(self) -> None:
        session = self.gp.session
        if session is None:
            return
        git = session.git
        self._data = {
            "branches": await R.branches(git),
            "tags": await T.tags(git),
            "recent": await R.recent_branches(git),
            "favorites": R.Favorites(session.private_dir(shared=True) / "favorites.json").load(),
            "current": session.active.branch,
            "active": session.active.path.resolve(),
        }
        self.rebuild()

    def rebuild(self) -> None:
        if not self._data or not self.query(OptionList):
            return
        query = self.query_one(Input).value.strip()
        ol = self.query_one(OptionList)
        prev = ol.highlighted_option.id if ol.highlighted_option else None
        ol.clear_options()
        self._refs.clear()
        branches = self._data["branches"]
        favs = self._data["favorites"]
        by_short = {b.short: b for b in branches if b.is_local}
        local = sorted(
            (b for b in branches if b.is_local), key=lambda b: (b.name not in favs, b.short.lower())
        )
        remote = sorted(
            (b for b in branches if b.is_remote),
            key=lambda b: (b.name not in favs, b.short.lower()),
        )
        recent = [by_short[n] for n in self._data["recent"] if n in by_short]
        sections: list[tuple[str, list[Any]]] = [
            ("Recent", recent if not query else []),
            ("Local", local),
            ("Remote", remote),
            ("Tags", self._data["tags"]),
        ]
        first_selectable: str | None = None
        for title, items in sections:
            shown = [r for r in items if _matches(query, r.short)]
            if not shown:
                continue
            ol.add_option(Option(Text(title, style=f"bold {C.purple}"), disabled=True))
            for ref in shown:
                oid = f"{title}:{ref.name}"
                self._refs[oid] = ref
                ol.add_option(Option(self._label(ref), id=oid))
                first_selectable = first_selectable or oid
        target = prev if prev in self._refs else first_selectable
        if target is not None:
            ol.highlighted = ol.get_option_index(target)
        if self.gp.session is not None:
            self.show_hints(
                self.gp.registry,
                self.context(SelectionContext(self.gp.session, operation=self.gp.operation)),
            )

    def _label(self, ref: Any) -> Text:
        t = Text()
        favs = self._data.get("favorites", set())
        is_current = ref.name == self._data.get("current")
        t.append("★ " if ref.name in favs else "  ", style=C.amber)
        t.append("● " if is_current else "  ", style=C.green)
        t.append(ref.short, style=f"bold {C.green}" if is_current else "")
        if isinstance(ref, Ref):  # tag
            return t
        wt = ref.worktree
        if wt is not None and wt.resolve() != self._data.get("active"):
            t.append(f"  ⎇ {wt.name}", style=f"italic {C.teal}")
        if ref.ahead or ref.behind:
            t.append(f"  ↑{ref.ahead} ↓{ref.behind}", style=C.purple)
        if ref.gone:
            t.append("  upstream gone", style="dim italic")
        elif ref.upstream_short:
            t.append(f"  → {ref.upstream_short}", style="dim")
        return t

    def selected_ref(self) -> Any | None:
        found = self.query(OptionList)
        if not found:  # still composing
            return None
        opt = found.first().highlighted_option
        return self._refs.get(opt.id) if opt and opt.id else None

    # -- interaction --------------------------------------------------------------------
    @on(Input.Changed, "#branch-search")
    def _search(self) -> None:
        self.rebuild()

    @on(OptionList.OptionHighlighted)
    def _highlighted(self) -> None:
        self.refresh_hints()

    @on(OptionList.OptionSelected)
    def _selected(self) -> None:
        self.gp.dispatch_id("branch.checkout")

    def on_key(self, event: events.Key) -> None:
        if event.key in ("up", "down", "pageup", "pagedown") and isinstance(self.focused, Input):
            ol = self.query_one(OptionList)
            action = {
                "up": ol.action_cursor_up,
                "down": ol.action_cursor_down,
                "pageup": ol.action_page_up,
                "pagedown": ol.action_page_down,
            }[event.key]
            action()
            event.stop()
            event.prevent_default()

    # -- context ----------------------------------------------------------------------------
    def context(self, base: SelectionContext) -> SelectionContext:
        ref = self.selected_ref()
        kwargs: dict[str, Any] = {"screen": "branches"}
        if ref is not None:
            kwargs.update(focus=Scope.BRANCH, refs=(ref,))
        return base.with_(**kwargs)

    def refresh_hints(self) -> None:
        if self.gp.session is not None and self.is_current:
            self.gp.refresh_hints()
