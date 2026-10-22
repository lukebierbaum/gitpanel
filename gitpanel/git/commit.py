"""Staging whole files, committing, amending, rollback, message history."""

from __future__ import annotations

import contextlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from .runner import Git, GitResult


async def stage_paths(git: Git, paths: Sequence[str]) -> None:
    if paths:
        await git.run("add", "-A", "--", *paths)


async def unstage_paths(git: Git, paths: Sequence[str]) -> None:
    if not paths:
        return
    if await git.try_rev_parse("HEAD"):
        await git.run("restore", "--staged", "--", *paths)
    else:  # unborn branch: nothing to restore from
        await git.run("rm", "--cached", "-r", "-q", "--ignore-unmatch", "--", *paths)


async def stage_all(git: Git) -> None:
    await git.run("add", "-A")


async def unstage_all(git: Git) -> None:
    if await git.try_rev_parse("HEAD"):
        await git.run("reset", "-q")
    else:
        await git.run("rm", "--cached", "-r", "-q", "--ignore-unmatch", ".")


@dataclass(frozen=True)
class CommitOptions:
    amend: bool = False
    signoff: bool = False
    author: str | None = None  # "Name <email>"
    no_verify: bool = False
    allow_empty: bool = False


async def commit(git: Git, message: str, opts: CommitOptions | None = None) -> GitResult:
    opts = opts or CommitOptions()
    if not message.strip() and not opts.amend:
        raise ValueError("Commit message is empty")
    args = ["commit", "--cleanup=strip", "-F", "-"]
    if opts.amend:
        args.append("--amend")
    if opts.signoff:
        args.append("--signoff")
    if opts.author:
        args.append(f"--author={opts.author}")
    if opts.no_verify:
        args.append("--no-verify")
    if opts.allow_empty:
        args.append("--allow-empty")
    return await git.run(*args, input=message.encode())


async def last_message(git: Git) -> str:
    if not await git.try_rev_parse("HEAD"):
        return ""
    res = await git.run("log", "-1", "--format=%B", "HEAD", read_only=True)
    return res.text.rstrip("\n")


async def head_parent_count(git: Git) -> int:
    res = await git.run("rev-list", "--parents", "-n", "1", "HEAD", check=False, read_only=True)
    return max(len(res.text.split()) - 1, 0)


async def paths_in_head(git: Git, paths: Sequence[str]) -> set[str]:
    if not paths or not await git.try_rev_parse("HEAD"):
        return set()
    res = await git.run("ls-tree", "-r", "-z", "--name-only", "HEAD", "--", *paths, read_only=True)
    return {p for p in res.text.split("\0") if p}


async def rollback(git: Git, paths: Sequence[str], *, delete_added: bool = False) -> None:
    """Revert `paths` to HEAD in index and working tree (PyCharm "Rollback").

    Files absent from HEAD (added or untracked) are unstaged; their local
    copies are deleted only when `delete_added` is set.
    """
    if not paths:
        return
    in_head = await paths_in_head(git, paths)
    tracked = [p for p in paths if p in in_head]
    new = [p for p in paths if p not in in_head]
    if tracked:
        await git.run("restore", "--source=HEAD", "--staged", "--worktree", "--", *tracked)
    if new:
        await git.run("rm", "--cached", "-q", "--ignore-unmatch", "-r", "--", *new)
        if delete_added:
            for p in new:
                with contextlib.suppress(FileNotFoundError, IsADirectoryError):
                    (git.cwd / p).unlink()


def add_to_gitignore(root: Path, path: str) -> None:
    gi = root / ".gitignore"
    entry = "/" + path.lstrip("/")
    existing = gi.read_text() if gi.exists() else ""
    if entry in existing.splitlines():
        return
    if existing and not existing.endswith("\n"):
        existing += "\n"
    gi.write_text(existing + entry + "\n")


class MessageHistory:
    """Recent commit messages, newest first (PyCharm's commit message history)."""

    def __init__(self, path: Path, limit: int = 20) -> None:
        self.path = path
        self.limit = limit

    def load(self) -> list[str]:
        try:
            data = json.loads(self.path.read_text())
        except (OSError, ValueError):
            return []
        return [m for m in data if isinstance(m, str)]

    def add(self, message: str) -> None:
        message = message.strip()
        if not message:
            return
        msgs = [m for m in self.load() if m != message]
        msgs.insert(0, message)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(msgs[: self.limit], indent=1))
