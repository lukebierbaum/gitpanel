"""Commit log queries with PyCharm-style filters."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from .models import Commit
from .runner import Git

US = "\x1f"
# sha, parents, author name, email, author time, committer name, email, commit time, subject
LOG_FORMAT = "%H%x1f%P%x1f%an%x1f%ae%x1f%at%x1f%cn%x1f%ce%x1f%ct%x1f%s"
DETAIL_FORMAT = LOG_FORMAT + "%x1f%B"


@dataclass(frozen=True)
class LogFilter:
    refs: tuple[str, ...] = ()  # empty = all refs (branches, remotes, tags, HEAD)
    author: str | None = None
    text: str | None = None  # regex over the message, case-insensitive
    paths: tuple[str, ...] = ()
    since: str | None = None
    until: str | None = None
    first_parent: bool = False
    no_merges: bool = False

    def args(self) -> list[str]:
        out: list[str] = []
        if self.author:
            out.append(f"--author={self.author}")
        if self.text:
            out += [f"--grep={self.text}", "--regexp-ignore-case", "--extended-regexp"]
        if self.since:
            out.append(f"--since={self.since}")
        if self.until:
            out.append(f"--until={self.until}")
        if self.first_parent:
            out.append("--first-parent")
        if self.no_merges:
            out.append("--no-merges")
        if self.refs:
            out += list(self.refs)
        else:
            out += ["--branches", "--remotes", "--tags", "HEAD"]
        return out

    @property
    def is_default(self) -> bool:
        return self == LogFilter()


def _ts(s: str) -> datetime | None:
    return datetime.fromtimestamp(int(s), UTC) if s else None


def parse_record(rec: str, *, with_body: bool = False) -> Commit:
    parts = rec.split(US)
    sha, parents, an, ae, at, cn, ce, ct, subject = parts[:9]
    body = US.join(parts[9:]).rstrip("\n") if with_body else ""
    return Commit(
        sha=sha.strip(),
        parents=tuple(parents.split()),
        subject=subject,
        author_name=an,
        author_email=ae,
        author_date=_ts(at),
        committer_name=cn,
        committer_email=ce,
        commit_date=_ts(ct),
        body=body,
    )


async def load_page(git: Git, filt: LogFilter, skip: int = 0, limit: int = 500) -> list[Commit]:
    """One page of commits in topological order (newest first)."""
    if not await git.try_rev_parse("HEAD") and not filt.refs:
        has_refs = await git.run("for-each-ref", "--count=1", read_only=True)
        if not has_refs.text.strip():
            return []
    args = [
        "log",
        "-z",
        "--topo-order",
        f"--format={LOG_FORMAT}",
        f"--skip={skip}",
        f"--max-count={limit}",
        *filt.args(),
    ]
    if filt.paths:
        args += ["--", *filt.paths]
    res = await git.run(*args, read_only=True)
    return [parse_record(r) for r in res.text.split("\0") if r.strip()]


async def commit_details(git: Git, sha: str) -> Commit:
    res = await git.run("show", "-s", "-z", f"--format={DETAIL_FORMAT}", sha, read_only=True)
    return parse_record(res.text.rstrip("\0"), with_body=True)


async def containing_branches(git: Git, sha: str, limit: int = 50) -> list[str]:
    res = await git.run(
        "branch",
        "-a",
        "--contains",
        sha,
        "--format=%(refname:short)",
        check=False,
        read_only=True,
    )
    names = [n for n in res.text.splitlines() if n and not n.endswith("/HEAD")]
    return names[:limit]


async def containing_remote_branches(git: Git, sha: str, limit: int = 50) -> list[str]:
    res = await git.run(
        "for-each-ref",
        "--contains",
        sha,
        "--format=%(refname:short)",
        "refs/remotes",
        check=False,
        read_only=True,
    )
    names = [n for n in res.text.splitlines() if n and not n.endswith("/HEAD")]
    return names[:limit]


async def has_remotes(git: Git) -> bool:
    res = await git.run("remote", read_only=True, check=False)
    return bool(res.text.strip())


async def is_ancestor(git: Git, a: str, b: str) -> bool:
    res = await git.run("merge-base", "--is-ancestor", a, b, check=False, read_only=True)
    return res.ok
