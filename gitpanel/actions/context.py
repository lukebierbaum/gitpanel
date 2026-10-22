"""SelectionContext: what the user is looking at, published by screens."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Literal

from ..git.models import Commit, FileStatus, Ref, StashEntry
from ..git.operations import OperationState
from ..git.patch import DiffSource, FileDiff, Selection
from ..git.session import RepoSession, Worktree


class Scope(StrEnum):
    GLOBAL = "global"
    COMMIT = "commit"
    BRANCH = "branch"
    REF = "ref"
    FILE = "file"
    HUNK = "hunk"
    WORKTREE = "worktree"
    STASH = "stash"
    SHELF = "shelf"
    CONFLICT = "conflict"


FileGroup = Literal["staged", "unstaged", "untracked", "conflict", "commit"]


@dataclass(frozen=True)
class FileRef:
    path: str
    source: DiffSource
    group: FileGroup
    status: FileStatus | None = None


@dataclass(frozen=True)
class PatchSelection:
    """A hunk/line selection over specific diffs (from patch.py)."""

    source: DiffSource
    diffs: tuple[FileDiff, ...]
    selection: Selection

    @property
    def paths(self) -> list[str]:
        return sorted(self.selection.paths())


@dataclass(frozen=True)
class SelectionContext:
    session: RepoSession
    screen: str = ""
    focus: Scope = Scope.GLOBAL
    commits: tuple[Commit, ...] = ()
    refs: tuple[Ref, ...] = ()
    files: tuple[FileRef, ...] = ()
    patch: PatchSelection | None = None
    worktree: Worktree | None = None
    stash: StashEntry | None = None
    operation: OperationState | None = None
    form: Mapping[str, Any] = field(default_factory=dict)  # screen inputs, e.g. commit message

    def placeholders(self) -> dict[str, str]:
        """Values for {placeholders} in confirmations and custom commands."""
        first_ref = self.refs[0].short if self.refs else ""
        branch = next((r.short for r in self.refs if r.is_local), first_ref)
        return {
            "repo": str(self.session.active.path),
            "worktree": str((self.worktree or self.session.active).path),
            "commit": self.commits[0].sha if self.commits else "",
            "commits": " ".join(c.sha for c in self.commits),
            "branch": branch,
            "ref": first_ref,
            "file": self.files[0].path if self.files else "",
            "files": " ".join(f.path for f in self.files),
            "count": str(len(self.files) or len(self.commits)),
            "op": str(self.operation.kind) if self.operation else "",
        }
