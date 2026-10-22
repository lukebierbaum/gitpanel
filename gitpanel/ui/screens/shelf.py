"""Shelf tool window: shelves and stashes with their changes."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from rich.text import Text
from textual import on, work
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import OptionList
from textual.widgets.option_list import Option

from ...actions.context import Scope, SelectionContext
from ...git import patch as P
from ...git import shelf as SH
from ...git import stash as ST
from ...git.models import StashEntry
from ..widgets.diff_view import DiffView

if TYPE_CHECKING:
    from ..app import GitPanelApp

PURPLE = "#A66BE0"


class ShelfView(Vertical):
    name_id = "shelf"
    stale = True

    DEFAULT_CSS = """
    ShelfView { height: 1fr; }
    ShelfView #shelf-left { width: 40%; min-width: 30; border-right: tall $panel; }
    ShelfView #shelf-list { height: 1fr; background: $surface; border: none; }
    ShelfView #shelf-files { height: 40%; background: $surface; border: none;
                             border-top: tall $panel; }
    ShelfView DiffView { height: 1fr; }
    """

    def __init__(self) -> None:
        super().__init__(id="shelf-view")
        self.entries: dict[str, SH.Shelf | StashEntry] = {}
        self.diffs: list[P.FileDiff] = []

    @property
    def gp(self) -> GitPanelApp:
        return self.app  # type: ignore[return-value]

    def compose(self) -> ComposeResult:
        with Horizontal():
            with Vertical(id="shelf-left"):
                yield OptionList(id="shelf-list")
                yield OptionList(id="shelf-files")
            yield DiffView(id="shelf-diff")

    def focus_default(self) -> None:
        self.query_one("#shelf-list", OptionList).focus()

    @work(exclusive=True, group="shelf-load")
    async def reload(self) -> None:
        session = self.gp.session
        if session is None:
            return
        ol = self.query_one("#shelf-list", OptionList)
        prev = ol.highlighted_option.id if ol.highlighted_option else None
        ol.clear_options()
        self.entries.clear()
        shelves = SH.list_shelves(session)
        stashes = await ST.stashes(session.git)
        ol.add_option(Option(Text("Shelf", style=f"bold {PURPLE}"), disabled=True))
        if not shelves:
            ol.add_option(
                Option(
                    Text("  nothing shelved — alt+s in the Commit window", style="dim"),
                    disabled=True,
                )
            )
        for s in shelves:
            oid = f"shelf:{s.id}"
            self.entries[oid] = s
            t = Text(f"  {s.name}")
            t.append(
                f"  {len(s.paths)} file(s) · {s.created.astimezone():%Y-%m-%d %H:%M}", style="dim"
            )
            ol.add_option(Option(t, id=oid))
        ol.add_option(Option(Text("Stashes", style=f"bold {PURPLE}"), disabled=True))
        if not stashes:
            ol.add_option(
                Option(
                    Text("  no stashes — alt+t in the Commit window", style="dim"), disabled=True
                )
            )
        for st in stashes:
            oid = f"stash:{st.sha}"
            self.entries[oid] = st
            t = Text(f"  {st.ref}  ")
            t.append(st.message, style="dim")
            ol.add_option(Option(t, id=oid))
        target = prev if prev in self.entries else next(iter(self.entries), None)
        if target:
            ol.highlighted = ol.get_option_index(target)
        else:
            self.show_entry()

    def selected(self) -> SH.Shelf | StashEntry | None:
        ol = self.query_one("#shelf-list", OptionList)
        opt = ol.highlighted_option
        return self.entries.get(opt.id) if opt and opt.id else None

    @on(OptionList.OptionHighlighted, "#shelf-list")
    def _entry(self) -> None:
        self.show_entry()
        self.gp.refresh_hints()

    @work(exclusive=True, group="shelf-entry")
    async def show_entry(self) -> None:
        files = self.query_one("#shelf-files", OptionList)
        view = self.query_one(DiffView)
        entry = self.selected()
        files.clear_options()
        self.diffs = []
        if entry is None or self.gp.session is None:
            view.set_diff(None, None)
            return
        git = self.gp.session.git
        if isinstance(entry, SH.Shelf):
            self.diffs = P.parse_diff(entry.patch())
            source: P.DiffSource = P.WorkingTree()
        else:
            self.diffs = await P.load_diff(git, P.CommitDiff(entry.sha))
            untracked = await git.try_rev_parse(f"{entry.sha}^3")
            if untracked:
                self.diffs += await P.load_diff(git, P.CommitDiff(untracked))
            source = P.CommitDiff(entry.sha)
        for d in self.diffs:
            files.add_option(Option(f"{d.status} {d.path}", id=d.path))
        if self.diffs:
            files.highlighted = 0
            view.set_diff(source, self.diffs[0])
        else:
            view.set_diff(None, None, message="Empty")

    @on(OptionList.OptionHighlighted, "#shelf-files")
    def _file(self, event: OptionList.OptionHighlighted) -> None:
        diff = next((d for d in self.diffs if d.path == event.option.id), None)
        view = self.query_one(DiffView)
        view.set_diff(view.source or P.WorkingTree(), diff)

    def context(self, base: SelectionContext) -> SelectionContext:
        entry = self.selected()
        kwargs: dict[str, Any] = {"screen": "shelf"}
        files_list = self.query_one("#shelf-files", OptionList)
        if isinstance(entry, SH.Shelf):
            form: dict[str, Any] = {"shelf": entry}
            if self.screen.focused is files_list and files_list.highlighted is not None:
                opt = files_list.get_option_at_index(files_list.highlighted)
                form["shelf_paths"] = [opt.id]
            kwargs.update(focus=Scope.SHELF, form=form)
        elif isinstance(entry, StashEntry):
            kwargs.update(focus=Scope.STASH, stash=entry)
        return SelectionContext(**{**base.__dict__, **kwargs})

    def hint_extras(self) -> list[tuple[str, str]]:
        return []

    def handle_event(self, event: str, data: dict[str, Any]) -> None:
        if event == "shelf.refresh":
            self.reload()
