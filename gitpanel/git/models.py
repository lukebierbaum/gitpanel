"""Shared data models for the git backend."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


class ChangeType(StrEnum):
    ADDED = "A"
    MODIFIED = "M"
    DELETED = "D"
    RENAMED = "R"
    COPIED = "C"
    TYPE_CHANGED = "T"
    UNMERGED = "U"
    UNTRACKED = "?"
    UNCHANGED = "."


@dataclass(frozen=True)
class FileStatus:
    """One path in `git status`.

    `staged` is the HEAD→index change, `unstaged` the index→worktree change.
    """

    path: str
    staged: ChangeType = ChangeType.UNCHANGED
    unstaged: ChangeType = ChangeType.UNCHANGED
    orig_path: str | None = None  # source path of a rename/copy
    submodule: bool = False
    conflict: str | None = None  # two-letter unmerged code, e.g. "UU", "AA", "DU"

    @property
    def untracked(self) -> bool:
        return self.unstaged is ChangeType.UNTRACKED

    @property
    def unmerged(self) -> bool:
        return self.conflict is not None

    @property
    def has_staged(self) -> bool:
        return self.staged is not ChangeType.UNCHANGED and not self.unmerged

    @property
    def has_unstaged(self) -> bool:
        return self.unstaged is not ChangeType.UNCHANGED

    @property
    def display_type(self) -> ChangeType:
        if self.unmerged:
            return ChangeType.UNMERGED
        if self.untracked:
            return ChangeType.UNTRACKED
        if self.staged is not ChangeType.UNCHANGED:
            if self.staged is ChangeType.ADDED and self.unstaged is ChangeType.DELETED:
                return ChangeType.UNCHANGED
            return self.staged
        return self.unstaged


@dataclass(frozen=True)
class BranchInfo:
    head: str | None  # sha, None for unborn
    branch: str | None  # short name, None when detached
    upstream: str | None = None
    ahead: int = 0
    behind: int = 0

    @property
    def detached(self) -> bool:
        return self.branch is None


@dataclass(frozen=True)
class RepoStatus:
    branch: BranchInfo
    files: tuple[FileStatus, ...] = ()

    def get(self, path: str) -> FileStatus | None:
        for f in self.files:
            if f.path == path:
                return f
        return None

    @property
    def tracked(self) -> tuple[FileStatus, ...]:
        return tuple(f for f in self.files if not f.untracked and not f.unmerged)

    @property
    def untracked(self) -> tuple[FileStatus, ...]:
        return tuple(f for f in self.files if f.untracked)

    @property
    def unmerged(self) -> tuple[FileStatus, ...]:
        return tuple(f for f in self.files if f.unmerged)

    @property
    def is_clean(self) -> bool:
        return not self.files


@dataclass(frozen=True)
class Commit:
    sha: str
    parents: tuple[str, ...] = ()
    subject: str = ""
    author_name: str = ""
    author_email: str = ""
    author_date: datetime | None = None
    committer_name: str = ""
    committer_email: str = ""
    commit_date: datetime | None = None
    body: str = ""

    @property
    def short(self) -> str:
        return self.sha[:8]

    @property
    def is_merge(self) -> bool:
        return len(self.parents) > 1


@dataclass(frozen=True)
class Ref:
    name: str  # full refname, e.g. refs/heads/main
    sha: str
    upstream: str | None = None
    extra: dict[str, str] = field(default_factory=dict, compare=False, hash=False)

    @property
    def short(self) -> str:
        for prefix in ("refs/heads/", "refs/remotes/", "refs/tags/"):
            if self.name.startswith(prefix):
                return self.name[len(prefix) :]
        return self.name

    @property
    def is_local(self) -> bool:
        return self.name.startswith("refs/heads/")

    @property
    def is_remote(self) -> bool:
        return self.name.startswith("refs/remotes/")

    @property
    def is_tag(self) -> bool:
        return self.name.startswith("refs/tags/")


@dataclass(frozen=True)
class StashEntry:
    index: int
    sha: str
    message: str

    @property
    def ref(self) -> str:
        return f"stash@{{{self.index}}}"
