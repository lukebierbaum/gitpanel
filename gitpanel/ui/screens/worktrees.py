"""Worktrees tool window: every worktree of the repository with its state."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from rich.text import Text
from textual import events, on, work
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import DataTable, Input, Static

from ...actions.context import Scope, SelectionContext
from ...git import worktrees as W
from ...git.session import Worktree

if TYPE_CHECKING:
    from ..app import GitPanelApp

GREEN = "#6CCB3A"
PURPLE = "#A66BE0"


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


class WorktreesView(Vertical):
    name_id = "worktrees"
    stale = True

    DEFAULT_CSS = """
    WorktreesView { height: 1fr; }
    WorktreesView #wt-filter { margin: 0 1; }
    WorktreesView DataTable { height: 1fr; background: $surface; }
    WorktreesView #wt-details {
        height: auto; min-height: 5; padding: 1 2; border-top: tall $panel;
    }
    """

    def __init__(self) -> None:
        super().__init__(id="worktrees-view")
        self.all_items: list[tuple[Worktree, W.WorktreeSummary]] = []
        self.items: list[tuple[Worktree, W.WorktreeSummary]] = []  # the ones shown (filtered)

    @property
    def gp(self) -> GitPanelApp:
        return self.app  # type: ignore[return-value]

    def compose(self) -> ComposeResult:
        yield Input(
            placeholder="/ Filter worktrees by name, branch, path or state (dirty, locked …)",
            id="wt-filter",
            compact=True,
        )
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
        self.all_items = list(zip(wts, summaries, strict=True))
        self._render_rows()

    def _render_rows(self) -> None:
        session = self.gp.session
        if session is None:
            return
        table = self.query_one(DataTable)
        prev_path = self.selected().path if self.selected() is not None else None
        prev = table.cursor_row
        words = self.query_one("#wt-filter", Input).value.lower().split()
        self.items = [
            (wt, s) for wt, s in self.all_items if all(w in _haystack(wt, s) for w in words)
        ]
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
            row = next(
                (i for i, (wt, _) in enumerate(self.items) if wt.path == prev_path),
                min(max(prev, 0), len(self.items) - 1),
            )
            table.move_cursor(row=row)
        self.show_details()

    @on(Input.Changed, "#wt-filter")
    def _filter(self) -> None:
        self._render_rows()

    @on(Input.Submitted, "#wt-filter")
    def _filter_done(self) -> None:
        self.query_one(DataTable).focus()

    def on_key(self, event: events.Key) -> None:
        focused = self.screen.focused
        search = self.query_one("#wt-filter", Input)
        if event.key == "slash" and focused is self.query_one(DataTable):
            event.stop()
            event.prevent_default()
            search.focus()
        elif event.key == "escape" and focused is search:
            event.stop()
            event.prevent_default()
            search.value = ""
            self.query_one(DataTable).focus()

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
        if self.screen.focused is self.query_one(DataTable):
            return [("/", "filter")]
        return []

    def handle_event(self, event: str, data: dict[str, Any]) -> None:
        if event == "worktrees.refresh":
            self.reload()
