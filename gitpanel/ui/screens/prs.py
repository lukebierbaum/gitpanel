"""Pull Requests tool window: list, overview, commits, files (with review threads), checks."""

from __future__ import annotations

import asyncio
import contextlib
import time
from datetime import UTC, datetime
from typing import Any

from rich.text import Text
from textual import on, work
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.timer import Timer
from textual.widgets import (
    DataTable,
    Markdown,
    Select,
    Static,
    TabbedContent,
    TabPane,
)

from ...actions.context import Scope, SelectionContext
from ...forge.filters import pr_matches
from ...forge.github import QUERIES, GitHub, detect
from ...forge.models import Check, CIState, PRStatus, PullRequest, ReviewThread
from ...forge.runner import ForgeError
from ...git import patch as P
from ...git.graph import GraphRow
from ...git.log import LogFilter, load_page
from ...git.markers import PickedMarkers, RefMarkers, collect
from ..base import ToolWindow
from ..colors import CHECK_ICON, CI_ICON, C
from ..fmt import ago, fmt_duration
from ..widgets.commit_table import CommitTable, LogRow
from ..widgets.diff_view import DiffView
from ..widgets.file_list import FileList
from ..widgets.list_table import Col, ListTable, words_match
from ..widgets.splitter import Splitter

REVIEW = {
    "APPROVED": ("Approved", "ok"),
    "CHANGES_REQUESTED": ("Changes", "fail"),
    "REVIEW_REQUIRED": ("Review", "pending"),
    "": ("", ""),
}


DEBOUNCE = 0.2  # seconds the list cursor must rest before a PR's details are fetched
DETAIL_TTL = 60.0  # cached details younger than this aren't fetched again on highlight


CI_ORDER = {"failure": 0, "pending": 1, "success": 2, "none": 3}
REVIEW_ORDER = {"CHANGES_REQUESTED": 0, "REVIEW_REQUIRED": 1, "APPROVED": 2, "": 3}
CHECK_ORDER = {"failure": 0, "cancelled": 1, "pending": 2, "success": 3, "neutral": 4, "skipped": 5}


