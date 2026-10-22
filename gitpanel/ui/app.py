"""GitPanelApp: owns the session, registry and dispatcher, and implements the `Ui` protocol."""

from __future__ import annotations

import subprocess
from collections.abc import AsyncIterator, Sequence
from pathlib import Path
from typing import Any

from rich.text import Text
from textual import events, on, work
from textual.app import App, ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Static

from ..actions.builtin import register_builtin
from ..actions.context import SelectionContext
from ..actions.registry import Action, Dispatcher, Registry
from ..actions.ui_protocol import Severity
from ..git.operations import OperationState, detect
from ..git.runner import GitError
from ..git.session import ChangeKind, RepoSession
from ..git.status import status
from .dialogs import ChooseDialog, ConfirmDialog, ErrorDialog, OutputDialog, PromptDialog
from .palette import RegistryCommands
from .screens.commit import CommitView
from .widgets.key_hints import KeyHints
from .widgets.operation_banner import OperationBanner


class Header(Static):
    DEFAULT_CSS = """
    Header { height: 1; background: $primary 40%; padding: 0 1; }
    """


class GitPanelApp(App[None]):
    TITLE = "gitpanel"
    COMMANDS = App.COMMANDS | {RegistryCommands}
    CSS = """
    Screen { layout: vertical; }
    #views { height: 1fr; }
    """

    def __init__(self, path: Path | str = ".", *, registry: Registry | None = None) -> None:
        super().__init__()
        self.path = Path(path)
        self.session: RepoSession | None = None
        self.registry = registry or register_builtin(Registry())
        self.dispatcher = Dispatcher(self.registry, self)
        self.operation: OperationState | None = None
        self.startup_error: str | None = None

    # -- layout ----------------------------------------------------------------
    def compose(self) -> ComposeResult:
        yield Header("gitpanel", id="header")
        yield OperationBanner(id="banner")
        with Vertical(id="views"):
            yield CommitView()
        yield KeyHints(id="hints")

    async def on_mount(self) -> None:
        try:
            self.session = await RepoSession.open(self.path)
        except GitError as e:
            self.startup_error = f"{self.path}: {e}"
            self.exit(return_code=2, message=self.startup_error)
            return
        self.session.subscribe(self._on_change)
        self.session.start_watching()
        await self._refresh_all(ChangeKind.ALL)

    @property
    def view(self) -> CommitView:
        return self.query_one(CommitView)

    # -- repo changes ---------------------------------------------------------------
    def _on_change(self, kinds: ChangeKind) -> None:
        self.refresh_repo(kinds)

    @work(exclusive=True, group="refresh")
    async def refresh_repo(self, kinds: ChangeKind) -> None:
        await self._refresh_all(kinds)

    async def _refresh_all(self, kinds: ChangeKind) -> None:
        session = self.session
        if session is None:
            return
        await session.refresh_active()
        st = await status(session.git)
        self.operation = await detect(session.git)
        self.query_one(OperationBanner).show_state(self.operation)
        self._update_header(
            st.branch.branch, st.branch.head, st.branch.ahead, st.branch.behind, st.branch.upstream
        )
        self.view.reload()
        self.refresh_hints()

    def _update_header(
        self, branch: str | None, head: str | None, ahead: int, behind: int, upstream: str | None
    ) -> None:
        assert self.session is not None
        wt = self.session.active
        t = Text()
        t.append("gitpanel ", style="bold")
        t.append(f" {self.session.common_dir.parent.name} ", style="reverse")
        t.append("  ⎇ ")
        t.append(branch or f"detached @ {head[:8] if head else 'unborn'}", style="bold")
        if upstream:
            t.append(f"  ↑{ahead} ↓{behind}", style="dim")
        if not wt.is_main:
            t.append(f"   worktree: {wt.path}", style="italic")
        self.query_one(Header).update(t)

    def on_app_focus(self, event: events.AppFocus) -> None:
        if self.session is not None:
            self.session.emit(ChangeKind.INDEX | ChangeKind.WORKTREE)

    # -- context & dispatch --------------------------------------------------------
    def context(self) -> SelectionContext:
        assert self.session is not None
        base = SelectionContext(self.session, operation=self.operation)
        return self.view.context(base)

    def refresh_hints(self) -> None:
        if self.session is None:
            return
        try:
            hints = self.query_one(KeyHints)
        except Exception:
            return
        extras = self.view.hint_extras() + [("^p", "commands")]
        hints.show_for(self.registry, self.context(), extras)

    def on_descendant_focus(self, event: events.DescendantFocus) -> None:
        self.refresh_hints()

    def on_key(self, event: events.Key) -> None:
        if self.session is None or isinstance(self.screen, ModalScreen):
            return
        if self.screen is not self.screen_stack[0]:
            return
        ctx = self.context()
        action = self.registry.for_key(ctx, event.key)
        if action is None:
            return
        event.stop()
        event.prevent_default()
        self.run_action_in_ctx(action, ctx)

    def run_action_in_ctx(self, action: Action, ctx: SelectionContext) -> None:
        self._dispatch(action, ctx)

    def dispatch_id(self, action_id: str) -> None:
        if self.session is None:
            return
        self._dispatch(self.registry.get(action_id), self.context())

    @work(group="actions")
    async def _dispatch(self, action: Action, ctx: SelectionContext) -> None:
        await self.dispatcher.dispatch(action, ctx)
        self.refresh_hints()

    @on(OperationBanner.Requested)
    def _banner(self, event: OperationBanner.Requested) -> None:
        self.dispatch_id(event.action_id)

    # -- Ui protocol -------------------------------------------------------------------
    async def confirm(self, title: str, body: str = "", *, danger: bool = False) -> bool:
        return bool(
            await self.push_screen(ConfirmDialog(title, body, danger), wait_for_dismiss=True)
        )

    async def prompt(self, title: str, *, default: str = "", placeholder: str = "") -> str | None:
        return await self.push_screen(
            PromptDialog(title, default, placeholder), wait_for_dismiss=True
        )

    async def choose(self, title: str, options: Sequence[tuple[str, str]]) -> str | None:
        return await self.push_screen(ChooseDialog(title, options), wait_for_dismiss=True)

    def notify(  # type: ignore[override]
        self,
        message: str,
        *,
        title: str = "",
        severity: Severity = "information",
        timeout: float | None = None,
        markup: bool = False,
    ) -> None:
        super().notify(message, title=title, severity=severity, timeout=timeout, markup=markup)

    def show_error(self, title: str, details: str) -> None:
        self.push_screen(ErrorDialog(title, details))

    async def show_output(self, title: str, stream: AsyncIterator[str]) -> int:
        return await self.push_screen(OutputDialog(title, stream), wait_for_dismiss=True)

    async def run_in_terminal(self, argv: Sequence[str], cwd: Path) -> int:
        with self.suspend():
            return subprocess.run(list(argv), cwd=cwd).returncode

    def open_screen(self, name: str, **kwargs: Any) -> None:
        pass  # more tool windows arrive in later milestones

    def post(self, event: str, **data: Any) -> None:
        if event == "app.quit":
            self.exit()
            return
        self.view.handle_event(event, data)
