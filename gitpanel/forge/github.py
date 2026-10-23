"""GitHub pull requests through the `gh` CLI, plus the local-git side (fetching PR heads)."""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from ..git.remote import NO_PROMPT
from ..git.session import RepoSession
from .models import PullRequest, ReviewThread
from .runner import ForgeError, Gh

LIST_FIELDS = (
    "number,title,author,state,isDraft,headRefName,baseRefName,headRefOid,additions,deletions,"
    "changedFiles,reviewDecision,statusCheckRollup,labels,updatedAt,createdAt,url,"
    "isCrossRepository,headRepositoryOwner"
)
DETAIL_FIELDS = (
    LIST_FIELDS
    + ",body,mergeable,mergeStateStatus,reviewRequests,latestReviews,comments,autoMergeRequest"
)

Query = Literal["mine", "review", "assigned", "open", "closed"]
QUERIES: dict[str, tuple[str, str]] = {
    # name → (label, search qualifier)
    "mine": ("Mine", "author:@me"),
    "review": ("Review requested", "review-requested:@me"),
    "assigned": ("Assigned", "assignee:@me"),
    "open": ("All open", ""),
    "closed": ("Closed", ""),
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

    async def current_pr(self) -> PullRequest | None:
        """The open PR for the checked-out branch, if any."""
        res = await self.gh.run("pr", "view", "--json", LIST_FIELDS, check=False)
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
        """Fetch the PR head (and its base) so commits and diffs come from local git."""
        remote = await self.remote_name()
        ref = f"refs/gitpanel/pr/{pr.number}"
        git = self.session.git
        have = await git.try_rev_parse(ref)
        if not (have and pr.head_sha and have == pr.head_sha):
            await git.run(
                "fetch",
                "--quiet",
                "--no-tags",
                remote,
                f"+refs/pull/{pr.number}/head:{ref}",
                env=NO_PROMPT,
            )
        await git.run("fetch", "--quiet", "--no-tags", remote, pr.base, env=NO_PROMPT, check=False)
        return await git.rev_parse(ref)

    async def merge_base(self, pr: PullRequest) -> str:
        remote = await self.remote_name()
        git = self.session.git
        head = await self.fetch_head(pr)
        base = f"refs/remotes/{remote}/{pr.base}"
        if not await git.try_rev_parse(base):
            base = "FETCH_HEAD"
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
