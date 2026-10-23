"""GitHub pull requests through the `gh` CLI, plus the local-git side (fetching PR heads)."""

from __future__ import annotations

import asyncio
import json
import re
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from ..git.remote import NO_PROMPT
from ..git.session import RepoSession
from .models import ROLLUP_STATE, PRStatus, PullRequest, ReviewThread, StackInfo
from .runner import ForgeError, Gh

# Only cheap fields: asking `gh pr list` for statusCheckRollup / reviewDecision / additions
# makes GitHub resolve them for every PR (≈11 s for 100 PRs vs ≈1.5 s). The list gets CI and
# review state afterwards from `statuses()`.
LIST_FIELDS = (
    "number,title,author,state,isDraft,headRefName,baseRefName,headRefOid,labels,updatedAt,"
    "createdAt,url,isCrossRepository,headRepositoryOwner"
)
SUMMARY_FIELDS = LIST_FIELDS + ",additions,deletions,changedFiles,reviewDecision,statusCheckRollup"
DETAIL_FIELDS = (
    SUMMARY_FIELDS
    + ",body,mergeable,mergeStateStatus,reviewRequests,latestReviews,comments,autoMergeRequest"
)
STATUS_BATCH = 10  # PRs per GraphQL request; batches run in parallel
STATUS_PARALLEL = 8
BASE_FETCH_TTL = 300.0  # seconds before a PR's base branch is fetched again

Query = Literal["mine", "review", "assigned", "open", "closed"]
QUERIES: dict[str, tuple[str, str]] = {
    # name → (label, search qualifier)
    "mine": ("Mine", "author:@me"),
    "review": ("Review requested", "review-requested:@me"),
    "assigned": ("Assigned", "assignee:@me"),
    "open": ("All open", ""),
    "closed": ("Closed", ""),
    "stacks": ("Stacks", ""),  # every stack in the repository (see PRsView)
}
Verdict = Literal["approve", "request-changes", "comment"]
MergeMethod = Literal["merge", "squash", "rebase"]

_GITHUB_URL = re.compile(r"github\.com[:/]+([^/\s]+)/([^/\s]+?)(?:\.git)?/?$")

THREADS_QUERY = """
query($owner: String!, $name: String!, $number: Int!) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      reviewThreads(first: 100) {
        nodes {
          id isResolved isOutdated path line diffSide
          comments(first: 50) { nodes { id databaseId author { login } body createdAt } }
        }
      }
    }
  }
}
"""
RESOLVE = "mutation($id: ID!) { resolveReviewThread(input: {threadId: $id}) { thread { id } } }"
UNRESOLVE = "mutation($id: ID!) { unresolveReviewThread(input: {threadId: $id}) { thread { id } } }"


@dataclass(frozen=True)
class RepoInfo:
    owner: str
    name: str
    default_branch: str
    url: str


async def github_remotes(session: RepoSession) -> dict[str, tuple[str, str]]:
    """remote name → (owner, repo) for remotes that point at github.com."""
    res = await session.git.run("remote", "-v", read_only=True, check=False)
    out: dict[str, tuple[str, str]] = {}
    for line in res.text.splitlines():
        parts = line.split()
        if len(parts) >= 2:
            m = _GITHUB_URL.search(parts[1])
            if m:
                out[parts[0]] = (m.group(1), m.group(2))
    return out


async def detect(session: RepoSession) -> GitHub | None:
    """A GitHub provider when the repo has a github.com remote and `gh` is installed."""
    if not Gh.available():
        return None
    if not await github_remotes(session):
        return None
    return GitHub(session)


