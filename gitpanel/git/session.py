"""Repository session: active worktree, change events, external-change watcher."""

from __future__ import annotations

import asyncio
import contextlib
import os
from collections.abc import Callable
from dataclasses import dataclass
from enum import Flag, auto
from pathlib import Path
from typing import TYPE_CHECKING

from .runner import Git, GitError

if TYPE_CHECKING:
    from .journal import Journal


class ChangeKind(Flag):
    NONE = 0
    INDEX = auto()
    WORKTREE = auto()
    HEAD = auto()
    REFS = auto()
    STASH = auto()
    CONFIG = auto()
    OPERATION = auto()
    WORKTREES = auto()
    ALL = INDEX | WORKTREE | HEAD | REFS | STASH | CONFIG | OPERATION | WORKTREES


class WorktreeGone(Exception):
    """The active worktree can't be used any more (its folder or its repository was moved
    or deleted) and no other worktree of the repository is left to switch to."""


@dataclass(frozen=True)
class Worktree:
    path: Path
    head: str | None
    branch: str | None  # full ref, e.g. refs/heads/main
    is_main: bool
    locked: str | None = None
    prunable: str | None = None
    bare: bool = False

    @property
    def branch_short(self) -> str | None:
        if self.branch and self.branch.startswith("refs/heads/"):
            return self.branch[len("refs/heads/") :]
        return self.branch

    @property
    def name(self) -> str:
        return self.path.name


def parse_worktree_list(raw: bytes) -> list[Worktree]:
    """Parse `git worktree list --porcelain -z`."""
    out: list[Worktree] = []
    cur: dict[str, str | None] = {}
    first = True

    def flush() -> None:
        nonlocal first, cur
        if not cur:
            return
        out.append(
            Worktree(
                path=Path(cur["worktree"] or ""),
                head=cur.get("HEAD"),
                branch=cur.get("branch"),
                is_main=first,
                locked=cur.get("locked"),
                prunable=cur.get("prunable"),
                bare="bare" in cur,
            )
        )
        first = False
        cur = {}

    for field in raw.decode("utf-8", "surrogateescape").split("\0"):
        if not field:
            flush()
            continue
        key, _, value = field.partition(" ")
        if key in ("locked", "prunable"):
            cur[key] = value
        elif key in ("bare", "detached"):
            cur[key] = ""
        else:
            cur[key] = value
    flush()
    return out


