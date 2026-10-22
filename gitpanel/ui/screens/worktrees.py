"""Worktrees tool window: every worktree of the repository with its state."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from rich.text import Text
from textual import on, work
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import DataTable, Static

from ...actions.context import Scope, SelectionContext
from ...git import worktrees as W
from ...git.session import Worktree

if TYPE_CHECKING:
    from ..app import GitPanelApp

GREEN = "#6CCB3A"
PURPLE = "#A66BE0"


class WorktreesView(Vertical):
    name_id = "worktrees"
    stale = True

    DEFAULT_CSS = """
    WorktreesView { height: 1fr; }
    WorktreesView DataTable { height: 1fr; background: $surface; }
    WorktreesView #wt-details {
        height: auto; min-height: 5; padding: 1 2; border-top: tall $panel;
    }
    """

    def __init__(self) -> None:
        super().__init__(id="worktrees-view")
        self.items: list[tuple[Worktree, W.WorktreeSummary]] = []

    @property
    def gp(self) -> GitPanelApp:
        return self.app  # type: ignore[return-value]

    def compose(self) -> ComposeResult:
        table: DataTable[Text | str] = DataTable(
            id="wt-table", cursor_type="row", zebra_stripes=True
        )
        yield table
        yield Static("", id="wt-details")

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        for label, width in (
            ("", 2),
            ("Worktree", 22),
            ("Branch", 26),
            ("Changes", 16),
            ("↑↓", 9),
            ("State", 16),
            ("Path", None),
        ):
            table.add_column(label, width=width)

    def focus_default(self) -> None:
        self.query_one(DataTable).focus()

    @work(exclusive=True, group="worktrees-load")
    async def reload(self) -> None:
        session = self.gp.session
        if session is None:
            return
        wts = await session.worktrees()
        summaries = await asyncio.gather(*(W.summary(w) for w in wts))
        self.items = list(zip(wts, summaries, strict=True))
        table = self.query_one(DataTable)
        prev = table.cursor_row
        table.clear()
        active = session.active.path.resolve()
        for wt, s in self.items:
            is_active = wt.path.resolve() == active
            changes = Text()
            if s.missing:
                changes.append("missing", style="red")
            elif s.dirty:
                if s.conflicts:
                    changes.append(f"{s.conflicts}! ", style="bold red")
                if s.staged:
                    changes.append(f"{s.staged}● ", style=GREEN)
                if s.unstaged:
                    changes.append(f"{s.unstaged}✚ ", style=PURPLE)
                if s.untracked:
                    changes.append(f"{s.untracked}? ", style="#E3B23C")
            else:
                changes.append("clean", style="dim")
            state = []
            if wt.is_main:
                state.append("main")
            if wt.locked is not None:
                state.append("🔒 locked")
            if wt.prunable is not None:
                state.append("prunable")
            if s.operation is not None:
                state.append(s.operation.kind.value)
            branch = Text(
                wt.branch_short or f"detached {wt.head[:8] if wt.head else ''}",
                style=f"bold {GREEN}" if is_active else "",
            )
            table.add_row(
                Text("●", style=GREEN) if is_active else "",
                Text(wt.name, style="bold" if is_active else ""),
                branch,
                changes,
                f"↑{s.ahead} ↓{s.behind}" if s.upstream else "",
                ", ".join(state),
                str(wt.path),
                key=str(wt.path),
            )
        if self.items:
            table.move_cursor(row=min(max(prev, 0), len(self.items) - 1))
        self.show_details()

    def selected(self) -> Worktree | None:
        table = self.query_one(DataTable)
        if not self.items or table.cursor_row < 0 or table.cursor_row >= len(self.items):
            return None
        return self.items[table.cursor_row][0]

    @on(DataTable.RowHighlighted)
    def _row(self) -> None:
        self.show_details()
        self.gp.refresh_hints()

    def show_details(self) -> None:
        details = self.query_one("#wt-details", Static)
        table = self.query_one(DataTable)
        if not self.items or table.cursor_row >= len(self.items):
            details.update("")
            return
        wt, s = self.items[table.cursor_row]
        t = Text()
        t.append(f"{wt.path}\n", style="bold")
        t.append(f"HEAD {wt.head[:10] if wt.head else '—'}", style=GREEN)
        if wt.branch_short:
            t.append(f" on {wt.branch_short}")
        if s.upstream:
            t.append(f"  tracking {s.upstream} (↑{s.ahead} ↓{s.behind})", style="dim")
        t.append("\n")
        if s.dirty:
            t.append(
                f"{s.staged} staged · {s.unstaged} modified · {s.untracked} untracked"
                + (f" · {s.conflicts} conflicts" if s.conflicts else "")
                + "\n"
            )
        if s.operation:
            t.append(s.operation.label + "\n", style="#E3B23C")
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
        return SelectionContext(**{**base.__dict__, **kwargs})

    def hint_extras(self) -> list[tuple[str, str]]:
        return []

    def handle_event(self, event: str, data: dict[str, Any]) -> None:
        if event == "worktrees.refresh":
            self.reload()
