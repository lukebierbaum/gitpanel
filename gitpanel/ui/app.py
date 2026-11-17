"""GitPanelApp: owns the session, registry and dispatcher, and implements the `Ui` protocol."""

from __future__ import annotations

import subprocess
import time
from collections.abc import AsyncIterator, Sequence
from datetime import timedelta
from pathlib import Path
from typing import Any

from rich.text import Text
from textual import events, on, work
from textual.app import App, ComposeResult
from textual.widgets import ContentSwitcher, Static, Tab, Tabs
from textual.worker import WorkerFailed

from ..actions.builtin import register_builtin
from ..actions.context import SelectionContext
from ..actions.custom import register_custom
from ..actions.registry import Action, Dispatcher, Registry
from ..actions.ui_protocol import Field, Severity
from ..config import Config
from ..config import load as load_config
from ..forge.github import detect as forge_detect
from ..git.operations import OperationState, detect
from ..git.runner import GitError
from ..git.session import ChangeKind, RepoSession, WorktreeGone
from ..git.status import status
from .base import ToolWindow
from .colors import CI_ICON, C
from .dialogs import (
    ChooseDialog,
    ConfirmDialog,
    ErrorDialog,
    FormDialog,
    OutputDialog,
    PromptDialog,
    TextDialog,
)
from .fmt import ahead_behind, branch_or_detached
from .keys import alt_equivalent
from .palette import RegistryCommands
from .screens.annotate import AnnotateScreen
from .screens.branches import BranchesPopup
from .screens.commit import CommitView
from .screens.compare import CompareView
from .screens.console import ConsoleView
from .screens.help import HelpScreen
from .screens.history import HistoryScreen
from .screens.journal import JournalScreen
from .screens.log import LogView
from .screens.prs import PRsView
from .screens.rebase_editor import RebaseEditorScreen
from .screens.resolver import ResolverScreen
from .screens.shelf import ShelfView
from .screens.worktrees import WorktreesView
from .theme import DEFAULT_THEME, THEMES
from .widgets.branch_panel import BranchPanel
from .widgets.diff_view import DiffView
from .widgets.key_hints import KeyHints, pretty_key
from .widgets.operation_banner import OperationBanner


class Header(Static):
    DEFAULT_CSS = """
    Header { height: 1; background: $primary 45%; color: $foreground; padding: 0 1; }
    """


