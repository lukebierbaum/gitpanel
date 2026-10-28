"""Pull Requests tool window: list, overview, commits, files (with review threads), checks."""

from __future__ import annotations

import asyncio
import contextlib
import time
from dataclasses import dataclass
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
from ...forge.stacks import Stack, build_stacks
from ...git import patch as P
from ...git.graph import GraphRow
from ...git.log import LogFilter, load_page
from ...git.markers import PickedMarkers, RefMarkers, collect
from ...git.remote import NO_PROMPT
from ...git.stack import LocalStack, is_ancestor, local_chains, read_gh_stack
from ..base import ToolWindow
from ..colors import CHECK_ICON, CI_ICON, C
from ..fmt import ago, fmt_duration, plural
from ..widgets.commit_table import CommitTable, LogRow
from ..widgets.diff_view import DiffView
from ..widgets.file_list import FileList
from ..widgets.list_table import Col, Group, ListTable, words_match
from ..widgets.splitter import Splitter

REVIEW = {
    "APPROVED": ("Approved", "ok"),
    "CHANGES_REQUESTED": ("Changes", "fail"),
    "REVIEW_REQUIRED": ("Review", "pending"),
    "": ("", ""),
}


@dataclass(frozen=True)
class BranchRow:
    """A local stack layer that has no PR yet (shown in the Stacks list)."""

    branch: str


Item = PullRequest | BranchRow


def _name(item: Item) -> str:
    return item.title if isinstance(item, PullRequest) else item.branch


def item_key(item: Item) -> str:
    return str(item.number) if isinstance(item, PullRequest) else f"branch:{item.branch}"


