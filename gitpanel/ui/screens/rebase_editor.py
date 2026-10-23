"""Interactive rebase editor (PyCharm's "Interactively Rebase from Here")."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from rich.text import Text
from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import DataTable, Static

from ...actions.context import SelectionContext
from ...actions.registry import Registry
from ...git import scripted_rebase as SR
from ...git.log import commit_details
from ..colors import C
from ..widgets.key_hints import KeyHints

if TYPE_CHECKING:
    from ..app import GitPanelApp

ACTION_STYLE = {  # colour names
    "pick": "green",
    "reword": "blue",
    "edit": "amber",
    "squash": "purple",
    "fixup": "purple",
    "drop": "red",
}


class RebaseEditorScreen(Screen[None]):
    DEFAULT_CSS = """
    RebaseEditorScreen { layout: vertical; }
    RebaseEditorScreen #rb-header { height: auto; padding: 0 1; background: $panel; }
    RebaseEditorScreen DataTable { height: 1fr; background: $surface; }
    """
    BINDINGS = [
        Binding("p", "set('pick')", "Pick"),
        Binding("r", "reword", "Reword"),
        Binding("e", "set('edit')", "Edit"),
        Binding("s", "set('squash')", "Squash"),
        Binding("f", "set('fixup')", "Fixup"),
        Binding("d", "toggle_drop", "Drop"),
        Binding("alt+up,K", "move(-1)", "Move up"),
        Binding("alt+down,J", "move(1)", "Move down"),
        Binding("escape", "cancel", "Cancel"),
    ]

    def __init__(self, base: str | None, todo: list[SR.TodoItem]) -> None:
        super().__init__()
        self.base = base
        self.todo = [SR.TodoItem(t.action, t.sha, t.subject, t.arg) for t in todo]
        self.messages: dict[str, str] = {}

    @property
    def gp(self) -> GitPanelApp:
        return self.app  # type: ignore[return-value]

    def compose(self) -> ComposeResult:
        yield Static("", id="rb-header")
        yield DataTable(id="rb-table", cursor_type="row")
        yield KeyHints(id="rb-hints")

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        table.add_column("Action", width=8)
        table.add_column("Commit", width=10)
        table.add_column("Subject")
        self.render_table()
        table.focus()

    def render_table(self, cursor: int | None = None) -> None:
        table = self.query_one(DataTable)
        row = table.cursor_row if cursor is None else cursor
        table.clear()
        for item in self.todo:
            style = C[ACTION_STYLE.get(item.action, "")]
            subject = self.messages.get(item.sha or "", item.subject).split("\n", 1)[0]
            subj = Text(("    ↳ " if item.action in ("squash", "fixup") else "") + subject)
            if item.action == "drop":
                subj.stylize("strike dim")
            if item.sha in self.messages:
                subj.append("  (new message)", style=f"italic {C.blue}")
            table.add_row(Text(item.action, style=f"bold {style}"), (item.sha or "")[:8], subj)
        if self.todo:
            table.move_cursor(row=max(0, min(row, len(self.todo) - 1)))
        branch = self.gp.session.active.branch_short if self.gp.session else "HEAD"
        header = Text()
        header.append(f"Rebasing {len(self.todo)} commit(s) of {branch}", style="bold")
        header.append(f" onto {self.base[:8] if self.base else 'root'}", style="dim")
        header.append(
            "   oldest first · p pick · r reword · e edit · s squash · f fixup · d drop"
            " · alt+↑↓ move · ctrl+s start · esc cancel",
            style="dim",
        )
        try:
            SR.validate(self.todo)
        except SR.RebaseError as e:
            header.append(f"\n⚠ {e}", style=C.red)
        self.query_one("#rb-header", Static).update(header)
        self.gp.refresh_hints()

    def _row(self) -> int:
        return self.query_one(DataTable).cursor_row

    def action_set(self, action: str) -> None:
        if self.todo:
            self.todo[self._row()].action = action  # type: ignore[assignment]
            self.render_table()

    def action_toggle_drop(self) -> None:
        if self.todo:
            item = self.todo[self._row()]
            item.action = "pick" if item.action == "drop" else "drop"
            self.render_table()

    def action_move(self, delta: int) -> None:
        i = self._row()
        j = i + delta
        if 0 <= j < len(self.todo):
            self.todo[i], self.todo[j] = self.todo[j], self.todo[i]
            self.render_table(cursor=j)

    @work
    async def action_reword(self) -> None:
        if not self.todo or self.gp.session is None:
            return
        item = self.todo[self._row()]
        current = self.messages.get(item.sha or "")
        if current is None:
            current = (await commit_details(self.gp.session.git, item.sha or "")).body
        msg = await self.gp.prompt(
            f"Message for {item.sha[:8] if item.sha else ''}", default=current, multiline=True
        )
        if msg is None:
            return
        self.messages[item.sha or ""] = msg
        item.action = "reword" if item.action == "pick" else item.action
        self.render_table()

    def action_cancel(self) -> None:
        self.dismiss()

    @on(DataTable.RowHighlighted)
    def _row_changed(self) -> None:
        self.gp.refresh_hints()

    def handle_event(self, event: str, data: dict[str, Any]) -> None:
        if event == "rebase.done":
            self.dismiss()

    # -- registry integration --------------------------------------------------------------
    def context(self, base: SelectionContext) -> SelectionContext:
        messages = dict(self.messages)
        # a squash group's combined message is asked for at its last squash commit
        for i, item in enumerate(self.todo):
            if item.action == "squash" and item.sha in messages:
                j = i
                while j + 1 < len(self.todo) and self.todo[j + 1].action in ("squash", "fixup"):
                    j += 1
                last = self.todo[j].sha or ""
                if last != item.sha:
                    messages[last] = messages.pop(item.sha or "")
        form = {"todo": tuple(self.todo), "base": self.base, "messages": messages}
        return SelectionContext(**{**base.__dict__, "screen": "rebase", "form": form})

    def show_hints(self, registry: Registry, ctx: SelectionContext) -> None:
        hints = self.query(KeyHints)
        if hints:
            hints.first().show_for(
                registry,
                ctx,
                [("p r e s f d", "set action"), ("alt+↑↓", "move"), ("esc", "cancel")],
                screen_only=True,
            )
