"""Detect in-progress git operations (rebase, merge, cherry-pick, revert, bisect, am)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from .runner import Git


class OpKind(StrEnum):
    REBASE = "rebase"
    MERGE = "merge"
    CHERRY_PICK = "cherry-pick"
    REVERT = "revert"
    BISECT = "bisect"
    AM = "am"


@dataclass(frozen=True)
class OperationState:
    kind: OpKind
    step: int | None = None
    total: int | None = None
    current: str | None = None
    onto: str | None = None
    conflicts: int = 0
    head_name: str | None = None
    remaining: int | None = None  # cherry-pick/revert sequences

    @property
    def label(self) -> str:
        text = {
            OpKind.REBASE: "Rebase",
            OpKind.MERGE: "Merge",
            OpKind.CHERRY_PICK: "Cherry-pick",
            OpKind.REVERT: "Revert",
            OpKind.BISECT: "Bisect",
            OpKind.AM: "Applying patches",
        }[self.kind]
        if self.step is not None and self.total:
            text += f" ({self.step}/{self.total})"
        elif self.remaining and self.remaining > 1:
            text += f" ({self.remaining} left)"
        return text + " in progress"


def _read(p: Path) -> str | None:
    try:
        return p.read_text().strip()
    except OSError:
        return None


def _sequencer_remaining(git_dir: Path) -> int | None:
    """Commits left in a cherry-pick/revert sequence, including the current one."""
    todo = _read(git_dir / "sequencer" / "todo")
    if todo is None:
        return None
    return sum(1 for ln in todo.splitlines() if ln.strip() and not ln.startswith("#"))


def _progress(git_dir: Path, remaining: int | None) -> tuple[int | None, int | None]:
    """git doesn't record the sequence length; gitpanel stores it when it starts one."""
    total_s = _read(git_dir / "gitpanel" / "sequence-total")
    if remaining is None or not total_s or not total_s.isdigit():
        return None, None
    total = int(total_s)
    return max(total - remaining + 1, 1), total


async def detect(git: Git) -> OperationState | None:
    git_dir = Path(await git.rev_parse("--path-format=absolute", "--git-dir"))
    conflicts = await _count_conflicts(git)

    for name in ("rebase-merge", "rebase-apply"):
        d = git_dir / name
        if d.is_dir():
            if name == "rebase-apply" and (d / "applying").exists():
                return OperationState(OpKind.AM, conflicts=conflicts)
            step = _read(d / "msgnum") or _read(d / "next")
            total = _read(d / "end") or _read(d / "last")
            current = _read(d / "stopped-sha") or _read(d / "original-commit")
            head_name = _read(d / "head-name")
            return OperationState(
                OpKind.REBASE,
                step=int(step) if step and step.isdigit() else None,
                total=int(total) if total and total.isdigit() else None,
                current=current,
                onto=_read(d / "onto"),
                conflicts=conflicts,
                head_name=head_name.removeprefix("refs/heads/") if head_name else None,
            )
    if (git_dir / "MERGE_HEAD").exists():
        merge_head = (_read(git_dir / "MERGE_HEAD") or "").split()
        return OperationState(
            OpKind.MERGE, current=merge_head[0] if merge_head else None, conflicts=conflicts
        )
    for fname, kind in (("CHERRY_PICK_HEAD", OpKind.CHERRY_PICK), ("REVERT_HEAD", OpKind.REVERT)):
        if (git_dir / fname).exists():
            remaining = _sequencer_remaining(git_dir)
            step, total = _progress(git_dir, remaining)
            return OperationState(
                kind,
                step=step,
                total=total,
                current=_read(git_dir / fname),
                conflicts=conflicts,
                remaining=remaining,
            )
    # sequencer stopped without *_HEAD (e.g. after a resolved commit, before --continue)
    if (git_dir / "sequencer" / "todo").exists():
        todo = _read(git_dir / "sequencer" / "todo") or ""
        kind = OpKind.REVERT if todo.lstrip().startswith("revert") else OpKind.CHERRY_PICK
        remaining = _sequencer_remaining(git_dir)
        step, total = _progress(git_dir, remaining)
        return OperationState(
            kind, step=step, total=total, conflicts=conflicts, remaining=remaining
        )
    if (git_dir / "BISECT_LOG").exists():
        # BISECT_LOG lives in the per-worktree git dir
        expected = _read(git_dir / "BISECT_EXPECTED_REV")
        return OperationState(OpKind.BISECT, current=expected, conflicts=conflicts)
    return None


async def _count_conflicts(git: Git) -> int:
    res = await git.run("diff", "--name-only", "--diff-filter=U", "-z", check=False, read_only=True)
    return len([p for p in res.text.split("\0") if p])
