"""Pull Requests tool window: list, overview, commits, files (with review threads), checks."""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from rich.text import Text
from textual import on, work
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.timer import Timer
from textual.widgets import (
    DataTable,
    Input,
    Markdown,
    OptionList,
    Select,
    Static,
    TabbedContent,
    TabPane,
)
from textual.widgets.option_list import Option

from ...actions.context import Scope, SelectionContext
from ...forge.github import QUERIES, GitHub, detect
from ...forge.models import PullRequest, ReviewThread
from ...forge.runner import ForgeError
from ...git import patch as P
from ...git.graph import GraphRow
from ...git.log import LogFilter, load_page
from ...git.markers import collect, default_providers
from ..widgets.commit_table import CommitTable, LogRow
from ..widgets.diff_view import DiffView
from ..widgets.splitter import Splitter

if TYPE_CHECKING:
    from ..app import GitPanelApp

GREEN, PURPLE, AMBER, RED = "#6CCB3A", "#A66BE0", "#E3B23C", "#E5484D"
CI_ICON = {
    "success": ("✓", GREEN),
    "failure": ("✗", RED),
    "pending": ("●", AMBER),
    "none": ("·", "#8A8F87"),
}
CHECK_ICON = {
    "success": ("✓", GREEN),
    "failure": ("✗", RED),
    "pending": ("●", AMBER),
    "skipped": ("–", "#8A8F87"),
    "neutral": ("○", "#8A8F87"),
    "cancelled": ("⊘", "#8A8F87"),
}
REVIEW = {
    "APPROVED": ("Approved", GREEN),
    "CHANGES_REQUESTED": ("Changes", RED),
    "REVIEW_REQUIRED": ("Review", AMBER),
    "": ("", ""),
}


def ago(when: datetime | None) -> str:
    if when is None:
        return ""
    s = (datetime.now(UTC) - when).total_seconds()
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if s >= size:
            return f"{int(s // size)}{unit}"
    return "now"


