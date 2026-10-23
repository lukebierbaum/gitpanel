"""Worktrees tool window: every worktree of the repository with its state."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from rich.text import Text
from textual import on, work
from textual.app import ComposeResult
from textual.widgets import DataTable, Static

from ...actions.context import Scope, SelectionContext
from ...git import worktrees as W
from ...git.session import Worktree
from ..base import ToolWindow
from ..colors import C
from ..fmt import ahead_behind, branch_or_detached
from ..widgets.list_table import Col, ListTable, words_match


def _haystack(wt: Worktree, s: W.WorktreeSummary) -> str:
    """What the filter matches: name, branch, path, HEAD and state words."""
    words = [wt.name, wt.branch_short or "detached", str(wt.path), wt.head or ""]
    words.append("dirty" if s.dirty else "clean")
    if s.missing:
        words.append("missing")
    if s.conflicts:
        words.append("conflicts")
    if wt.is_main:
        words.append("main")
    if wt.locked is not None:
        words.append(f"locked {wt.locked}")
    if wt.prunable is not None:
        words.append("prunable")
    if s.operation is not None:
        words.append(s.operation.kind.value)
    return " ".join(words).lower()


Item = tuple[Worktree, W.WorktreeSummary]


class WorktreesView(ToolWindow):
    name_id = "worktrees"
    stale = True

    DEFAULT_CSS = """
    WorktreesView { height: 1fr; }
    WorktreesView #wt-details {
        height: auto; min-height: 5; padding: 1 2; border-top: tall $panel;
    }
    """

    def __init__(self) -> None:
        super().__init__(id="worktrees-view")
        self._active: Path | None = None

    def compose(self) -> ComposeResult:
        yield ListTable[Item](
            self._columns(),
            key=lambda item: str(item[0].path),
            matches=lambda item, text: words_match(text, _haystack(*item)),
            placeholder="/ Filter worktrees by name, branch, path or state (dirty, locked …)",
            empty=lambda text: f"No worktrees match “{text}”." if text else "",
            state_key="worktrees.list",
            table_id="wt-table",
        )
        yield Static("", id="wt-details")

    @property
    def listing(self) -> ListTable[Item]:
        return self.query_one(ListTable)

    def _is_active(self, wt: Worktree) -> bool:
        return self._active is not None and wt.path.resolve() == self._active

    def _columns(self) -> list[Col[Item]]:
        def changes(item: Item) -> Text:
            _, s = item
            t = Text()
            if s.missing:
                t.append("missing", style="red")
            elif s.dirty:
                if s.conflicts:
                    t.append(f"{s.conflicts}! ", style="bold red")
                if s.staged:
                    t.append(f"{s.staged}● ", style=C.green)
                if s.unstaged:
                    t.append(f"{s.unstaged}✚ ", style=C.purple)
                if s.untracked:
                    t.append(f"{s.untracked}? ", style=C.amber)
            else:
                t.append("clean", style="dim")
            return t

        def state(item: Item) -> str:
            wt, s = item
            words = []
            if wt.is_main:
                words.append("main")
            if wt.locked is not None:
                words.append("🔒 locked")
            if wt.prunable is not None:
                words.append("prunable")
            if s.operation is not None:
                words.append(s.operation.kind.value)
            return ", ".join(words)

        def branch(item: Item) -> Text:
            wt, _ = item
            return Text(
                branch_or_detached(wt.branch_short, wt.head),
                style=f"bold {C.green}" if self._is_active(wt) else "",
            )

        def sync(item: Item) -> str:
            _, s = item
            return ahead_behind(s.ahead, s.behind) if s.upstream else ""

        def dirt(item: Item) -> int:
            _, s = item
            return s.conflicts * 1000 + s.staged + s.unstaged + s.untracked

        return [
            Col("active", "", 2,
                lambda item: Text("●", style=C.green) if self._is_active(item[0]) else ""),
            Col("name", "Worktree", 22,
                lambda item: Text(item[0].name, style="bold" if self._is_active(item[0]) else ""),
                sort=lambda item: item[0].name.casefold()),
            Col("branch", "Branch", 26, branch,
                sort=lambda item: (item[0].branch_short or "~").casefold()),
            Col("changes", "Changes", 16, changes, sort=dirt, descending_first=True),
            Col("sync", "↑↓", 9,
                sync,
                sort=lambda item: item[1].ahead + item[1].behind, descending_first=True),
            Col("state", "State", 16, state, sort=state),
            Col("path", "Path", None, lambda item: str(item[0].path),
                sort=lambda item: str(item[0].path)),
        ]  # fmt: skip

    def focus_default(self) -> None:
        self.query_one(DataTable).focus()

    @work(exclusive=True, group="worktrees-load")
    async def reload(self) -> None:
        session = self.gp.session
        if session is None:
            return
        wts = await session.worktrees()
        summaries = await asyncio.gather(*(W.summary(w) for w in wts))
        self._active = session.active.path.resolve()
        self.listing.set_items(list(zip(wts, summaries, strict=True)))
        self.show_details()

    def selected(self) -> Worktree | None:
        item = self.listing.selected
        return item[0] if item is not None else None

    @on(ListTable.Highlighted)
    def _row(self) -> None:
        self.show_details()
        self.gp.refresh_hints()

    def show_details(self) -> None:
        details = self.query_one("#wt-details", Static)
        item = self.listing.selected
        if item is None:
            details.update("")
            return
        wt, s = item
        t = Text()
        t.append(f"{wt.path}\n", style="bold")
        t.append(f"HEAD {wt.head[:10] if wt.head else '—'}", style=C.green)
        if wt.branch_short:
            t.append(f" on {wt.branch_short}")
        if s.upstream:
            t.append(f"  tracking {s.upstream} ({ahead_behind(s.ahead, s.behind)})", style="dim")
        t.append("\n")
        if s.dirty:
            t.append(
                f"{s.staged} staged · {s.unstaged} modified · {s.untracked} untracked"
                + (f" · {s.conflicts} conflicts" if s.conflicts else "")
                + "\n"
            )
        if s.operation:
            t.append(s.operation.label + "\n", style=C.amber)
        if wt.locked is not None:
            t.append(f"Locked{': ' + wt.locked if wt.locked else ''}\n", style="dim")
        if wt.prunable is not None:
            t.append(f"Prunable: {wt.prunable}\n", style="red")
        details.update(t)

    def context(self, base: SelectionContext) -> SelectionContext:
        focused = self.screen.focused
        wt = self.selected()
        kwargs: dict[str, Any] = {"screen": "worktrees", "worktree": wt}
        if focused is self.query_one(DataTable) and wt is not None:
            kwargs["focus"] = Scope.WORKTREE
        return base.with_(**kwargs)

    def hint_extras(self) -> list[tuple[str, str]]:
        return self.listing.hints()

    def handle_event(self, event: str, data: dict[str, Any]) -> None:
        if event == "worktrees.refresh":
            self.reload()