INDEX_TTL = 60.0  # seconds before the all-open-PRs index is fetched again
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
        # stacks: every open PR (cheap fields), local stacks, and what was built from them
        self._index: list[PullRequest] | None = None
        self._index_at = 0.0
        self._local: list[LocalStack] = []
        self._stacks: list[Stack] = []
        self._layer_at: dict[str, tuple[Stack, int]] = {}  # item key → (stack, layer index)
        self._needs_restack: dict[str, tuple[bool, float]] = {}  # stack key → (needs, checked at)

    def compose(self) -> ComposeResult:
        with Horizontal(id="pr-main"):
            yield ListTable[Item](
                self._pr_columns(),
                key=item_key,
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
                arrange=self._arrange,
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
    def _pr_columns(self) -> list[Col[Item]]:
        def status(item: Item) -> tuple[CIState | None, str | None]:
            st = self._statuses.get(item.number) if isinstance(item, PullRequest) else None
            return (st.ci, st.review) if st is not None else (None, None)

        def title(item: Item) -> Text:
            t = Text()
            at = self._layer_at.get(item_key(item))
            if at is not None and self.pr_list.sort is None:
                stack, i = at
                last = i == len(stack.layers) - 1
                t.append("  " * stack.layers[i].depth + ("└ " if last else "├ "), style="dim")
            if isinstance(item, BranchRow):
                t.append(item.branch)
                t.append("  no PR yet", style="dim italic")
            else:
                t.append(item.title, style="dim" if item.draft else "")
                if item.draft:
                    t.append("  draft", style=f"italic {C.purple}")
                for label in item.labels[:3]:
                    t.append(f"  {label}", style=C.teal)
            if at is not None:
                t.append(f"  {at[1] + 1}/{len(at[0].layers)}", style="dim")
            return t

        def pr_only(fn: Any, empty: Any = "") -> Any:
            return lambda item: fn(item) if isinstance(item, PullRequest) else empty

        return [
            Col("num", "#", 6, pr_only(lambda pr: Text(f"#{pr.number}", style="bold")),
                sort=pr_only(lambda pr: pr.number, 0), descending_first=True),
            Col("ci", "CI", 4, pr_only(lambda pr: self._ci_cell(status(pr)[0])),
                sort=lambda it: CI_ORDER.get(status(it)[0] or "", 4)),
            Col("review", "Review", 9, pr_only(lambda pr: self._review_cell(status(pr)[1])),
                sort=lambda it: REVIEW_ORDER.get(status(it)[1] or "", 4)
                if status(it)[1] is not None else 4),
            Col("title", "Title", None, title, sort=lambda it: _name(it).casefold()),
            Col("author", "Author", 10, pr_only(lambda pr: pr.author),
                sort=pr_only(lambda pr: pr.author.casefold())),
            Col("updated", "Updated", 9, pr_only(lambda pr: ago(pr.updated)),
                sort=pr_only(lambda pr: pr.updated.timestamp() if pr.updated else 0.0, 0.0),
                descending_first=True),
        ]  # fmt: skip

    # -- stacks ------------------------------------------------------------------------------
    def _arrange(self, matched: list[Item]) -> list[Item | Group]:
        """Stacks together under a label row (whole stack kept when one PR matches), then
        the rest. The Stacks list shows only stacks."""
        stacks_only = self._query == "stacks"
        self._update_stacks()
        if not self.gp.config.pr_stack_view and not stacks_only:
            return list(matched)
        by_key = {item_key(i): i for i in self.pr_list.items}
        rows: list[Item | Group] = []
        rest: list[Item] = []
        placed: set[str] = set()
        shown_keys: set[str] = set()
        for item in matched:
            at = self._layer_at.get(item_key(item))
            if at is None:
                rest.append(item)
                continue
            stack = at[0]
            if stack.key in placed:
                continue
            placed.add(stack.key)
            rows.append(self._stack_group(stack))
            for layer in stack.layers:
                key = str(layer.pr) if layer.pr is not None else f"branch:{layer.branch}"
                if key in by_key and key not in shown_keys:
                    rows.append(by_key[key])
                    shown_keys.add(key)
        if stacks_only:
            return rows
        if rows and rest:
            rows.append(Group("group:rest", Text("Not in a stack", style=f"bold {C.other}")))
        return rows + rest

    def _update_stacks(self) -> None:
        listed = [i for i in self.pr_list.items if isinstance(i, PullRequest)]
        pool = {p.number: p for p in (self._index or [])}
        pool.update({p.number: p for p in listed})
        local = self._local if self._query == "stacks" else []
        self._stacks = build_stacks(list(pool.values()), self._statuses, local)
        self._layer_at = {}
        for stack in self._stacks:
            for i, layer in enumerate(stack.layers):
                key = str(layer.pr) if layer.pr is not None else f"branch:{layer.branch}"
                self._layer_at.setdefault(key, (stack, i))

    def _stack_group(self, stack: Stack) -> Group:
        """The label row above a stack: kind in the Title column, size and base beside it."""
        size = len(stack.layers)
        if stack.kind == "github":
            label = Text(stack.label, style=f"bold {C.other}")
            info = self._statuses.get(stack.layers[0].pr) if stack.layers[0].pr else None
            if info is not None and info.stack is not None:
                size = max(size, info.stack.size)
        else:
            label = Text(f"⚠ {stack.label[0].upper()}{stack.label[1:]}", style=f"bold {C.warn}")
        n_prs = len(stack.prs)
        if stack.kind == "github":
            n_prs = max(n_prs, size - stack.local_layers)
        count = plural(n_prs, "PR")
        if stack.local_layers:
            count += f" + {stack.local_layers}"
        return Group(
            f"group:{stack.key}",
            label,
            (
                ("author", Text(count, style="dim")),
                ("updated", Text(f"→ {stack.base}", style="dim")),
            ),
        )

    def stack_of(self, pr: PullRequest | None) -> tuple[Stack, int] | None:
        return self._layer_at.get(str(pr.number)) if pr is not None else None

    def _stack_strip(self, pr: PullRequest) -> Text | None:
        at = self.stack_of(pr)
        if at is None:
            return None
        stack, i = at
        t = Text()
        if stack.kind == "github":
            t.append(stack.label, style=f"bold {C.other}")
        else:
            t.append(f"⚠ {stack.label}", style=f"bold {C.warn}")
        t.append(f"  {stack.base}", style="dim")
        for j, layer in enumerate(stack.layers):
            t.append(" ← ", style="dim")
            name = f"#{layer.pr}" if layer.pr is not None else layer.branch
            st = self._statuses.get(layer.pr) if layer.pr is not None else None
            if j == i:
                t.append(f" {name} ", style=f"bold reverse {C.current}")
            else:
                t.append(name, style="bold" if layer.pr is not None else "dim italic")
            if st is not None and st.ci != "none":
                icon, color = CI_ICON[st.ci]
                t.append(f" {icon}", style=C[color])
        t.append(f"   {i + 1} of {len(stack.layers)}  ·  [ ] move", style="dim")
        needs = self._needs_restack.get(stack.key)
        if needs is not None and needs[0]:
            t.append("   ⟲ needs restack — S", style=f"bold {C.warn}")
        return t

    @work(exclusive=True, group="prs-stack-check")
    async def check_stack(self, stack: Stack) -> None:
        """Whether a layer no longer contains the one below it (or its base is gone): the
        stack needs a restack. Fetches the stack's branches, at most once a minute."""
        session = self.gp.session
        if session is None or self.provider is None or len(stack.layers) < 1:
            return
        seen = self._needs_restack.get(stack.key)
        if seen is not None and time.monotonic() - seen[1] < INDEX_TTL:
            return
        git = session.git
        remote = await self.provider.remote_name()
        names = {stack.base, *(ly.branch for ly in stack.layers if ly.pr is not None)}
        await git.run(
            "fetch", "--quiet", "--no-tags", remote,
            *(f"+refs/heads/{b}:refs/remotes/{remote}/{b}" for b in sorted(names)),
            check=False, env=NO_PROMPT,
        )  # fmt: skip
        # the base branch is gone (its PR was merged); being behind the base is fine
        needs = not await git.try_rev_parse(f"refs/remotes/{remote}/{stack.base}")
        prev: str | None = None
        for layer in stack.layers:
            ref = None
            for cand in (f"refs/remotes/{remote}/{layer.branch}", f"refs/heads/{layer.branch}"):
                if await git.try_rev_parse(cand):
                    ref = cand
                    break
            if ref is None:
                continue
            if prev is not None and not await is_ancestor(git, prev, ref):
                needs = True  # a layer no longer contains the one below it
            prev = ref
        self._needs_restack[stack.key] = (needs, time.monotonic())
        if self.detail is not None and self.stack_of(self.detail) is not None:
            self._render_overview(self.detail)

    def stack_move(self, delta: int) -> None:
        """Select the PR below (-1) / above (+1) the current one in its stack."""
        at = self.stack_of(self.detail)
        if at is None:
            return
        stack, i = at
        j = i + delta
        if not 0 <= j < len(stack.layers):
            self.gp.notify("Top of the stack" if delta > 0 else "Bottom of the stack", timeout=2)
            return
        layer = stack.layers[j]
        key = str(layer.pr) if layer.pr is not None else f"branch:{layer.branch}"
        listing = self.pr_list
        row = next((r for r, it in enumerate(listing.shown) if not isinstance(it, Group)
                    and item_key(it) == key), None)  # fmt: skip
        if row is not None:
            listing.table.move_cursor(row=row)
        elif layer.pr is not None:
            self._select(layer.pr, force=True)

    @work(exclusive=True, group="prs-index")
    async def load_index(self, *, force: bool = False) -> None:
        """All open PRs (cheap fields): finds chains and stacks beyond the current list."""
        if self.provider is None:
            return
        if not force and self._index is not None and time.monotonic() - self._index_at < INDEX_TTL:
            return
        try:
            index = await self.provider.open_pr_index()
        except ForgeError:
            return
        self._index, self._index_at = index, time.monotonic()
        self.pr_list.refresh_rows()

    async def _load_local_stacks(self) -> list[LocalStack]:
        session = self.gp.session
        if session is None or self.provider is None:
            return []
        recorded = read_gh_stack(session)
        info = await self.provider.repo()
        remote = await self.provider.remote_name()
        trunk = f"refs/remotes/{remote}/{info.default_branch}"
        if not await session.git.try_rev_parse(trunk):
            trunk = info.default_branch
        known = {b for st in recorded for b in st.branches}
        return recorded + await local_chains(session.git, trunk, exclude=known)

    def _pr_matches(self, item: Item, text: str) -> bool:
        if isinstance(item, BranchRow):
            return words_match(text, item.branch)
        pr = item
        # text GitHub already searched for is left to GitHub (it also matches descriptions,
        # which the list doesn't have)
        if text == self._searched.strip():
            return True
        st = self._statuses.get(pr.number)
        ci, review = (st.ci, st.review) if st is not None else (None, None)
        return pr_matches(pr, text, ci=ci, review=review)

    def _empty_text(self, text: str) -> str:
        label = QUERIES.get(self._query, ("", ""))[0]
        if self._query == "stacks" and not text:
            return (
                "No stacks in this repository yet.\n\nStacks are PRs built on each other: "
                "GitHub stacks (gh stack), PRs whose base is another PR's branch, and local "
                "branches stacked with gh stack init."
            )
        if text:
            return f"No pull requests in “{label}” match “{text}”."
        return f"No pull requests in “{label}”."

    @property
    def pr_list(self) -> ListTable[Item]:
        return self.query_one("#pr-left", ListTable)

    @property
    def prs(self) -> list[PullRequest]:
        return [i for i in self.pr_list.items if isinstance(i, PullRequest)]

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
            self._index = None
            self._local = []
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
            if query == "stacks":
                prs = await self.provider.open_pr_index()
                self._index, self._index_at = prs, time.monotonic()
                self._local = await self._load_local_stacks()
            else:
                prs = await self.provider.list_prs(query, search)
        except ForgeError as e:
            listing.show_message(f"{e}\n\n{e.hint}" if e.hint else str(e))
            listing.set_note("")
            return
        items: list[Item] = list(prs)
        if query == "stacks":
            have = {p.head for p in prs if not p.cross_repo}
            items += [BranchRow(b) for st in self._local for b in st.branches if b not in have]
        self._lists[key] = items  # type: ignore[assignment]
        listing.show_message(None)
        listing.set_note(f"updated {datetime.now():%H:%M}")
        self._fill_list(items, query, keep, force=True)
        self.load_statuses([p.number for p in prs])
        if query != "stacks":
            self.load_index()

    def _fill_list(
        self, items: list[Item], query: str, keep: int | None, *, force: bool = False
    ) -> None:
        self._query = query
        current = self.detail.number if self.detail else None
        self.pr_list.set_items(items, keep=str(keep or current) if (keep or current) else None)
        selected = self.pr_list.selected
        if isinstance(selected, PullRequest):
            self._select(selected.number, force=force)
        else:
            self._show_branch(selected)

    def _show_branch(self, row: BranchRow | None) -> None:
        """A list row without a PR: say what it is instead of PR details."""
        self.detail = None
        self._clear_detail()
        if row is None:
            return
        at = self._layer_at.get(item_key(row))
        t = Text(row.branch, style="bold")
        t.append("\nNo pull request yet", style="dim")
        if at is not None:
            stack, i = at
            if stack.kind == "local":
                t.append(f" — layer {i + 1} of {len(stack.layers)} of a local stack", style="dim")
                t.append(" (not a GitHub stack yet)", style=C.warn)
            else:
                t.append(f" — layer {i + 1} of {len(stack.layers)} on top of ", style="dim")
                t.append(stack.label, style=C.other)
                t.append(" (not on GitHub yet)", style=C.warn)
        self.query_one("#pr-meta", Static).update(t)
        self.gp.refresh_hints()

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
        if isinstance(event.item, PullRequest):
            self._select(event.item.number)
        elif isinstance(event.item, BranchRow):
            self._show_branch(event.item)
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
        at = self.stack_of(pr)
        if at is not None and not partial:
            self.check_stack(at[0])
        if partial:
            self.query_one("#pr-body", Markdown).update("*Loading…*")
        else:
            self._render_checks(pr)
        # commits/files only need the head sha and base, which the list already has
        self.call_later(self._load_active_tab)
        self.gp.refresh_hints()

    def _render_overview(self, pr: PullRequest) -> None:
        t = Text()
        strip = self._stack_strip(pr)
        if strip is not None:
            t.append_text(strip)
            t.append("\n")
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
        elif event == "prs.stack_move":
            self.stack_move(int(data.get("delta", 1)))
        elif event == "prs.threads":
            self.reload_threads()

    def context(self, base: SelectionContext) -> SelectionContext:
        focused = self.screen.focused
        form: dict[str, Any] = {"provider": self.provider}
        kwargs: dict[str, Any] = {"screen": "prs"}
        pr = self.detail
        if pr is not None:
            form["pr"] = pr
            at = self.stack_of(pr)
            if at is not None:
                form["stack"], form["stack_index"] = at
        selected = self.pr_list.selected
        if isinstance(selected, BranchRow) and focused is self.query_one("#pr-list", DataTable):
            form["branch_row"] = selected
            at = self._layer_at.get(item_key(selected))
            if at is not None:
                form["stack"], form["stack_index"] = at
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
        if self.stack_of(self.detail) is not None:
            hints.append(("[ ]", "stack"))
        return [*hints, ("tab", "next pane")]