class PRsView(Vertical):
    name_id = "prs"
    stale = True

    DEFAULT_CSS = """
    PRsView { height: 1fr; }
    PRsView #pr-bar { height: 3; padding: 0 1; }
    PRsView #pr-query { width: 26; }
    PRsView #pr-search { width: 1fr; margin: 1 1 0 1; }
    PRsView #pr-status { width: auto; max-width: 60; padding: 1 1 0 0; color: $text-muted; }
    PRsView #pr-main { height: 1fr; }
    PRsView #pr-left { width: 50%; min-width: 30; }
    PRsView #pr-list { height: 1fr; background: $surface; }
    PRsView #pr-empty { height: auto; padding: 1 2; color: $text-muted; display: none; }
    PRsView #pr-empty.-shown { display: block; }
    PRsView TabbedContent { height: 1fr; }
    PRsView #pr-overview { padding: 0 1; }
    PRsView #pr-files { width: 26; background: $surface; border: none; }
    PRsView #pr-thread { height: auto; max-height: 12; padding: 0 1; background: $panel;
                         display: none; }
    PRsView #pr-thread.-shown { display: block; }
    PRsView #pr-checks { height: 1fr; background: $surface; }
    """

    def __init__(self) -> None:
        super().__init__(id="prs-view")
        self.provider: GitHub | None = None
        self.prs: list[PullRequest] = []
        self.detail: PullRequest | None = None
        self.diffs: list[P.FileDiff] = []
        self.threads: list[ReviewThread] = []
        self._loaded_tabs: set[str] = set()
        self._timer: Timer | None = None
        self._select_after: int | None = None
        self._detected = False

    @property
    def gp(self) -> GitPanelApp:
        return self.app  # type: ignore[return-value]

    def compose(self) -> ComposeResult:
        with Horizontal(id="pr-bar"):
            yield Select(
                [(label, key) for key, (label, _) in QUERIES.items()],
                value="mine",
                allow_blank=False,
                id="pr-query",
            )
            yield Input(
                placeholder="Search (title words, label:bug, author:someone …)",
                id="pr-search",
                compact=True,
            )
            yield Static("", id="pr-status")
        with Horizontal(id="pr-main"):
            with Vertical(id="pr-left"):
                yield Static("", id="pr-empty")
                yield DataTable(id="pr-list", cursor_type="row", zebra_stripes=True)
            yield Splitter("prs.list", "x", minimum=30)
            with TabbedContent(id="pr-tabs"):
                with (
                    TabPane("Overview", id="tab-overview"),
                    VerticalScroll(id="pr-overview"),
                ):
                    yield Static("", id="pr-meta")
                    yield Markdown("", id="pr-body")
                    yield Static("", id="pr-conversation")
                with TabPane("Commits", id="tab-commits"):
                    yield CommitTable(id="pr-commits")
                with TabPane("Files", id="tab-files"), Horizontal():
                    yield OptionList(id="pr-files", compact=True)
                    yield Splitter("prs.files", "x", minimum=20)
                    with Vertical():
                        yield DiffView(id="pr-diff")
                        yield Static("", id="pr-thread")
                with TabPane("Checks", id="tab-checks"):
                    yield DataTable(id="pr-checks", cursor_type="row")

    def on_mount(self) -> None:
        table = self.query_one("#pr-list", DataTable)
        for label, width in (
            ("#", 6),
            ("CI", 3),
            ("Review", 9),
            ("Title", None),
            ("Author", 12),
            ("Updated", 8),
        ):
            table.add_column(label, width=width)
        checks = self.query_one("#pr-checks", DataTable)
        for label, width in (("", 2), ("Check", 32), ("Workflow", 18), ("Time", 8)):
            checks.add_column(label, width=width)

    def focus_default(self) -> None:
        self.query_one("#pr-list", DataTable).focus()
        cfg = self.gp.config
        if self._timer is None and cfg.pr_refresh_seconds > 0:
            self._timer = self.set_interval(cfg.pr_refresh_seconds, self._tick)
        if not self._detected:
            query = cfg.pr_default_query if cfg.pr_default_query in QUERIES else "mine"
            self.query_one("#pr-query", Select).value = query

    def _tick(self) -> None:
        if self.display and self.gp.view is self:  # only while the window is showing
            self.reload()

    # -- loading -------------------------------------------------------------------------
    def _status(self, text: str, style: str = "") -> None:
        self.query_one("#pr-status", Static).update(Text(text, style=style))

    def _empty(self, text: str | None) -> None:
        empty = self.query_one("#pr-empty", Static)
        empty.set_class(text is not None, "-shown")
        if text is not None:
            empty.update(text)

    @work(exclusive=True, group="prs-list")
    async def reload(self) -> None:
        session = self.gp.session
        if session is None:
            return
        if not self._detected or (self.provider and self.provider.session is not session):
            self.provider = await detect(session)
            self._detected = True
        if self.provider is None:
            self._empty(
                "This repository has no github.com remote, or the GitHub CLI (gh) "
                "isn't installed.\n\nInstall gh from https://cli.github.com and run "
                "`gh auth login`."
            )
            self._status("")
            return
        query = str(self.query_one("#pr-query", Select).value)
        search = self.query_one("#pr-search", Input).value
        self._status("loading…")
        try:
            self.prs = await self.provider.list_prs(query, search)
        except ForgeError as e:
            self._empty(f"{e}\n\n{e.hint}" if e.hint else str(e))
            self._status("")
            return
        table = self.query_one("#pr-list", DataTable)
        keep = self._select_after or (self.detail.number if self.detail else None)
        self._select_after = None
        table.clear()
        for pr in self.prs:
            icon, color = CI_ICON[pr.ci]
            review, rcolor = REVIEW.get(pr.review_decision, ("", ""))
            title = Text(pr.title, style="dim" if pr.draft else "")
            if pr.draft:
                title.append("  draft", style=f"italic {PURPLE}")
            for label in pr.labels[:3]:
                title.append(f"  {label}", style="#3FB8AF")
            table.add_row(
                Text(f"#{pr.number}", style="bold"),
                Text(icon, style=f"bold {color}"),
                Text(review, style=rcolor),
                title,
                pr.author,
                ago(pr.updated),
                key=str(pr.number),
            )
        label = QUERIES.get(query, ("", ""))[0]
        self._empty(None if self.prs else f"No pull requests in “{label}”.")
        self._status(f"{len(self.prs)} · updated {datetime.now():%H:%M}")
        if self.prs:
            row = next((i for i, p in enumerate(self.prs) if p.number == keep), 0)
            table.move_cursor(row=row)
            self.show_pr(self.prs[row].number, force=True)
        else:
            self.detail = None
            self._clear_detail()

    @on(Select.Changed, "#pr-query")
    def _query_changed(self) -> None:
        self.reload()

    @on(Input.Submitted, "#pr-search")
    def _search(self) -> None:
        self.reload()
        self.query_one("#pr-list", DataTable).focus()

    @on(DataTable.RowHighlighted, "#pr-list")
    def _row(self, event: DataTable.RowHighlighted) -> None:
        if event.row_key and event.row_key.value:
            self.show_pr(int(event.row_key.value))
        self.gp.refresh_hints()

    def _clear_detail(self) -> None:
        self.query_one("#pr-meta", Static).update("")
        self.query_one("#pr-body", Markdown).update("")
        self.query_one("#pr-conversation", Static).update("")
        self.query_one("#pr-commits", CommitTable).set_rows([], has_more=False)
        self.query_one("#pr-files", OptionList).clear_options()
        self.query_one("#pr-diff", DiffView).set_diff(None, None)
        self.query_one("#pr-checks", DataTable).clear()

    @work(exclusive=True, group="prs-detail")
    async def show_pr(self, number: int, *, force: bool = False) -> None:
        if self.provider is None:
            return
        if not force and self.detail is not None and self.detail.number == number:
            return
        try:
            pr = await self.provider.pr(number)
        except ForgeError as e:
            self.gp.notify(str(e), severity="error")
            return
        self.detail = pr
        self._loaded_tabs.clear()
        self._render_overview(pr)
        self._render_checks(pr)
        await self._load_active_tab()
        self.gp.refresh_hints()

    def _render_overview(self, pr: PullRequest) -> None:
        t = Text()
        t.append(f"#{pr.number} {pr.title}\n", style="bold")
        state_color = {"OPEN": GREEN, "MERGED": PURPLE, "CLOSED": RED}.get(pr.state, "")
        t.append(f"{pr.state.lower()}{' · draft' if pr.draft else ''}", style=f"bold {state_color}")
        t.append(f"   {pr.author} wants to merge {pr.head} into {pr.base}", style="dim")
        t.append(f"   +{pr.additions} −{pr.deletions} in {pr.changed_files} files\n", style="dim")
        icon, color = CI_ICON[pr.ci]
        failing = sum(c.state == "failure" for c in pr.checks)
        t.append(f"{icon} checks: ", style=color)
        t.append(f"{len(pr.checks)} total, {failing} failing" if pr.checks else "none", style="dim")
        review, rcolor = REVIEW.get(pr.review_decision, ("", ""))
        if review:
            t.append("   review: ", style="dim")
            t.append(review, style=rcolor)
        if pr.mergeable == "CONFLICTING":
            t.append("   merge conflicts with base", style=RED)
        if pr.auto_merge:
            t.append("   auto-merge on", style=GREEN)
        t.append("\n")
        if pr.labels:
            t.append("labels: " + ", ".join(pr.labels) + "\n", style="#3FB8AF")
        if pr.reviewers_requested:
            t.append("waiting on: " + ", ".join(pr.reviewers_requested) + "\n", style="dim")
        self.query_one("#pr-meta", Static).update(t)
        self.query_one("#pr-body", Markdown).update(pr.body or "*No description.*")
        conv = Text()
        events: list[tuple[datetime | None, str, str, str]] = [
            (r.submitted, r.author, r.state.replace("_", " ").lower(), r.body) for r in pr.reviews
        ] + [(c.created, c.author, "commented", c.body) for c in pr.comments]
        events.sort(key=lambda e: e[0] or datetime.min.replace(tzinfo=UTC))
        if events:
            conv.append("\nConversation\n", style=f"bold {PURPLE}")
        for when, who, what, body in events:
            conv.append(f"{who} ", style="bold")
            conv.append(f"{what} · {ago(when)} ago\n" if when else f"{what}\n", style="dim")
            if body.strip():
                conv.append(body.strip() + "\n")
            conv.append("\n")
        self.query_one("#pr-conversation", Static).update(conv)

    def _render_checks(self, pr: PullRequest) -> None:
        table = self.query_one("#pr-checks", DataTable)
        table.clear()
        for i, c in enumerate(pr.checks):
            icon, color = CHECK_ICON[c.state]
            dur = c.duration
            table.add_row(
                Text(icon, style=f"bold {color}"),
                c.name,
                c.workflow,
                f"{int(dur // 60)}m{int(dur % 60):02d}s" if dur is not None else "",
                key=str(i),
            )

    @on(TabbedContent.TabActivated, "#pr-tabs")
    async def _tab(self) -> None:
        await self._load_active_tab()
        self.gp.refresh_hints()

    async def _load_active_tab(self) -> None:
        pr = self.detail
        if pr is None or self.provider is None:
            return
        tab = self.query_one("#pr-tabs", TabbedContent).active
        if tab in self._loaded_tabs or tab not in ("tab-commits", "tab-files"):
            return
        self._loaded_tabs.add(tab)
        self.load_tab(tab, pr)

    @work(exclusive=True, group="prs-tab")
    async def load_tab(self, tab: str, pr: PullRequest) -> None:
        assert self.provider is not None
        session = self.gp.session
        if session is None:
            return
        try:
            head = await self.provider.fetch_head(pr)
            base = await self.provider.merge_base(pr)
        except Exception as e:  # noqa: BLE001 - network/permission problems shouldn't crash
            self._loaded_tabs.discard(tab)
            self.gp.notify(f"Couldn't fetch #{pr.number}: {e}", severity="error")
            return
        if self.detail is None or self.detail.number != pr.number:
            return
        if tab == "tab-commits":
            commits = await load_page(session.git, LogFilter(refs=(f"{base}..{head}",)), 0, 500)
            rows = [LogRow(c, GraphRow(c.sha, 0, (("●", 0),))) for c in commits]
            markers = await collect(default_providers(), session, [c.sha for c in commits])
            for r in rows:
                r.markers = markers.get(r.commit.sha, [])
            self.query_one("#pr-commits", CommitTable).set_rows(rows, has_more=False)
        else:
            self.diffs = await P.load_diff(session.git, P.RangeDiff(base, head))
            files = self.query_one("#pr-files", OptionList)
            files.clear_options()
            try:
                self.threads = await self.provider.threads(pr.number)
            except ForgeError:
                self.threads = []
            per_file = defaultdict(int)
            for t in self.threads:
                if not t.resolved:
                    per_file[t.path] += 1
            for d in self.diffs:
                label = Text(
                    f"{d.status} ",
                    style=GREEN if d.status == "A" else RED if d.status == "D" else "#B58CE8",
                )
                label.append(d.path)
                if per_file.get(d.path):
                    label.append(f"  💬{per_file[d.path]}", style=AMBER)
                files.add_option(Option(label, id=d.path))
            if self.diffs:
                files.highlighted = 0
                self._show_file(self.diffs[0].path)

    # -- files & threads ----------------------------------------------------------------------
    def _show_file(self, path: str) -> None:
        view = self.query_one("#pr-diff", DiffView)
        diff = next((d for d in self.diffs if d.path == path), None)
        marks: dict[tuple[str, int], str] = {}
        for t in self.threads:
            if t.path == path and t.line is not None:
                marks[(t.side, t.line)] = "✓" if t.resolved else "◆"
        view.line_marks = marks
        if self.detail is not None:
            view.set_diff(P.CommitDiff(self.detail.head_sha), diff, message="No changes")
        self._show_thread()

    @on(OptionList.OptionHighlighted, "#pr-files")
    def _file(self, event: OptionList.OptionHighlighted) -> None:
        if event.option.id:
            self._show_file(event.option.id)

    @on(DiffView.SelectionChanged, "#pr-diff")
    def _diff_cursor(self) -> None:
        self._show_thread()
        self.gp.refresh_hints()

    def _current_file(self) -> str | None:
        files = self.query_one("#pr-files", OptionList)
        if files.highlighted is None:
            return None
        return files.get_option_at_index(files.highlighted).id

    def _thread_at_cursor(self) -> ReviewThread | None:
        ref = self.query_one("#pr-diff", DiffView).cursor_line_ref()
        path = self._current_file()
        if ref is None or path is None:
            return None
        side, line = ref
        found = [t for t in self.threads if t.path == path and t.side == side and t.line == line]
        found.sort(key=lambda t: t.resolved)
        return found[0] if found else None

    def _show_thread(self) -> None:
        panel = self.query_one("#pr-thread", Static)
        thread = self._thread_at_cursor()
        panel.set_class(thread is not None, "-shown")
        if thread is None:
            return
        t = Text()
        t.append(
            "Resolved thread\n" if thread.resolved else "Review thread\n",
            style=f"bold {GREEN if thread.resolved else AMBER}",
        )
        for c in thread.comments:
            t.append(f"{c.author} ", style="bold")
            t.append(f"{ago(c.created)}\n" if c.created else "\n", style="dim")
            t.append(c.body.strip() + "\n")
        t.append("R reply · x " + ("unresolve" if thread.resolved else "resolve"), style="dim")
        panel.update(t)

    @work(exclusive=True, group="prs-threads")
    async def reload_threads(self) -> None:
        if self.provider is None or self.detail is None:
            return
        self.threads = await self.provider.threads(self.detail.number)
        path = self._current_file()
        if path:
            keep = self.query_one("#pr-diff", DiffView).cursor
            self._show_file(path)
            self.query_one("#pr-diff", DiffView).cursor = keep

    # -- events & context ------------------------------------------------------------------
    def handle_event(self, event: str, data: dict[str, Any]) -> None:
        if event == "prs.refresh":
            self._select_after = data.get("select")
            self.reload()
        elif event == "prs.threads":
            self.reload_threads()

    def context(self, base: SelectionContext) -> SelectionContext:
        focused = self.screen.focused
        form: dict[str, Any] = {"provider": self.provider}
        kwargs: dict[str, Any] = {"screen": "prs"}
        pr = self.detail
        if pr is not None:
            form["pr"] = pr
        if focused is self.query_one("#pr-list", DataTable):
            form["where"] = "list"
        elif focused is self.query_one("#pr-checks", DataTable) and pr is not None:
            form["where"] = "checks"
            row = self.query_one("#pr-checks", DataTable).cursor_row
            if 0 <= row < len(pr.checks):
                form["check"] = pr.checks[row]
        elif focused is self.query_one("#pr-diff", DiffView):
            form["where"] = "files"
            ref = self.query_one("#pr-diff", DiffView).cursor_line_ref()
            path = self._current_file()
            if ref and path:
                form["line"] = {"path": path, "side": ref[0], "line": ref[1]}
            thread = self._thread_at_cursor()
            if thread is not None:
                form["thread"] = thread
        elif focused is self.query_one("#pr-commits", CommitTable):
            table = self.query_one("#pr-commits", CommitTable)
            kwargs.update(focus=Scope.COMMIT, commits=tuple(table.selected_commits()))
        else:
            form["where"] = "overview"
        if "focus" not in kwargs and pr is not None:
            kwargs["focus"] = Scope.PR
        kwargs["form"] = form
        return SelectionContext(**{**base.__dict__, **kwargs})

    def hint_extras(self) -> list[tuple[str, str]]:
        return [("tab", "next pane")]