class PRsView(ToolWindow):
    name_id = "prs"
    stale = True

    DEFAULT_CSS = """
    PRsView { height: 1fr; }
    PRsView #pr-main { height: 1fr; }
    PRsView #pr-left { width: 50%; min-width: 30; }
    PRsView #pr-query { width: 22; }
    PRsView TabbedContent { height: 1fr; width: 1fr; }
    PRsView #pr-overview { padding: 0 1; }
    PRsView #pr-files { width: 26; }
    PRsView #pr-thread { height: auto; max-height: 12; padding: 0 1; background: $panel;
                         display: none; }
    PRsView #pr-thread.-shown { display: block; }
    """

    def __init__(self) -> None:
        super().__init__(id="prs-view")
        self.provider: GitHub | None = None
        self.detail: PullRequest | None = None
        self.threads: list[ReviewThread] = []
        self._loaded_tabs: set[str] = set()
        self._timer: Timer | None = None
        self._select_after: int | None = None
        self._detected = False
        # caches so revisiting a list or a PR is instant while it refreshes in the background
        self._lists: dict[tuple[str, str], list[PullRequest]] = {}
        self._statuses: dict[int, PRStatus] = {}
        self._details: dict[int, tuple[PullRequest, float]] = {}
        self._pending: Timer | None = None
        self._query = "mine"
        self._searched = ""  # text last sent to GitHub as a search (enter in the filter box)

    def compose(self) -> ComposeResult:
        with Horizontal(id="pr-main"):
            yield ListTable[PullRequest](
                self._pr_columns(),
                key=lambda pr: str(pr.number),
                matches=self._pr_matches,
                placeholder="/ Filter: words, author:, label:, status:failure, review:approved, "
                "draft:false — enter searches GitHub",
                controls=[
                    Select(
                        [(label, key) for key, (label, _) in QUERIES.items()],
                        value="mine",
                        allow_blank=False,
                        compact=True,
                        id="pr-query",
                    )
                ],
                empty=self._empty_text,
                state_key="prs.list",
                table_id="pr-list",
                id="pr-left",
            )
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
                    yield FileList(id="pr-files")
                    yield Splitter("prs.files", "x", minimum=20)
                    with Vertical():
                        yield DiffView(id="pr-diff")
                        yield Static("", id="pr-thread")
                with TabPane("Checks", id="tab-checks"):
                    yield ListTable[tuple[int, Check]](
                        [
                            Col(
                                "state",
                                "",
                                2,
                                lambda ic: self._check_icon(ic[1]),
                                sort=lambda ic: CHECK_ORDER.get(ic[1].state, 9),
                            ),
                            Col(
                                "name",
                                "Check",
                                None,
                                lambda ic: ic[1].name,
                                sort=lambda ic: ic[1].name.casefold(),
                            ),
                            Col(
                                "workflow",
                                "Workflow",
                                18,
                                lambda ic: ic[1].workflow,
                                sort=lambda ic: ic[1].workflow.casefold(),
                            ),
                            Col(
                                "time",
                                "Time",
                                8,
                                lambda ic: fmt_duration(ic[1].duration),
                                sort=lambda ic: ic[1].duration or 0.0,
                                descending_first=True,
                            ),
                        ],  # fmt: skip
                        key=lambda ic: str(ic[0]),
                        matches=lambda ic, text: words_match(
                            text, f"{ic[1].name} {ic[1].workflow} {ic[1].state}"
                        ),
                        placeholder="/ Filter checks (name, workflow, failure …)",
                        empty=lambda text: f"No checks match “{text}”." if text else "No checks.",
                        state_key="prs.checks",
                        table_id="pr-checks",
                        zebra=False,
                        id="pr-checks-list",
                    )

    # -- list columns ------------------------------------------------------------------------
    def _pr_columns(self) -> list[Col[PullRequest]]:
        def status(pr: PullRequest) -> tuple[CIState | None, str | None]:
            st = self._statuses.get(pr.number)
            return (st.ci, st.review) if st is not None else (None, None)

        def title(pr: PullRequest) -> Text:
            t = Text(pr.title, style="dim" if pr.draft else "")
            if pr.draft:
                t.append("  draft", style=f"italic {C.purple}")
            for label in pr.labels[:3]:
                t.append(f"  {label}", style=C.teal)
            return t

        return [
            Col("num", "#", 6, lambda pr: Text(f"#{pr.number}", style="bold"),
                sort=lambda pr: pr.number, descending_first=True),
            Col("ci", "CI", 4, lambda pr: self._ci_cell(status(pr)[0]),
                sort=lambda pr: CI_ORDER.get(status(pr)[0] or "", 4)),
            Col("review", "Review", 9, lambda pr: self._review_cell(status(pr)[1]),
                sort=lambda pr: REVIEW_ORDER.get(status(pr)[1] or "", 4)
                if status(pr)[1] is not None else 4),
            Col("title", "Title", None, title, sort=lambda pr: pr.title.casefold()),
            Col("author", "Author", 10, lambda pr: pr.author,
                sort=lambda pr: pr.author.casefold()),
            Col("updated", "Updated", 9, lambda pr: ago(pr.updated),
                sort=lambda pr: pr.updated.timestamp() if pr.updated else 0.0,
                descending_first=True),
        ]  # fmt: skip

    def _pr_matches(self, pr: PullRequest, text: str) -> bool:
        # text GitHub already searched for is left to GitHub (it also matches descriptions,
        # which the list doesn't have)
        if text == self._searched.strip():
            return True
        st = self._statuses.get(pr.number)
        ci, review = (st.ci, st.review) if st is not None else (None, None)
        return pr_matches(pr, text, ci=ci, review=review)

    def _empty_text(self, text: str) -> str:
        label = QUERIES.get(self._query, ("", ""))[0]
        if text:
            return f"No pull requests in “{label}” match “{text}”."
        return f"No pull requests in “{label}”."

    @property
    def pr_list(self) -> ListTable[PullRequest]:
        return self.query_one("#pr-left", ListTable)

    @property
    def prs(self) -> list[PullRequest]:
        return self.pr_list.items

    @staticmethod
    def _check_icon(c: Check) -> Text:
        icon, color = CHECK_ICON[c.state]
        return Text(icon, style=f"bold {C[color]}")

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
    @work(exclusive=True, group="prs-list")
    async def reload(self) -> None:
        session = self.gp.session
        if session is None:
            return
        if not self._detected or (self.provider and self.provider.session is not session):
            self.provider = await detect(session)
            self._detected = True
            self._lists.clear()
            self._statuses.clear()
            self._details.clear()
        listing = self.pr_list
        if self.provider is None:
            listing.show_message(
                "This repository has no github.com remote, or the GitHub CLI (gh) "
                "isn't installed.\n\nInstall gh from https://cli.github.com and run "
                "`gh auth login`."
            )
            listing.set_note("")
            return
        query = str(self.query_one("#pr-query", Select).value)
        search = self._searched
        key = (query, search)
        keep = self._select_after or (self.detail.number if self.detail else None)
        self._select_after = None
        cached = self._lists.get(key)
        if cached is not None and [p.number for p in cached] != [p.number for p in self.prs]:
            self._fill_list(cached, query, keep)
        listing.set_note("loading…")
        try:
            prs = await self.provider.list_prs(query, search)
        except ForgeError as e:
            listing.show_message(f"{e}\n\n{e.hint}" if e.hint else str(e))
            listing.set_note("")
            return
        self._lists[key] = prs
        listing.show_message(None)
        listing.set_note(f"updated {datetime.now():%H:%M}")
        self._fill_list(prs, query, keep, force=True)
        self.load_statuses([p.number for p in prs])

    def _fill_list(
        self, prs: list[PullRequest], query: str, keep: int | None, *, force: bool = False
    ) -> None:
        self._query = query
        current = self.detail.number if self.detail else None
        self.pr_list.set_items(prs, keep=str(keep or current) if (keep or current) else None)
        selected = self.pr_list.selected
        if selected is not None:
            self._select(selected.number, force=force)
        else:
            self.detail = None
            self._clear_detail()

    @staticmethod
    def _ci_cell(ci: CIState | None) -> Text:
        if ci is None:
            return Text("…", style="dim")
        icon, color = CI_ICON[ci]
        return Text(icon, style=f"bold {C[color]}")

    @staticmethod
    def _review_cell(review: str | None) -> Text:
        text, color = REVIEW.get(review or "", ("", ""))
        return Text(text, style=C[color])

    @work(exclusive=True, group="prs-status")
    async def load_statuses(self, numbers: list[int]) -> None:
        """CI and review state for the listed PRs (a separate, slower query)."""
        if self.provider is None:
            return
        try:
            found = await self.provider.statuses(numbers)
        except ForgeError:
            return
        self._statuses.update(found)
        self.pr_list.refresh_rows()  # cells, filter and sort can all depend on these

    @on(Select.Changed, "#pr-query")
    def _query_changed(self) -> None:
        self.reload()

    @on(ListTable.FilterSubmitted, "#pr-left")
    def _search(self, event: ListTable.FilterSubmitted) -> None:
        self._searched = event.text
        self.reload()

    @on(ListTable.FilterChanged, "#pr-left")
    def _filter_cleared(self, event: ListTable.FilterChanged) -> None:
        if not event.text and self._searched:  # back to the unsearched list
            self._searched = ""
            self.reload()

    @on(ListTable.Highlighted, "#pr-left")
    def _row(self, event: ListTable.Highlighted) -> None:
        if event.item is not None:
            self._select(event.item.number)
        self.gp.refresh_hints()

    def _select(self, number: int, *, force: bool = False) -> None:
        """Show a PR: cached details at once, fresh ones once the cursor stops moving."""
        if not force and self.detail is not None and self.detail.number == number:
            return
        cached = self._details.get(number)
        if cached is not None:
            self._show(cached[0])
            if not force and time.monotonic() - cached[1] < DETAIL_TTL:
                return
        else:
            summary = next((p for p in self.prs if p.number == number), None)
            if summary is not None:
                self._show(summary, partial=True)
        if self._pending is not None:
            self._pending.stop()
        self._pending = self.set_timer(DEBOUNCE, lambda: self.show_pr(number, force=True))

    def _clear_detail(self) -> None:
        self.query_one("#pr-meta", Static).update("")
        self.query_one("#pr-body", Markdown).update("")
        self.query_one("#pr-conversation", Static).update("")
        self.query_one("#pr-commits", CommitTable).set_rows([], has_more=False)
        self.query_one("#pr-files", FileList).set_files(None, [])
        self.query_one("#pr-diff", DiffView).set_diff(None, None)
        self.query_one("#pr-checks-list", ListTable).set_items([])

    @work(exclusive=True, group="prs-detail")
    async def show_pr(self, number: int, *, force: bool = False) -> None:
        if self.provider is None:
            return
        if not force and self.detail is not None and self.detail.number == number:
            return
        summary = self.detail if self.detail is not None and self.detail.number == number else None
        if summary is not None:
            self.prefetch(summary)  # the commits download while GitHub sends the details
        try:
            pr = await self.provider.pr(number)
        except ForgeError as e:
            self.gp.notify(str(e), severity="error")
            return
        self._details[number] = (pr, time.monotonic())
        old = self._statuses.get(number)
        self._statuses[number] = PRStatus(pr.ci, pr.review_decision, old.stack if old else None)
        if self.detail is not None and self.detail.number != number:
            return  # the cursor moved on meanwhile
        self._show(pr)

    def _show(self, pr: PullRequest, *, partial: bool = False) -> None:
        """Render a PR; `partial` = only the list summary is known so far."""
        old = self.detail
        self.detail = pr
        if old is None or old.number != pr.number or old.head_sha != pr.head_sha:
            self._loaded_tabs.clear()
            self._clear_detail()
        self._render_overview(pr)
        if partial:
            self.query_one("#pr-body", Markdown).update("*Loading…*")
        else:
            self._render_checks(pr)
        # commits/files only need the head sha and base, which the list already has
        self.call_later(self._load_active_tab)
        self.gp.refresh_hints()

    def _render_overview(self, pr: PullRequest) -> None:
        t = Text()
        t.append(f"#{pr.number} {pr.title}\n", style="bold")
        state_color = {"OPEN": C.green, "MERGED": C.purple, "CLOSED": C.red}.get(pr.state, "")
        t.append(f"{pr.state.lower()}{' · draft' if pr.draft else ''}", style=f"bold {state_color}")
        t.append(f"   {pr.author} wants to merge {pr.head} into {pr.base}", style="dim")
        t.append(f"   +{pr.additions} −{pr.deletions} in {pr.changed_files} files\n", style="dim")
        icon, color = CI_ICON[pr.ci]
        failing = sum(c.state == "failure" for c in pr.checks)
        t.append(f"{icon} checks: ", style=C[color])
        t.append(f"{len(pr.checks)} total, {failing} failing" if pr.checks else "none", style="dim")
        review, rcolor = REVIEW.get(pr.review_decision, ("", ""))
        if review:
            t.append("   review: ", style="dim")
            t.append(review, style=C[rcolor])
        if pr.mergeable == "CONFLICTING":
            t.append("   merge conflicts with base", style=C.red)
        if pr.auto_merge:
            t.append("   auto-merge on", style=C.green)
        t.append("\n")
        if pr.labels:
            t.append("labels: " + ", ".join(pr.labels) + "\n", style=C.teal)
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
            conv.append("\nConversation\n", style=f"bold {C.purple}")
        for when, who, what, body in events:
            conv.append(f"{who} ", style="bold")
            conv.append(f"{what} · {ago(when)} ago\n" if when else f"{what}\n", style="dim")
            if body.strip():
                conv.append(body.strip() + "\n")
            conv.append("\n")
        self.query_one("#pr-conversation", Static).update(conv)

    def _render_checks(self, pr: PullRequest) -> None:
        self.query_one("#pr-checks-list", ListTable).set_items(list(enumerate(pr.checks)))

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

    @work(exclusive=True, group="prs-prefetch")
    async def prefetch(self, pr: PullRequest) -> None:
        """Fetch a PR's commits ahead of time so Commits/Files open without waiting."""
        if self.provider is None or not pr.head_sha:
            return
        with contextlib.suppress(Exception):  # best effort; the tab reports real problems
            await self.provider.fetch_head(pr)

    @work(exclusive=True, group="prs-tab")
    async def load_tab(self, tab: str, pr: PullRequest) -> None:
        assert self.provider is not None
        session = self.gp.session
        if session is None:
            return
        provider = self.provider

        async def threads() -> list[ReviewThread]:
            if tab != "tab-files":
                return []
            try:
                return await provider.threads(pr.number)
            except ForgeError:
                return []

        # review threads come from GitHub while git fetches the commits
        threads_task = asyncio.ensure_future(threads())
        try:
            head = await provider.fetch_head(pr)
            base = await provider.merge_base(pr, head)
        except Exception as e:  # noqa: BLE001 - network/permission problems shouldn't crash
            threads_task.cancel()
            self._loaded_tabs.discard(tab)
            self.gp.notify(f"Couldn't fetch #{pr.number}: {e}", severity="error")
            return
        if self.detail is None or self.detail.number != pr.number:
            return
        if tab == "tab-commits":
            commits = await load_page(session.git, LogFilter(refs=(f"{base}..{head}",)), 0, 500)
            rows = [LogRow(c, GraphRow(c.sha, 0, (("●", 0),))) for c in commits]
            table = self.query_one("#pr-commits", CommitTable)
            table.set_rows(rows, has_more=False)
            # branch labels and already-picked marks; pushed/unpushed colours are moot here
            # (a PR's commits are on GitHub) and cost seconds on big repos
            table.update_markers(
                await collect([RefMarkers(), PickedMarkers()], session, [c.sha for c in commits])
            )
        else:
            diffs = await P.load_diff(session.git, P.RangeDiff(base, head))
            self.threads = []
            files = self.query_one("#pr-files", FileList)
            files.set_files(P.CommitDiff(head), diffs)
            # review threads arrive separately; badge the files and mark lines when they do
            self.threads = await threads_task
            if self.detail is None or self.detail.number != pr.number:
                return
            files.redecorate(self._thread_badge)
            path = self._current_file()
            if path and self.threads:
                keep = self.query_one("#pr-diff", DiffView).cursor
                self._show_file(path)
                self.query_one("#pr-diff", DiffView).cursor = keep

    def _thread_badge(self, d: P.FileDiff) -> Text | None:
        open_threads = sum(t.path == d.path and not t.resolved for t in self.threads)
        return Text(f"  💬{open_threads}", style=C.amber) if open_threads else None

    @property
    def diffs(self) -> list[P.FileDiff]:
        return self.query_one("#pr-files", FileList).diffs

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

    @on(FileList.Selected, "#pr-files")
    def _file(self, event: FileList.Selected) -> None:
        if event.diff is not None:
            self._show_file(event.diff.path)

    @on(DiffView.SelectionChanged, "#pr-diff")
    def _diff_cursor(self) -> None:
        self._show_thread()
        self.gp.refresh_hints()

    def _current_file(self) -> str | None:
        current = self.query_one("#pr-files", FileList).current
        return current.path if current is not None else None

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
            style=f"bold {C.green if thread.resolved else C.amber}",
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
            selected = self.query_one("#pr-checks-list", ListTable).selected
            if selected is not None:
                form["check"] = selected[1]
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
        return base.with_(**kwargs)

    def hint_extras(self) -> list[tuple[str, str]]:
        hints = [h for lt in self.query(ListTable) for h in lt.hints()]
        return [*hints, ("tab", "next pane")]