class RepoSession:
    """One open repository. Holds the active worktree and broadcasts changes."""

    def __init__(self, common_dir: Path, active: Worktree, git_dir: Path) -> None:
        self.common_dir = common_dir
        self.active = active
        self.git_dir = git_dir
        self.git = Git(active.path)
        self.lock = asyncio.Lock()
        self._subscribers: list[Callable[[ChangeKind], None]] = []
        self._journal: Journal | None = None
        self._watch_task: asyncio.Task[None] | None = None
        self._mtimes: dict[Path, float] = {}

    # -- construction -------------------------------------------------------
    @classmethod
    async def open(cls, path: Path | str) -> RepoSession:
        path = Path(path).resolve()
        probe = Git(path)
        toplevel = Path(await probe.rev_parse("--show-toplevel"))
        common = Path(await probe.rev_parse("--path-format=absolute", "--git-common-dir"))
        git_dir = Path(await probe.rev_parse("--path-format=absolute", "--git-dir"))
        session = cls(common, Worktree(toplevel, None, None, True), git_dir)
        session.active = await session._find_worktree(toplevel)
        session._snapshot_mtimes()
        return session

    async def _find_worktree(self, toplevel: Path) -> Worktree:
        for wt in await self.worktrees():
            if wt.path.resolve() == toplevel.resolve():
                return wt
        return Worktree(toplevel, None, None, True)

    # -- worktrees ----------------------------------------------------------
    async def worktrees(self) -> list[Worktree]:
        res = await self.git.run("worktree", "list", "--porcelain", "-z", read_only=True)
        return parse_worktree_list(res.stdout)

    async def refresh_active(self) -> Worktree:
        """Re-read the active worktree. If it's unusable (its folder was moved or deleted,
        or git can no longer read it), switch to another worktree of the repository that
        still exists — main first — like `switch()` (so ChangeKind.ALL is emitted); the
        caller sees `active.path` change. Raises WorktreeGone when there is none."""
        try:
            self.active = await self._find_worktree(self.active.path)
            return self.active
        except GitError as e:
            reason = str(e)
        gone = self.active.path
        if not self.common_dir.is_dir():
            raise WorktreeGone(
                f"{gone}: its repository {self.common_dir} no longer exists — moved or "
                f"deleted? ({reason}). If you moved it, open it from its new location; "
                "linked worktrees are re-attached with `git worktree repair` there."
            )
        try:
            res = await Git(self.common_dir).run(
                "worktree", "list", "--porcelain", "-z", read_only=True
            )
            candidates = parse_worktree_list(res.stdout)
        except GitError:
            candidates = []
        candidates.sort(key=lambda w: not w.is_main)
        for wt in candidates:
            if wt.bare or wt.path == gone or not wt.path.is_dir():
                continue
            try:
                await self.switch(wt.path)
            except (GitError, ValueError):
                continue
            return self.active
        raise WorktreeGone(f"{gone}: {reason}, and no other worktree of the repository is left")

    async def switch(self, path: Path | str) -> None:
        path = Path(path).resolve()
        probe = Git(path)
        toplevel = Path(await probe.rev_parse("--show-toplevel"))
        common = Path(await probe.rev_parse("--path-format=absolute", "--git-common-dir"))
        if common.resolve() != self.common_dir.resolve():
            raise ValueError(f"{path} belongs to a different repository")
        self.git_dir = Path(await probe.rev_parse("--path-format=absolute", "--git-dir"))
        self.git = Git(toplevel)
        self._journal = None
        self.active = await self._find_worktree(toplevel)
        self._snapshot_mtimes()
        self.emit(ChangeKind.ALL)

    # -- private storage ----------------------------------------------------
    def private_dir(self, *, shared: bool = False) -> Path:
        base = self.common_dir if shared else self.git_dir
        d = base / "gitpanel"
        if base.is_dir():  # never re-create a git dir that was moved or deleted
            d.mkdir(exist_ok=True)
        return d

    @property
    def journal(self) -> Journal:
        if self._journal is None:
            from .journal import Journal

            self._journal = Journal(self)
        return self._journal

    # -- events -------------------------------------------------------------
    def subscribe(self, cb: Callable[[ChangeKind], None]) -> Callable[[], None]:
        self._subscribers.append(cb)

        def unsubscribe() -> None:
            with contextlib.suppress(ValueError):
                self._subscribers.remove(cb)

        return unsubscribe

    def emit(self, kinds: ChangeKind) -> None:
        if not kinds:
            return
        self._snapshot_mtimes()
        for cb in list(self._subscribers):
            cb(kinds)

    # -- external change watcher -------------------------------------------
    def _watched(self) -> dict[Path, ChangeKind]:
        g, c = self.git_dir, self.common_dir
        return {
            g / "index": ChangeKind.INDEX | ChangeKind.WORKTREE,
            g / "HEAD": ChangeKind.HEAD | ChangeKind.REFS,
            g / "logs" / "HEAD": ChangeKind.HEAD,
            c / "packed-refs": ChangeKind.REFS,
            c / "refs" / "heads": ChangeKind.REFS,
            c / "refs" / "remotes": ChangeKind.REFS,
            c / "refs" / "tags": ChangeKind.REFS,
            c / "refs" / "stash": ChangeKind.STASH,
            c / "config": ChangeKind.CONFIG,
            c / "worktrees": ChangeKind.WORKTREES,
            g / "MERGE_HEAD": ChangeKind.OPERATION,
            g / "CHERRY_PICK_HEAD": ChangeKind.OPERATION,
            g / "REVERT_HEAD": ChangeKind.OPERATION,
            g / "BISECT_LOG": ChangeKind.OPERATION,
            g / "rebase-merge": ChangeKind.OPERATION,
            g / "rebase-apply": ChangeKind.OPERATION,
        }

    @staticmethod
    def _mtime(p: Path) -> float:
        try:
            st = p.stat()
        except OSError:
            return -1.0
        if p.is_dir():
            latest = st.st_mtime
            for root, dirs, _files in os.walk(p):
                for d in dirs:
                    with contextlib.suppress(OSError):
                        latest = max(latest, os.stat(os.path.join(root, d)).st_mtime)
            return latest
        return st.st_mtime

    def _snapshot_mtimes(self) -> None:
        self._mtimes = {p: self._mtime(p) for p in self._watched()}

    def poll_changes(self) -> ChangeKind:
        """Compare watched files with the last snapshot; return what changed."""
        changed = ChangeKind.NONE
        for p, kind in self._watched().items():
            m = self._mtime(p)
            if self._mtimes.get(p) != m:
                changed |= kind
                self._mtimes[p] = m
        return changed

    def start_watching(self, interval: float = 1.0) -> None:
        if self._watch_task is not None:
            return

        async def loop() -> None:
            while True:
                await asyncio.sleep(interval)
                if self.lock.locked():
                    continue
                changed = self.poll_changes()
                if changed:
                    for cb in list(self._subscribers):
                        cb(changed)

        self._watch_task = asyncio.create_task(loop())

    def stop_watching(self) -> None:
        if self._watch_task is not None:
            self._watch_task.cancel()
            self._watch_task = None