class GitPanelApp(App[None]):
    TITLE = "gitpanel"
    COMMANDS = App.COMMANDS | {RegistryCommands}
    CSS = """
    Screen { layout: vertical; }
    #views { height: 1fr; }
    #toolwindows { height: 1; background: $panel; }
    #toolwindows Tab { padding: 0 2; }
    """
    VIEWS = ("commit", "log", "worktrees", "compare", "shelf", "console", "prs")

    def __init__(
        self,
        path: Path | str = ".",
        *,
        registry: Registry | None = None,
        config: Config | None = None,
        config_path: Path | None = None,
        trust_repo_config: bool = False,
    ) -> None:
        super().__init__()
        self.path = Path(path)
        self.config = config or Config()
        self._config_explicit = config is not None
        self._config_path = config_path
        self._trust_repo_config = trust_repo_config
        self.session: RepoSession | None = None
        self.registry = registry or register_builtin(Registry())
        self.dispatcher = Dispatcher(self.registry, self)
        self.operation: OperationState | None = None
        self.startup_error: str | None = None
        self._pr_badge: tuple[str | None, Any, float] = (None, None, 0.0)
        self._header_args: tuple[Any, ...] | None = None
        self._gone: str | None = None  # why the repository can't be read (reported once)
        self._forge: tuple[Any, Any] | None = None
        for theme in THEMES:
            self.register_theme(theme)
        self.theme = DEFAULT_THEME

    # -- layout ----------------------------------------------------------------
    def compose(self) -> ComposeResult:
        yield Header("gitpanel", id="header")
        yield OperationBanner(id="banner")
        yield Tabs(
            Tab("1 Commit", id="tab-commit"),
            Tab("2 Log", id="tab-log"),
            Tab("3 Worktrees", id="tab-worktrees"),
            Tab("4 Compare", id="tab-compare"),
            Tab("5 Shelf", id="tab-shelf"),
            Tab("6 Console", id="tab-console"),
            Tab("7 PRs", id="tab-prs"),
            id="toolwindows",
        )
        with ContentSwitcher(initial="commit-view", id="views"):
            yield CommitView()
            yield LogView()
            yield WorktreesView()
            yield CompareView()
            yield ShelfView()
            yield ConsoleView()
            yield PRsView()
        yield KeyHints(id="hints")

    def watch_theme(self, theme: str) -> None:
        # gitpanel's own colours (ui/colors.py) follow the theme's light/dark
        C.use(self.current_theme.dark)
        self.refresh(layout=False)

    async def on_mount(self) -> None:
        try:
            self.session = await RepoSession.open(self.path)
        except GitError as e:
            self.startup_error = f"{self.path}: {e}"
            self.exit(return_code=2, message=self.startup_error)
            return
        self.query_one(Tabs).can_focus = False
        self.apply_config()
        self.session.subscribe(self._on_change)
        self.session.start_watching()
        self.run_worker(
            self.session.journal.prune(
                self.config.journal_keep, timedelta(days=self.config.journal_max_age_days)
            ),
            group="prune",
            exit_on_error=False,
        )
        await self._refresh_all(ChangeKind.ALL)

    def apply_config(self) -> None:
        assert self.session is not None
        if not self._config_explicit:
            self.config = load_config(
                self._config_path, self.session.active.path, self._trust_repo_config
            )
        cfg = self.config
        self.dispatcher.config = cfg
        cfg.errors += [f"commands: {e}" for e in register_custom(self.registry, cfg.commands)]
        if cfg.theme in self.available_themes:
            self.theme = cfg.theme
        else:
            cfg.errors.append(f"unknown theme {cfg.theme!r}")
        try:
            self.registry.rebind(cfg.keys)
        except KeyError as e:
            known = {k: v for k, v in cfg.keys.items() if k in {a.id for a in self.registry.all()}}
            self.registry.rebind(known)
            cfg.errors.append(f"[keys]: {e.args[0]}")
        for dv in self.query(DiffView):
            dv.split = cfg.side_by_side
        for panel in self.query(BranchPanel):
            panel.folders = cfg.branch_folders
        for err in cfg.errors:
            self.notify(f"config: {err}", severity="warning", timeout=8)

    @property
    def view(self) -> ToolWindow:
        current = self.query_one(ContentSwitcher).current or "commit-view"
        return self.query_one(f"#{current}", ToolWindow)

    def _views(self) -> list[ToolWindow]:
        return list(self.query_one(ContentSwitcher).query_children(ToolWindow))

    def _context_screen(self) -> Any | None:
        """The screen whose selection actions apply to: a tool popup or the base screen."""
        screen = self.screen
        if hasattr(screen, "context"):
            return screen
        if screen is self.screen_stack[0]:
            return self.view
        return None

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
        before = session.active.path
        try:
            await session.refresh_active()
            if session.active.path != before:
                # said before the next await: the switch's ChangeKind.ALL starts a new
                # refresh, which cancels this one
                self.notify(
                    f"{before} is gone (moved or deleted) — switched to worktree "
                    f"{session.active.path}",
                    severity="warning",
                    timeout=10,
                )
            st = await status(session.git)
            self.operation = await detect(session.git)
        except (WorktreeGone, GitError) as e:
            self._repo_gone(str(e))
            return
        self._gone = None
        self.query_one(OperationBanner).show_state(self.operation)
        self._update_header(
            st.branch.branch, st.branch.head, st.branch.ahead, st.branch.behind, st.branch.upstream
        )
        self.view.reload()
        for v in self._views():
            if v is not self.view:
                v.stale = True
        if hasattr(self.screen, "reload"):
            self.screen.reload()
        self.refresh_hints()

    def _repo_gone(self, reason: str) -> None:
        """The active worktree can't be read and there's nothing to switch to: say so once
        (not on every change event / focus) and leave the views as they are."""
        assert self.session is not None
        t = Text()
        t.append("gitpanel ", style="bold")
        t.append(f" {self.session.common_dir.parent.name} ", style=f"bold {C.ink} on {C.red}")
        t.append(f"  ⚠ {self.session.active.path} is no longer available", style=f"bold {C.red}")
        self._header_args = None
        self.query_one(Header).update(t)
        if self._gone != reason:
            self._gone = reason
            self.show_error("Can't read the repository", reason)

    def _update_header(
        self, branch: str | None, head: str | None, ahead: int, behind: int, upstream: str | None
    ) -> None:
        assert self.session is not None
        wt = self.session.active
        t = Text()
        t.append("gitpanel ", style="bold")
        t.append(f" {self.session.common_dir.parent.name} ", style=f"bold {C.ink} on {C.green}")
        t.append("  ⎇ ")
        t.append(branch_or_detached(branch, head), style=f"bold {C.green}")
        if upstream:
            t.append(f"  {ahead_behind(ahead, behind)}", style="dim")
        if not wt.is_main:
            t.append(f"   worktree: {wt.path}", style="italic")
        badge_branch, pr, _ = self._pr_badge
        if pr is not None and badge_branch == branch:
            icon, color = CI_ICON[pr.ci]
            t.append(f"   PR #{pr.number}", style="bold")
            if pr.ci != "none":
                t.append(f" {icon}", style=f"bold {C[color]}")
        self._header_args = (branch, head, ahead, behind, upstream)
        self.query_one(Header).update(t)
        self._maybe_refresh_badge(branch)

    def _maybe_refresh_badge(self, branch: str | None) -> None:
        badge_branch, _, fetched = self._pr_badge
        ttl = max(self.config.pr_refresh_seconds, 30)
        if branch and (branch != badge_branch or time.monotonic() - fetched > ttl):
            self._pr_badge = (
                branch,
                self._pr_badge[1] if branch == badge_branch else None,
                time.monotonic(),
            )
            self.refresh_pr_badge(branch)

    @work(exclusive=True, group="pr-badge")
    async def refresh_pr_badge(self, branch: str) -> None:
        session = self.session
        if session is None:
            return
        if self._forge is None or self._forge[0] is not session:
            self._forge = (session, await forge_detect(session))
        provider = self._forge[1]
        pr = None
        if provider is not None:
            try:
                pr = await provider.current_pr()
            except Exception:  # noqa: BLE001 - the badge is best effort
                pr = None
        if session.active.branch_short == branch:
            self._pr_badge = (branch, pr, time.monotonic())
            if self._header_args is not None:
                self._update_header(*self._header_args)

    def on_app_focus(self, event: events.AppFocus) -> None:
        if self.session is not None:
            self.session.emit(ChangeKind.INDEX | ChangeKind.WORKTREE)

    # -- context & dispatch --------------------------------------------------------
    def context(self) -> SelectionContext:
        assert self.session is not None
        base = SelectionContext(self.session, operation=self.operation)
        provider = self._context_screen() or self.view
        return provider.context(base)

    def refresh_hints(self) -> None:
        if self.session is None or not self.screen_stack:  # no screens while shutting down
            return
        provider = self._context_screen()
        if provider is not None and hasattr(provider, "show_hints"):
            provider.show_hints(self.registry, self.context())
            return
        try:
            hints = self.screen_stack[0].query_one("#hints", KeyHints)
        except Exception:
            return
        extras = self.view.hint_extras() + [("alt+enter", "actions"), ("^p", "commands")]
        base = SelectionContext(self.session, operation=self.operation)
        hints.show_for(self.registry, self.view.context(base), extras)

    def on_descendant_focus(self, event: events.DescendantFocus) -> None:
        self.refresh_hints()

    def on_key(self, event: events.Key) -> None:
        if self.session is None or self._context_screen() is None:
            return
        ctx = self.context()
        action = self.registry.for_key(ctx, event.key)
        if action is None:
            # Mac-Option-style layouts send Alt+5 as "∞" etc.; map back to alt+…
            alt = alt_equivalent(event.character)
            action = self.registry.for_key(ctx, alt) if alt else None
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

    async def prompt(
        self, title: str, *, default: str = "", placeholder: str = "", multiline: bool = False
    ) -> str | None:
        if multiline:
            return await self.push_screen(TextDialog(title, default), wait_for_dismiss=True)
        return await self.push_screen(
            PromptDialog(title, default, placeholder), wait_for_dismiss=True
        )

    async def form(
        self,
        title: str,
        fields: Sequence[Field],
        *,
        body: str = "",
        ok_label: str = "OK",
        danger: bool = False,
    ) -> dict[str, Any] | None:
        return await self.push_screen(
            FormDialog(title, fields, body, ok_label, danger), wait_for_dismiss=True
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

    def _handle_exception(self, error: Exception) -> None:
        # Textual sends every unhandled error here and exits. A git command failing in a
        # background load (repository moved, a ref deleted under us …) is reported instead:
        # the worker simply ends. Only workers — after a failing message handler Textual
        # has already stopped that widget's message loop, so those still exit; handlers
        # that await git catch GitError themselves.
        if isinstance(error, WorkerFailed) and isinstance(error.error, GitError):
            self.report_git_error(error.error)
            return
        super()._handle_exception(error)

    def report_git_error(self, e: GitError) -> None:
        """Show a failed git command without stacking the same error twice; while the
        repository is known to be unreadable (see the header) a toast is enough."""
        if self._gone is not None:
            self.notify(str(e), severity="error", timeout=6)
            return
        if any(isinstance(s, ErrorDialog) and s.title_text == str(e) for s in self.screen_stack):
            return
        self.show_error(str(e), e.details)

    async def show_output(self, title: str, stream: AsyncIterator[str]) -> int:
        return await self.push_screen(OutputDialog(title, stream), wait_for_dismiss=True)

    async def run_in_terminal(self, argv: Sequence[str], cwd: Path) -> int:
        with self.suspend():
            return subprocess.run(list(argv), cwd=cwd).returncode

    def open_screen(self, name: str, **kwargs: Any) -> None:
        if name == "resolver":
            self.push_screen(ResolverScreen(str(kwargs["path"])))
            return
        if name == "help":
            self.push_screen(HelpScreen(self.context()))
            return
        if name == "journal":
            self.push_screen(JournalScreen())
            return
        if name == "history":
            self.push_screen(
                HistoryScreen(
                    str(kwargs["path"]), kwargs.get("start"), kwargs.get("end"), kwargs.get("rev")
                )
            )
            return
        if name == "annotate":
            self.push_screen(
                AnnotateScreen(str(kwargs["path"]), kwargs.get("rev"), kwargs.get("line"))
            )
            return
        if name == "rebase":
            self.push_screen(RebaseEditorScreen(kwargs.get("base"), list(kwargs["todo"])))
            return
        if name == "branches":
            if not isinstance(self.screen, BranchesPopup):
                self.push_screen(BranchesPopup())
            return
        if name not in self.VIEWS:
            return
        while len(self.screen_stack) > 1:
            self.pop_screen()  # back to the tool windows
        switcher = self.query_one(ContentSwitcher)
        if switcher.current == f"{name}-view":
            if kwargs:
                self.view.handle_event("open", kwargs)
            return
        switcher.current = f"{name}-view"
        if kwargs:
            self.view.handle_event("open", kwargs)
        self.query_one(Tabs).active = f"tab-{name}"
        view = self.view
        if view.stale:
            view.stale = False
            view.reload()
        view.focus_default()
        self.refresh_hints()

    @on(Tabs.TabActivated, "#toolwindows")
    def _tab_activated(self, event: Tabs.TabActivated) -> None:
        if event.tab.id:
            self.open_screen(event.tab.id.removeprefix("tab-"))

    def post(self, event: str, **data: Any) -> None:
        if event == "app.quit":
            self.exit()
            return
        if event == "clipboard":
            self.copy_to_clipboard(str(data.get("text", "")))
            return
        if event == "open_url":
            self.open_url(str(data.get("url", "")))
            return
        if event == "popup.close":
            if isinstance(self.screen, BranchesPopup):
                self.screen.dismiss()
            return
        if event == "branches.refresh":
            if isinstance(self.screen, BranchesPopup):
                self.screen.reload()
            return
        if event == "context_menu":
            self._context_menu()
            return
        if hasattr(self.screen, "handle_event"):
            self.screen.handle_event(event, data)
        for v in self._views():
            v.handle_event(event, data)

    @work(group="actions")
    async def _context_menu(self) -> None:
        ctx = self.context()
        actions = [
            a
            for a in self.registry.available(ctx)
            if not a.id.startswith("app.") and a.show_in_palette
        ]
        if not actions:
            self.notify("No actions for the current selection")
            return
        options = []
        for a in actions:
            keys = self.registry.keys_for(a)
            shown = f"   [{pretty_key(keys[0])}]" if keys else ""
            options.append((a.id, a.label_for(ctx) + shown))
        choice = await self.choose("Actions", options)
        if choice:
            await self.dispatcher.dispatch(self.registry.get(choice), ctx)
            self.refresh_hints()
