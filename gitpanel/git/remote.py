"""Remotes: fetch, pull, push."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from .log import LogFilter, load_page
from .models import Commit
from .runner import Git, GitError, GitResult

NO_PROMPT = {"GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "", "SSH_ASKPASS": ""}
AUTH_MARKERS = (
    "terminal prompts disabled",
    "could not read username",
    "could not read password",
    "authentication failed",
    "permission denied (publickey",
    "host key verification failed",
)


def is_auth_error(err: GitError) -> bool:
    text = err.result.stderr.lower()
    return any(m in text for m in AUTH_MARKERS)


@dataclass(frozen=True)
class Remote:
    name: str
    fetch_url: str
    push_url: str


async def remotes(git: Git) -> list[Remote]:
    res = await git.run("remote", "-v", read_only=True)
    urls: dict[str, dict[str, str]] = {}
    for line in res.text.splitlines():
        parts = line.split()
        if len(parts) >= 3:
            urls.setdefault(parts[0], {})[parts[2].strip("()")] = parts[1]
    return [
        Remote(n, u.get("fetch", ""), u.get("push", u.get("fetch", ""))) for n, u in urls.items()
    ]


async def add_remote(git: Git, name: str, url: str) -> None:
    await git.run("remote", "add", name, url)


async def remove_remote(git: Git, name: str) -> None:
    await git.run("remote", "remove", name)


async def rename_remote(git: Git, old: str, new: str) -> None:
    await git.run("remote", "rename", old, new)


async def set_url(git: Git, name: str, url: str) -> None:
    await git.run("remote", "set-url", name, url)


async def fetch(git: Git, remote: str | None = None, *, prune: bool = True) -> GitResult:
    args = ["fetch", "--quiet"]
    if prune:
        args.append("--prune")
    args += [remote] if remote else ["--all"]
    return await git.run(*args, env=NO_PROMPT)


PullMode = Literal["merge", "rebase", "ff-only"]


async def pull(git: Git, mode: PullMode = "merge", *, autostash: bool = True) -> GitResult:
    args = ["pull", "--quiet", "--no-edit"]
    args.append({"merge": "--no-rebase", "rebase": "--rebase", "ff-only": "--ff-only"}[mode])
    if autostash:
        args.append("--autostash")
    return await git.run(*args, env=NO_PROMPT, check=False)


async def outgoing(git: Git, branch: str, upstream: str | None) -> list[Commit]:
    """Commits on `branch` not yet on its upstream (all commits if it has none)."""
    refs = (f"{upstream}..{branch}",) if upstream else (branch, "--not", "--remotes")
    return await load_page(git, LogFilter(refs=refs), 0, 1000)


async def push(
    git: Git,
    branch: str,
    remote: str,
    *,
    remote_branch: str | None = None,
    set_upstream: bool = False,
    force_with_lease: bool = False,
    tags: bool = False,
) -> GitResult:
    args = ["push", "--porcelain"]
    if set_upstream:
        args.append("--set-upstream")
    if force_with_lease:
        args.append("--force-with-lease")
    if tags:
        args.append("--follow-tags")
    args += [remote, f"refs/heads/{branch}:refs/heads/{remote_branch or branch}"]
    return await git.run(*args, env=NO_PROMPT)


async def push_many(
    git: Git,
    remote: str,
    branches: Sequence[str],
    leases: Mapping[str, str],
    *,
    atomic: bool = True,
) -> GitResult:
    """Push several branches at once, each with `--force-with-lease` against the sha in
    `leases` (what we last saw on the remote), so a push someone else made in between makes
    it fail instead of being overwritten. `atomic`: all branches update, or none."""
    args = ["push", "--porcelain"]
    if atomic:
        args.append("--atomic")
    for b in branches:
        args.append(
            f"--force-with-lease={b}:{leases[b]}" if b in leases else f"--force-with-lease={b}"
        )
    args += [remote, *(f"refs/heads/{b}:refs/heads/{b}" for b in branches)]
    return await git.run(*args, env=NO_PROMPT)


async def delete_remote_branch(git: Git, remote: str, branch: str) -> GitResult:
    return await git.run("push", remote, "--delete", branch, env=NO_PROMPT)
