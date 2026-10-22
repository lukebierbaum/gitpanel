"""Console tool window: the git commands gitpanel runs and what git answered."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.widgets import RichLog, Static

from ...actions.context import SelectionContext
from ...git.console import CONSOLE, ConsoleEntry

if TYPE_CHECKING:
    from ..app import GitPanelApp

GREEN = "#6CCB3A"
PURPLE = "#A66BE0"
RED = "#E5484D"
MAX_LINES_SHOWN = 40  # per stream, per entry


class ConsoleView(Vertical):
    name_id = "console"
    stale = False

    DEFAULT_CSS = """
    ConsoleView { height: 1fr; }
    ConsoleView #console-header { height: 1; padding: 0 1; background: $panel; }
    ConsoleView RichLog { height: 1fr; background: $surface; padding: 0 1; }
    """
    BINDINGS = [
        Binding("a", "toggle_queries", "Show all commands", show=False),
        Binding("o", "toggle_output", "Show output", show=False),
        Binding("c", "clear", "Clear", show=False),
    ]

    def __init__(self) -> None:
        super().__init__(id="console-view")
        self.show_queries = False
        self.show_output = True
        self._unsubscribe = None
        self._placeholder = False

    @property
    def gp(self) -> GitPanelApp:
        return self.app  # type: ignore[return-value]

    def compose(self) -> ComposeResult:
        yield Static("", id="console-header")
        yield RichLog(
            id="console-log",
            wrap=True,
            markup=False,
            highlight=False,
            max_lines=20000,
            auto_scroll=True,
        )

    def on_mount(self) -> None:
        self._unsubscribe = CONSOLE.subscribe(self._on_console)
        self.rebuild()

    def on_unmount(self) -> None:
        if self._unsubscribe:
            self._unsubscribe()

    def focus_default(self) -> None:
        self.query_one(RichLog).focus()

    def reload(self) -> None:  # tool-window contract; the console updates live
        self._update_header()

    # -- live updates ---------------------------------------------------------------
    def _on_console(self, event: str, entry: ConsoleEntry | None) -> None:
        if event == "clear":
            self.rebuild()
        elif event == "finish" and entry is not None and self._visible(entry):
            if self._placeholder:
                self.rebuild()  # drop the "no commands yet" line
            else:
                self.query_one(RichLog).write(self._format_entry(entry))
        self._update_header()

    def _visible(self, entry: ConsoleEntry) -> bool:
        return self.show_queries or not entry.read_only

    def rebuild(self) -> None:
        log = self.query_one(RichLog)
        log.clear()
        shown = [e for e in CONSOLE.entries if not e.running and self._visible(e)]
        self._placeholder = not shown
        if not shown:
            log.write(
                Text(
                    "No commands yet — anything gitpanel changes in the repository "
                    "appears here (a also shows read-only queries).",
                    style="dim",
                )
            )
        for entry in shown:
            log.write(self._format_entry(entry))
        self._update_header()

    def _update_header(self) -> None:
        running = [e for e in CONSOLE.entries if e.running]
        t = Text()
        t.append("Console", style=f"bold {GREEN}")
        t.append(f"   {len(CONSOLE.entries)} commands recorded", style="dim")
        if running:
            t.append(f"   running: {running[-1].command[:60]}", style="#E3B23C")
        t.append("   a ", style="bold")
        t.append("changes only" if self.show_queries else "all commands", style="dim")
        t.append("  o ", style="bold")
        t.append("hide output" if self.show_output else "show output", style="dim")
        t.append("  c ", style="bold")
        t.append("clear", style="dim")
        headers = self.query("#console-header")
        if headers:
            headers.first(Static).update(t)

    def _format_entry(self, e: ConsoleEntry) -> Text:
        t = Text()
        t.append(e.started.strftime("%H:%M:%S "), style="dim")
        if e.cancelled:
            mark, style = "⊘", "dim"
        elif e.ok:
            mark, style = "✓", GREEN
        else:
            mark, style = "✗", RED
        t.append(f"{mark} ", style=f"bold {style}")
        t.append("$ " if e.kind == "shell" else "", style="dim")
        t.append(e.command, style="bold" if not e.read_only else "")
        meta = []
        if e.duration is not None:
            meta.append(f"{e.duration * 1000:.0f} ms" if e.duration < 1 else f"{e.duration:.1f} s")
        if e.returncode not in (0, None):
            meta.append(f"exit {e.returncode}")
        if e.cancelled:
            meta.append("cancelled")
        active = self.gp.session.active.path if self.gp.session else None
        if active is None or e.cwd.resolve() != active.resolve():
            meta.append(f"in {e.cwd}")
        if meta:
            t.append("   " + " · ".join(meta), style="dim")
        if e.stdin_preview:
            t.append(f"\n         ← {e.stdin_preview}", style=f"italic {PURPLE}")
        if self.show_output:
            for text, out_style in ((e.stdout, ""), (e.stderr, RED if not e.ok else "dim")):
                lines = text.rstrip("\n").splitlines()
                for line in lines[:MAX_LINES_SHOWN]:
                    t.append("\n         " + line, style=out_style or "dim")
                more = len(lines) - MAX_LINES_SHOWN
                if more > 0 or (text is e.stdout and e.stdout_truncated):
                    t.append(f"\n         … {max(more, 0)} more lines", style="dim italic")
        return t

    # -- toggles (view only, no git) ----------------------------------------------------------
    def action_toggle_queries(self) -> None:
        self.show_queries = not self.show_queries
        self.rebuild()

    def action_toggle_output(self) -> None:
        self.show_output = not self.show_output
        self.rebuild()

    def action_clear(self) -> None:
        CONSOLE.clear()

    def context(self, base: SelectionContext) -> SelectionContext:
        return SelectionContext(**{**base.__dict__, "screen": "console"})

    def hint_extras(self) -> list[tuple[str, str]]:
        return [("a", "all/changes only"), ("o", "output"), ("c", "clear")]

    def handle_event(self, event: str, data: dict[str, Any]) -> None:
        pass