class GitHub:
    name = "GitHub"

    def __init__(self, session: RepoSession) -> None:
        self.session = session
        self._repo: RepoInfo | None = None
        self._base_fetched: dict[str, float] = {}  # base branch → monotonic time of last fetch
        self._fetching: dict[tuple[int, str], asyncio.Task[str]] = {}  # (PR, head sha) in flight

    @property
    def gh(self) -> Gh:
        return Gh(self.session.active.path)

    # -- repository ---------------------------------------------------------------
    async def repo(self) -> RepoInfo:
        if self._repo is None:
            d = await self.gh.json("repo", "view", "--json", "owner,name,defaultBranchRef,url")
            self._repo = RepoInfo(
                owner=str((d.get("owner") or {}).get("login", "")),
                name=str(d.get("name", "")),
                default_branch=str((d.get("defaultBranchRef") or {}).get("name") or "main"),
                url=str(d.get("url", "")),
            )
        return self._repo

    async def remote_name(self) -> str:
        """The local remote that points at the base repository (where pull/N/head lives)."""
        remotes = await github_remotes(self.session)
        info = await self.repo()
        for name, (owner, repo) in remotes.items():
            if owner.lower() == info.owner.lower() and repo.lower() == info.name.lower():
                return name
        return "origin" if "origin" in remotes or not remotes else next(iter(remotes))

    # -- reading --------------------------------------------------------------------
    async def list_prs(
        self, query: str = "mine", search: str = "", limit: int = 100
    ) -> list[PullRequest]:
        qualifier = QUERIES.get(query, ("", ""))[1]
        args = ["pr", "list", "--limit", str(limit), "--json", LIST_FIELDS]
        args += ["--state", "closed" if query == "closed" else "open"]
        terms = " ".join(t for t in (qualifier, search.strip()) if t)
        if terms:
            args += ["--search", terms]
        data = await self.gh.json(*args)
        return [PullRequest.parse(d) for d in data or []]

    async def pr(self, number: int) -> PullRequest:
        d = await self.gh.json("pr", "view", str(number), "--json", DETAIL_FIELDS)
        return PullRequest.parse(d, detailed=True)

    async def statuses(self, numbers: Sequence[int]) -> dict[int, PRStatus]:
        """PR number → CI rollup, review decision and GitHub stack, in small parallel
        GraphQL batches."""
        if not numbers:
            return {}
        info = await self.repo()
        limit = asyncio.Semaphore(STATUS_PARALLEL)

        async def batch(chunk: Sequence[int]) -> dict[int, PRStatus]:
            fields = " ".join(
                f"p{n}: pullRequest(number: {n}) {{ reviewDecision "
                "commits(last: 1) { nodes { commit { statusCheckRollup { state } } } } "
                "stack { number size baseRefName } stackEntry { position } }"
                for n in chunk
            )
            query = (
                "query($owner: String!, $name: String!) "
                f"{{ repository(owner: $owner, name: $name) {{ {fields} }} }}"
            )
            async with limit:
                d = await self.gh.json(
                    "api", "graphql", "-f", f"query={query}",
                    "-F", f"owner={info.owner}", "-F", f"name={info.name}",
                )  # fmt: skip
            repo = ((d or {}).get("data") or {}).get("repository") or {}
            out: dict[int, PRStatus] = {}
            for n in chunk:
                pr = repo.get(f"p{n}")
                if not pr:
                    continue
                nodes = (pr.get("commits") or {}).get("nodes") or [{}]
                state = (
                    ((nodes[-1] or {}).get("commit") or {}).get("statusCheckRollup") or {}
                ).get("state")
                stack, entry = pr.get("stack"), pr.get("stackEntry")
                out[n] = PRStatus(
                    ROLLUP_STATE.get(str(state), "none"),
                    str(pr.get("reviewDecision") or ""),
                    StackInfo(
                        int(stack["number"]),
                        int((entry or {}).get("position") or 0),
                        int(stack.get("size") or 0),
                        str(stack.get("baseRefName") or ""),
                    )
                    if stack
                    else None,
                )
            return out

        chunks = [numbers[i : i + STATUS_BATCH] for i in range(0, len(numbers), STATUS_BATCH)]
        merged: dict[int, PRStatus] = {}
        for part in await asyncio.gather(*(batch(c) for c in chunks)):
            merged.update(part)
        return merged

    async def open_pr_index(self, limit: int = 300) -> list[PullRequest]:
        """Every open PR with the cheap list fields — for finding stacks across the repo."""
        data = await self.gh.json(
            "pr", "list", "--state", "open", "--limit", str(limit), "--json", LIST_FIELDS
        )
        return [PullRequest.parse(d) for d in data or []]

    async def stack_members(self, pr_number: int) -> list[int]:
        """The PR numbers of the GitHub stack `pr_number` is in, bottom → top ([] if none)."""
        info = await self.repo()
        query = (
            "query($owner: String!, $name: String!, $number: Int!) { repository(owner: $owner,"
            " name: $name) { pullRequest(number: $number) { stack { entries(first: 100) {"
            " nodes { position pullRequest { number } } } } } } }"
        )
        d = await self.gh.json(
            "api", "graphql", "-f", f"query={query}", "-F", f"owner={info.owner}",
            "-F", f"name={info.name}", "-F", f"number={pr_number}",
        )  # fmt: skip
        pr = (((d or {}).get("data") or {}).get("repository") or {}).get("pullRequest") or {}
        nodes = ((pr.get("stack") or {}).get("entries") or {}).get("nodes") or []
        nodes = sorted((n for n in nodes if n.get("pullRequest")), key=lambda n: n["position"])
        return [int(n["pullRequest"]["number"]) for n in nodes]

    async def current_pr(self) -> PullRequest | None:
        """The open PR for the checked-out branch, if any."""
        res = await self.gh.run("pr", "view", "--json", SUMMARY_FIELDS, check=False)
        if not res.ok or not res.stdout.strip():
            return None
        pr = PullRequest.parse(json.loads(res.stdout))
        return pr if pr.state == "OPEN" else None

    async def threads(self, number: int) -> list[ReviewThread]:
        info = await self.repo()
        d = await self.gh.json(
            "api",
            "graphql",
            "-f",
            f"query={THREADS_QUERY}",
            "-F",
            f"owner={info.owner}",
            "-F",
            f"name={info.name}",
            "-F",
            f"number={number}",
        )
        nodes = (
            (((d or {}).get("data") or {}).get("repository") or {}).get("pullRequest") or {}
        ).get("reviewThreads", {}).get("nodes") or []
        return [ReviewThread.parse(n) for n in nodes]

    # -- local git side ---------------------------------------------------------------
    async def fetch_head(self, pr: PullRequest) -> str:
        """Fetch the PR head (and its base) so commits and diffs come from local git.

        One `git fetch` for both, skipped when the head is already here and the base was
        fetched in the last few minutes. Callers asking for the same head while a fetch is
        running share it (a prefetch and the tab that needs it), and cancelling one caller
        doesn't abort the fetch for the others.
        """
        key = (pr.number, pr.head_sha)
        task = self._fetching.get(key)
        if task is None or task.done() and (task.cancelled() or task.exception()):
            task = asyncio.ensure_future(self._fetch_head(pr))
            self._fetching[key] = task
            task.add_done_callback(lambda _: self._fetching.pop(key, None))
        return await asyncio.shield(task)

    async def _fetch_head(self, pr: PullRequest) -> str:
        remote = await self.remote_name()
        ref = f"refs/gitpanel/pr/{pr.number}"
        git = self.session.git
        have = await git.try_rev_parse(ref)
        refspecs = []
        if not (have and pr.head_sha and have == pr.head_sha):
            refspecs.append(f"+refs/pull/{pr.number}/head:{ref}")
        fetched = self._base_fetched.get(pr.base)
        if pr.base and (fetched is None or time.monotonic() - fetched > BASE_FETCH_TTL):
            refspecs.append(f"+refs/heads/{pr.base}:refs/remotes/{remote}/{pr.base}")
        if refspecs:
            fetch = ("fetch", "--quiet", "--no-tags", remote)
            res = await git.run(*fetch, *refspecs, env=NO_PROMPT, check=False)
            if res.ok:
                self._base_fetched[pr.base] = time.monotonic()
            elif refspecs[0].startswith("+refs/pull/"):
                # the base may be gone (deleted after merging); the head is what matters
                await git.run(*fetch, refspecs[0], env=NO_PROMPT)
        return await git.rev_parse(ref)

    async def merge_base(self, pr: PullRequest, head: str | None = None) -> str:
        remote = await self.remote_name()
        git = self.session.git
        if head is None:
            head = await self.fetch_head(pr)
        base = f"refs/remotes/{remote}/{pr.base}"
        res = await git.run("merge-base", base, head, read_only=True)
        return res.text.strip()

    async def checkout(self, number: int, branch: str | None = None) -> None:
        args = ["pr", "checkout", str(number)]
        if branch:
            args += ["--branch", branch]
        await self.gh.run(*args, read_only=False)

    # -- acting ---------------------------------------------------------------------------
    async def create(
        self,
        head: str,
        base: str,
        title: str,
        body: str,
        *,
        draft: bool = False,
        reviewers: Sequence[str] = (),
    ) -> str:
        args = [
            "pr",
            "create",
            "--head",
            head,
            "--base",
            base,
            "--title",
            title,
            "--body-file",
            "-",
        ]
        if draft:
            args.append("--draft")
        for r in reviewers:
            args += ["--reviewer", r]
        res = await self.gh.run(*args, input=body, read_only=False)
        return res.stdout.strip().splitlines()[-1] if res.stdout.strip() else ""

    async def review(self, number: int, verdict: Verdict, body: str = "") -> None:
        args = ["pr", "review", str(number), f"--{verdict}"]
        if body or verdict != "approve":
            args += ["--body-file", "-"]
            await self.gh.run(*args, input=body, read_only=False)
        else:
            await self.gh.run(*args, read_only=False)

    async def comment(self, number: int, body: str) -> None:
        await self.gh.run(
            "pr", "comment", str(number), "--body-file", "-", input=body, read_only=False
        )

    async def merge(
        self, number: int, method: MergeMethod, *, delete_branch: bool = False, auto: bool = False
    ) -> None:
        args = ["pr", "merge", str(number), f"--{method}"]
        if delete_branch:
            args.append("--delete-branch")
        if auto:
            args.append("--auto")
        await self.gh.run(*args, read_only=False)

    async def close(self, number: int) -> None:
        await self.gh.run("pr", "close", str(number), read_only=False)

    async def reopen(self, number: int) -> None:
        await self.gh.run("pr", "reopen", str(number), read_only=False)

    async def ready(self, number: int, *, draft: bool = False) -> None:
        args = ["pr", "ready", str(number)]
        if draft:
            args.append("--undo")
        await self.gh.run(*args, read_only=False)

    async def rerun_failed(self, pr: PullRequest) -> list[str]:
        run_ids = sorted({c.run_id for c in pr.checks if c.state == "failure" and c.run_id})
        if not run_ids:
            raise ForgeError("No failed GitHub Actions runs to re-run")
        for run_id in run_ids:
            await self.gh.run("run", "rerun", run_id, "--failed", read_only=False)
        return run_ids

    async def add_line_comment(
        self, pr: PullRequest, path: str, line: int, side: Literal["LEFT", "RIGHT"], body: str
    ) -> None:
        info = await self.repo()
        await self.gh.run(
            "api",
            f"repos/{info.owner}/{info.name}/pulls/{pr.number}/comments",
            "-f",
            f"body={body}",
            "-f",
            f"commit_id={pr.head_sha}",
            "-f",
            f"path={path}",
            "-F",
            f"line={line}",
            "-f",
            f"side={side}",
            read_only=False,
        )

    async def reply(self, pr: PullRequest, comment_id: int, body: str) -> None:
        info = await self.repo()
        await self.gh.run(
            "api",
            f"repos/{info.owner}/{info.name}/pulls/{pr.number}/comments/{comment_id}/replies",
            "-f",
            f"body={body}",
            read_only=False,
        )

    async def set_resolved(self, thread_id: str, resolved: bool) -> None:
        await self.gh.run(
            "api",
            "graphql",
            "-f",
            f"query={RESOLVE if resolved else UNRESOLVE}",
            "-F",
            f"id={thread_id}",
            read_only=False,
        )
