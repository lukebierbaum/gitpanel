"""Worktree management (the session tracks which one is active)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .operations import OperationState, detect
from .runner import Git, GitError
from .session import Worktree
from .status import status

DEFAULT_TEMPLATE = "../{repo}.worktrees/{branch}"


def default_path(main_worktree: Path, branch: str, template: str = DEFAULT_TEMPLATE) -> Path:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", branch).strip("-") or "worktree"
    rel = template.format(repo=main_worktree.name, branch=safe)
    return (main_worktree / rel).resolve()


async def add(
    git: Git,
    path: Path,
    *,
    branch: str | None = None,
    new_branch: str | None = None,
    start: str | None = None,
    detach: str | None = None,
) -> None:
    """Create a worktree for an existing branch, a new branch, or a detached commit."""
    args = ["worktree", "add", "--quiet"]
    if new_branch:
        args += ["-b", new_branch, str(path)]
        if start:
            args.append(start)
    elif detach:
        args += ["--detach", str(path), detach]
    elif branch:
        args += [str(path), branch]
    else:
        raise ValueError("branch, new_branch or detach is required")
    await git.run(*args)


async def remove(git: Git, path: Path, *, force: bool = False, locked: bool = False) -> None:
    """Remove a worktree; `force` discards local changes, `locked` also overrides a lock."""
    args = ["worktree", "remove"]
    if force or locked:
        args.append("--force")
    if locked:
        args.append("--force")  # git needs it twice for locked worktrees
    await git.run(*args, str(path))


async def move(git: Git, path: Path, new_path: Path) -> None:
    await git.run("worktree", "move", str(path), str(new_path))


async def lock(git: Git, path: Path, reason: str = "") -> None:
    args = ["worktree", "lock"]
    if reason:
        args += ["--reason", reason]
    await git.run(*args, str(path))


async def unlock(git: Git, path: Path) -> None:
    await git.run("worktree", "unlock", str(path))


async def prune(git: Git) -> str:
    res = await git.run("worktree", "prune", "--verbose")
    return (res.text + res.stderr).strip()


async def repair(git: Git) -> None:
    await git.run("worktree", "repair")


@dataclass(frozen=True)
class WorktreeSummary:
    staged: int = 0
    unstaged: int = 0
    untracked: int = 0
    conflicts: int = 0
    ahead: int = 0
    behind: int = 0
    upstream: str | None = None
    operation: OperationState | None = None
    missing: bool = False
    # git's error when the worktree can't be read, e.g. its `.git` file points at a main
    # repository that has moved (`git worktree repair` fixes the links)
    broken: str | None = None

    @property
    def dirty(self) -> bool:
        return bool(self.staged or self.unstaged or self.untracked or self.conflicts)


async def summary(wt: Worktree) -> WorktreeSummary:
    if wt.bare:
        return WorktreeSummary()
    if not wt.path.exists():
        return WorktreeSummary(missing=True)
    git = Git(wt.path)
    try:
        st = await status(git)
        operation = await detect(git)
    except GitError as e:
        return WorktreeSummary(broken=str(e))
    return WorktreeSummary(
        staged=sum(1 for f in st.files if f.has_staged),
        unstaged=sum(1 for f in st.files if f.has_unstaged and not f.untracked and not f.unmerged),
        untracked=len(st.untracked),
        conflicts=len(st.unmerged),
        ahead=st.branch.ahead,
        behind=st.branch.behind,
        upstream=st.branch.upstream,
        operation=operation,
    )


def for_branch(worktrees: list[Worktree], branch: str) -> Worktree | None:
    full = branch if branch.startswith("refs/") else f"refs/heads/{branch}"
    return next((w for w in worktrees if w.branch == full), None)
