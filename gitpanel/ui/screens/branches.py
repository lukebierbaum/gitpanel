"""PyCharm-style Branches popup: search, Recent / Local / Remote / Tags, favourites."""

from __future__ import annotations

from typing import Any

from rich.text import Text
from textual import on, work
from textual.app import ComposeResult
from textual.widgets import Input, OptionList
from textual.widgets.option_list import Option

from ...actions.context import Scope, SelectionContext
from ...git import refs as R
from ...git import tags as T
from ..base import FilterPopup, ScreenHints
from ..colors import C
from ..widgets.branch_label import branch_label, favorites_first, load_favorites
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


class BranchesPopup(ScreenHints, FilterPopup):
    POPUP_TITLE = "Git Branches"
    PLACEHOLDER = "Search branches and tags"
    DEFAULT_CSS = """
    BranchesPopup .fp-box { width: 96; height: 80%; }
    """
    # only its own actions in the hint bar (plain letters go to the search box)
    HINT_EXTRAS = [("alt+enter", "more actions"), ("esc", "close")]

    def __init__(self) -> None:
        super().__init__()
        self._refs: dict[str, Any] = {}
        self._data: dict[str, Any] = {}

    def compose_footer(self) -> ComposeResult:
        yield KeyHints(id="branch-hints")

    def on_mount(self) -> None:
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
            "favorites": load_favorites(session),
            "current": session.active.branch,
            "active": session.active.path.resolve(),
        }
        self._data["has_remotes"] = any(b.is_remote for b in self._data["branches"])
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
        fav_first = favorites_first(self._data["favorites"])
        by_short = {b.short: b for b in branches if b.is_local}
        local = sorted((b for b in branches if b.is_local), key=fav_first)
        remote = sorted(
            (b for b in branches if b.is_remote),
            key=fav_first,
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
        return branch_label(
            ref,
            current=self._data.get("current"),
            favorites=self._data.get("favorites", set()),
            active=self._data.get("active"),
            has_remotes=self._data.get("has_remotes", False),
        )

    def selected_ref(self) -> Any | None:
        found = self.query(OptionList)
        if not found:  # still composing
            return None
        opt = found.first().highlighted_option
        return self._refs.get(opt.id) if opt and opt.id else None

    # -- interaction --------------------------------------------------------------------
    @on(Input.Changed, ".fp-search")
    def _search(self) -> None:
        self.rebuild()

    @on(OptionList.OptionHighlighted)
    def _highlighted(self) -> None:
        self.refresh_hints()

    @on(OptionList.OptionSelected)
    def _selected(self) -> None:
        self.gp.dispatch_id("branch.checkout")

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
