"""Git Log tool window: filters, commit graph table, details, changed files, diff."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from rich.text import Text
from textual import events, on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Input, OptionList, Static
from textual.widgets.option_list import Option

from ...actions.context import FileRef, PatchSelection, Scope, SelectionContext
from ...git import patch as P
from ...git import refs as R
from ...git import tags as T
from ...git.graph import GraphBuilder
from ...git.log import LogFilter, commit_details, containing_branches, load_page
from ...git.markers import collect, default_providers
from ..widgets.branch_panel import BranchPanel
from ..widgets.commit_table import CommitTable, LogRow, fmt_date
from ..widgets.diff_view import DiffView

if TYPE_CHECKING:
    from ..app import GitPanelApp

PAGE = 400
STATUS_COLORS = {"A": "#6CCB3A", "M": "#B58CE8", "D": "#E5484D", "R": "#3FB8AF", "C": "#3FB8AF"}


class LogView(Vertical):
    name_id = "log"
    BINDINGS = [
        Binding("left_square_bracket", "toggle_branches", "Branches panel", show=False),
    ]

    DEFAULT_CSS = """
    LogView { height: 1fr; }
    LogView #log-filters { height: 1; }
    LogView #log-main { height: 1fr; }
    LogView #log-right { width: 1fr; }
    LogView #log-filters Input { width: 1fr; margin-right: 1; }
    LogView #f-text { width: 2fr; }
    LogView CommitTable { height: 55%; border-bottom: tall $panel; }
    LogView #log-bottom { height: 1fr; }
    LogView #log-side { width: 40%; min-width: 30; border-right: tall $panel; }
    LogView #log-details { height: 1fr; padding: 0 1; }
    LogView #log-files { height: 1fr; background: $surface; border: none; }
    LogView DiffView { height: 1fr; }
    """

    def __init__(self) -> None:
        super().__init__(id="log-view")
        self.providers = default_providers()
        self.builder = GraphBuilder()
        self.filter = LogFilter()
        self._loaded = 0
        self._diffs: list[P.FileDiff] = []
        self._detail_sha: str | None = None

    @property
    def gp(self) -> GitPanelApp:
        return self.app  # type: ignore[return-value]

    def compose(self) -> ComposeResult:
        with Horizontal(id="log-filters"):
            yield Input(placeholder="/ Text or regex in message", id="f-text", compact=True)
            yield Input(placeholder="Branch: all", id="f-branch", compact=True)
            yield Input(placeholder="User", id="f-user", compact=True)
            yield Input(placeholder="Paths", id="f-path", compact=True)
            yield Input(placeholder="Since (e.g. 2.weeks)", id="f-since", compact=True)
        with Horizontal(id="log-main"):
            yield BranchPanel(id="log-branches")
            with Vertical(id="log-right"):
                yield CommitTable(id="log-table")
                with Horizontal(id="log-bottom"):
                    with Vertical(id="log-side"):
                        with VerticalScroll(id="log-details"):
                            yield Static("", id="log-details-text")
                        yield OptionList(id="log-files", compact=True)
                    yield DiffView(id="log-diff")

    stale = True

    def focus_default(self) -> None:
        self.query_one(CommitTable).focus()

    # -- loading ------------------------------------------------------------------
    def _read_filter(self) -> LogFilter:
        def val(i: str) -> str:
            return self.query_one(f"#{i}", Input).value.strip()

        branch = val("f-branch")
        paths = val("f-path")
        return LogFilter(
            refs=tuple(branch.split()) if branch else (),
            author=val("f-user") or None,
            text=val("f-text") or None,
            paths=tuple(paths.split()) if paths else (),
            since=val("f-since") or None,
        )

    def reload(self) -> None:
        """Refresh the branches panel and the log (after repository changes)."""
        self.reload_branches()
        self.reload_log()

    @work(exclusive=True, group="log-branches")
    async def reload_branches(self) -> None:
        session = self.gp.session
        if session is None:
            return
        git = session.git
        self.query_one(BranchPanel).populate(
            await R.branches(git),
            await T.tags(git),
            R.Favorites(session.private_dir(shared=True) / "favorites.json").load(),
            session.active.branch,
            session.active.path.resolve(),
        )

    @on(BranchPanel.Changed)
    def _branches_picked(self, event: BranchPanel.Changed) -> None:
        self.query_one("#f-branch", Input).value = " ".join(event.filters)
        self.reload_log()

    def action_toggle_branches(self) -> None:
        panel = self.query_one(BranchPanel)
        panel.toggle_class("-hidden")
        if panel.has_class("-hidden") and panel.has_focus:
            self.query_one(CommitTable).focus()

    @work(exclusive=True, group="log-reload")
    async def reload_log(self) -> None:
        session = self.gp.session
        if session is None:
            return
        table = self.query_one(CommitTable)
        keep = table.rows[table.cursor].commit.sha if table.rows else None
        self.filter = self._read_filter()
        self.builder = GraphBuilder()
        try:
            commits = await load_page(session.git, self.filter, 0, PAGE)
        except Exception as e:  # bad filter values (e.g. unknown branch) shouldn't crash
            self.gp.notify(f"Log: {e}", severity="error")
            commits = []
        rows = [LogRow(c, g) for c, g in zip(commits, self.builder.add_all(commits), strict=True)]
        self._loaded = len(commits)
        markers = await collect(self.providers, session, [c.sha for c in commits])
        for r in rows:
            r.markers = markers.get(r.commit.sha, [])
        table.set_rows(rows, keep_sha=keep, has_more=len(commits) == PAGE)
        self._detail_sha = None
        self.show_details()

    @on(CommitTable.NeedMore)
    def _need_more(self) -> None:
        self.load_more()

    @work(exclusive=True, group="log-more")
    async def load_more(self) -> None:
        session = self.gp.session
        table = self.query_one(CommitTable)
        if session is None or not table.has_more:
            return
        commits = await load_page(session.git, self.filter, self._loaded, PAGE)
        self._loaded += len(commits)
        rows = [LogRow(c, g) for c, g in zip(commits, self.builder.add_all(commits), strict=True)]
        markers = await collect(self.providers, session, [c.sha for c in commits])
        for r in rows:
            r.markers = markers.get(r.commit.sha, [])
        table.append_rows(rows, has_more=len(commits) == PAGE)

    # -- details -----------------------------------------------------------------------
    @on(CommitTable.CursorChanged)
    def _cursor_changed(self) -> None:
        self.show_details()
        self.gp.refresh_hints()

    @work(exclusive=True, group="log-details")
    async def show_details(self) -> None:
        session = self.gp.session
        table = self.query_one(CommitTable)
        text = self.query_one("#log-details-text", Static)
        files = self.query_one("#log-files", OptionList)
        diff_view = self.query_one(DiffView)
        selected = table.selected_rows()
        if session is None or not selected:
            text.update("No commits match the filter" if session else "")
            files.clear_options()
            diff_view.set_diff(None, None)
            self._detail_sha = None
            return
        if len(selected) > 1:
            text.update(f"{len(selected)} commits selected")
            files.clear_options()
            diff_view.set_diff(None, None, message="Select a single commit to see its changes")
            self._detail_sha = None
            return
        row = selected[0]
        sha = row.commit.sha
        if sha == self._detail_sha:
            return
        self._detail_sha = sha
        git = session.git
        c = await commit_details(git, sha)
        t = Text()
        t.append(c.subject + "\n", style="bold")
        body = c.body.split("\n", 1)[1].strip("\n") if "\n" in c.body else ""
        if body:
            t.append(body + "\n")
        t.append("\n")
        t.append(f"{c.sha[:10]}", style="bold #6CCB3A")
        t.append(f"  {c.author_name} <{c.author_email}>  {fmt_date(c.author_date)}\n", style="dim")
        if (c.committer_name, c.committer_email) != (c.author_name, c.author_email):
            t.append(f"committed by {c.committer_name}  {fmt_date(c.commit_date)}\n", style="dim")
        if row.markers:
            t.append("refs: " + ", ".join(m.label for m in row.markers) + "\n", style="#A66BE0")
        text.update(t)
        self._diffs = await P.load_diff(git, P.CommitDiff(sha))
        if self._detail_sha != sha:
            return
        files.clear_options()
        for d in self._diffs:
            label = Text()
            label.append(f"{d.status} ", style=STATUS_COLORS.get(d.status, ""))
            label.append(d.path)
            if d.status in "RC" and d.old_path:
                label.append(f"  ← {d.old_path}", style="dim")
            files.add_option(Option(label, id=d.path))
        if self._diffs:
            files.highlighted = 0
            diff_view.set_diff(P.CommitDiff(sha), self._diffs[0])
        else:
            diff_view.set_diff(None, None, message="No file changes (merge or empty commit)")
        branches = await containing_branches(git, sha)
        if self._detail_sha == sha and branches:
            t.append("in: " + ", ".join(branches[:12]), style="dim")
            if len(branches) > 12:
                t.append(f" (+{len(branches) - 12})", style="dim")
            text.update(t)

    @on(OptionList.OptionHighlighted, "#log-files")
    def _file_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        if self._detail_sha is None:
            return
        diff = next((d for d in self._diffs if d.path == event.option.id), None)
        self.query_one(DiffView).set_diff(P.CommitDiff(self._detail_sha), diff)

    # -- filters ---------------------------------------------------------------------------
    @on(Input.Submitted)
    def _filter_submitted(self) -> None:
        self.reload_log()
        self.query_one(CommitTable).focus()

    def on_key(self, event: events.Key) -> None:
        focused = self.screen.focused
        if event.key == "slash" and isinstance(focused, CommitTable):
            event.stop()
            event.prevent_default()
            self.query_one("#f-text", Input).focus()
        elif (
            event.key == "escape"
            and isinstance(focused, Input)
            and focused.parent
            and (focused.parent.id == "log-filters")
        ):
            event.stop()
            event.prevent_default()
            focused.value = ""
            self.reload_log()
            self.query_one(CommitTable).focus()

    # -- context ------------------------------------------------------------------------
    def context(self, base: SelectionContext) -> SelectionContext:
        focused = self.screen.focused
        table = self.query_one(CommitTable)
        picked = tuple(
            r.commit.sha for r in table.rows if any(m.kind == "picked" for m in r.markers)
        )
        kwargs: dict[str, Any] = {
            "screen": "log",
            "commits": tuple(table.selected_commits()),
            "form": {"picked": picked},
        }
        diff_view = self.query_one(DiffView)
        panel = self.query_one(BranchPanel)
        if focused is panel:
            ref = panel.selected_ref()
            if ref is not None:
                kwargs.update(focus=Scope.BRANCH, refs=(ref,))
        elif focused is table and table.rows:
            kwargs["focus"] = Scope.COMMIT
        elif focused is diff_view and diff_view.diff is not None and diff_view.source:
            sel = diff_view.selection()
            if sel is not None:
                kwargs.update(
                    focus=Scope.HUNK,
                    patch=PatchSelection(diff_view.source, (diff_view.diff,), sel),
                )
        elif focused is self.query_one("#log-files", OptionList) and self._detail_sha:
            files = self.query_one("#log-files", OptionList)
            if files.highlighted is not None:
                path = files.get_option_at_index(files.highlighted).id or ""
                kwargs.update(
                    focus=Scope.FILE,
                    files=(FileRef(path, P.CommitDiff(self._detail_sha), "commit"),),
                )
        return SelectionContext(**{**base.__dict__, **kwargs})

    def hint_extras(self) -> list[tuple[str, str]]:
        focused = self.screen.focused
        if isinstance(focused, BranchPanel):
            return [("space", "mark branch"), ("[", "hide panel")]
        if isinstance(focused, CommitTable):
            return [("/", "filter"), ("shift+↑↓", "select"), ("[", "branches")]
        if isinstance(focused, DiffView):
            return [("s", "side-by-side"), ("n/p", "next/prev change")]
        return []

    def handle_event(self, event: str, data: dict[str, Any]) -> None:
        if event == "open" and "branch" in data:
            for fid in ("f-text", "f-user", "f-path", "f-since"):
                self.query_one(f"#{fid}", Input).value = ""
            self.query_one("#f-branch", Input).value = str(data["branch"])
            self.stale = False
            self.reload_branches()
            self.reload_log()
