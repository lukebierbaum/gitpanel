"""Operation journal: undo/redo for everything gitpanel does to a repository.

See docs/architecture.md §5.
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
from collections.abc import AsyncIterator, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from .runner import Git, GitError

if TYPE_CHECKING:
    from .session import RepoSession

ResetMode = Literal["soft", "mixed", "keep", "hard"]
BACKUP_PREFIX = "refs/gitpanel/backup/"


@dataclass(frozen=True)
class HeadState:
    ref: str | None
    sha: str | None


@dataclass
class JournalEntry:
    id: int
    op: str
    time: str
    head_before: HeadState
    head_after: HeadState
    refs_before: dict[str, str | None]
    refs_after: dict[str, str | None]
    backup: str | None = None  # snapshot before a destructive op
    backup_after: str | None = None  # snapshot after it (for redo)
    paths: list[str] | None = None
    reset_mode: ResetMode = "keep"
    undone: bool = False
    failed: bool = False
    meta: dict[str, str] = field(default_factory=dict)

    @property
    def when(self) -> datetime:
        return datetime.fromisoformat(self.time)

    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True)

    @classmethod
    def from_json(cls, line: str) -> JournalEntry:
        d = json.loads(line)
        d["head_before"] = HeadState(**d["head_before"])
        d["head_after"] = HeadState(**d["head_after"])
        return cls(**d)


class UndoRefused(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


# --- snapshots ----------------------------------------------------------------


async def head_state(git: Git) -> HeadState:
    ref_res = await git.run("symbolic-ref", "-q", "HEAD", check=False, read_only=True)
    ref = ref_res.text.strip() or None
    sha = await git.try_rev_parse("HEAD")
    return HeadState(ref, sha)


async def all_refs(git: Git) -> dict[str, str]:
    res = await git.run("for-each-ref", "--format=%(refname)%00%(objectname)", read_only=True)
    refs: dict[str, str] = {}
    for line in res.text.splitlines():
        name, _, sha = line.partition("\0")
        if not name.startswith("refs/gitpanel/"):
            refs[name] = sha
    return refs


async def snapshot_worktree(git: Git, message: str) -> str:
    """Commit the full working tree (tracked + untracked, not ignored) without touching the index.

    The real index tree is recorded as an `Index-Tree:` trailer.
    """
    wt = await git.run("write-tree", check=False)  # fails while conflicts are unresolved
    index_tree = wt.text.strip() if wt.ok else "none"
    git_index = Path(await git.rev_parse("--path-format=absolute", "--git-path", "index"))
    fd, tmp = tempfile.mkstemp(prefix="gitpanel-index-")
    os.close(fd)
    try:
        if git_index.exists():
            Path(tmp).write_bytes(git_index.read_bytes())
        else:
            os.unlink(tmp)
        env = {"GIT_INDEX_FILE": tmp}
        await git.run("add", "-A", "--", ".", env=env)
        tree = (await git.run("write-tree", env=env)).text.strip()
    finally:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
    parents: list[str] = []
    head = await git.try_rev_parse("HEAD")
    if head:
        parents = ["-p", head]
    msg = f"gitpanel snapshot: {message}\n\nIndex-Tree: {index_tree}\n"
    res = await git.run("commit-tree", tree, *parents, input=msg.encode())
    return res.text.strip()


async def _index_tree_of(git: Git, snapshot: str) -> str:
    body = (await git.run("log", "-1", "--format=%B", snapshot, read_only=True)).text
    for line in body.splitlines():
        if line.startswith("Index-Tree: "):
            return line.split(": ", 1)[1].strip()
    raise ValueError(f"{snapshot} is not a gitpanel snapshot")


async def restore_snapshot(git: Git, snapshot: str, paths: Sequence[str] | None) -> None:
    """Make working tree and index match `snapshot` (limited to `paths` if given)."""
    current = await snapshot_worktree(git, "pre-restore")
    spec = list(paths) if paths else ["."]
    res = await git.run(
        "diff",
        "--name-status",
        "-z",
        "--no-renames",
        current,
        snapshot,
        "--",
        *spec,
        read_only=True,
    )
    fields = res.text.split("\0")
    to_delete: list[str] = []
    to_write: list[str] = []
    for status, path in zip(fields[0::2], fields[1::2], strict=False):
        if status == "D":
            to_delete.append(path)
        elif status:
            to_write.append(path)
    root = git.cwd
    for p in to_delete:
        with contextlib.suppress(FileNotFoundError):
            (root / p).unlink()
    if to_write:
        await git.run("restore", f"--source={snapshot}", "--worktree", "--", *to_write)
    index_tree = await _index_tree_of(git, snapshot)
    if index_tree == "none":
        return
    # reset with a tree-ish also drops index entries the tree doesn't have
    await git.run("reset", "-q", index_tree, "--", *spec)


# --- journal ------------------------------------------------------------------


class Journal:
    def __init__(self, session: RepoSession) -> None:
        self.session = session
        self._depth = 0
        self._current: JournalEntry | None = None

    @property
    def git(self) -> Git:
        return self.session.git

    @property
    def path(self) -> Path:
        return self.session.private_dir() / "journal.jsonl"

    # -- storage ------------------------------------------------------------
    def entries(self, limit: int | None = None) -> list[JournalEntry]:
        try:
            lines = self.path.read_text().splitlines()
        except FileNotFoundError:
            return []
        out = [JournalEntry.from_json(ln) for ln in lines if ln.strip()]
        return out[-limit:] if limit else out

    def _write(self, entries: list[JournalEntry]) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text("".join(e.to_json() + "\n" for e in entries))
        tmp.replace(self.path)

    def _append(self, entry: JournalEntry) -> None:
        entries = [e for e in self.entries() if not e.undone]  # new action drops redo history
        entries.append(entry)
        self._write(entries)

    def _next_id(self) -> int:
        entries = self.entries()
        return (entries[-1].id + 1) if entries else 1

    # -- recording ------------------------------------------------------------
    @contextlib.asynccontextmanager
    async def mutation(
        self,
        op: str,
        *,
        destructive: bool = False,
        paths: Sequence[str] | None = None,
        reset_mode: ResetMode = "keep",
    ) -> AsyncIterator[JournalEntry]:
        if self._depth:
            assert self._current is not None
            self._depth += 1
            try:
                yield self._current
            finally:
                self._depth -= 1
            return

        from .session import ChangeKind

        async with self.session.lock:
            git = self.git
            head_before = await head_state(git)
            refs_before = await all_refs(git)
            entry = JournalEntry(
                id=self._next_id(),
                op=op,
                time=datetime.now(UTC).isoformat(timespec="seconds"),
                head_before=head_before,
                head_after=head_before,
                refs_before={},
                refs_after={},
                paths=list(paths) if paths is not None else None,
                reset_mode=reset_mode,
            )
            if destructive:
                entry.backup = await snapshot_worktree(git, op)
            self._current = entry
            self._depth = 1
            failed = False
            try:
                yield entry
            except BaseException:
                failed = True
                raise
            finally:
                self._depth = 0
                self._current = None
                head_after = await head_state(git)
                refs_after = await all_refs(git)
                changed = {
                    r
                    for r in refs_before.keys() | refs_after.keys()
                    if refs_before.get(r) != refs_after.get(r)
                }
                entry.head_after = head_after
                entry.refs_before = {r: refs_before.get(r) for r in sorted(changed)}
                entry.refs_after = {r: refs_after.get(r) for r in sorted(changed)}
                moved = bool(changed) or head_after != head_before
                if entry.backup and not failed:
                    entry.backup_after = await snapshot_worktree(git, f"after {op}")
                if failed:
                    entry.failed = True
                    entry.op = f"{op} (failed)"
                if moved or (entry.backup and not failed):
                    if entry.backup:
                        await git.run("update-ref", f"{BACKUP_PREFIX}{entry.id}", entry.backup)
                    if entry.backup_after:
                        await git.run(
                            "update-ref", f"{BACKUP_PREFIX}{entry.id}-after", entry.backup_after
                        )
                    self._append(entry)
                kinds = ChangeKind.INDEX | ChangeKind.WORKTREE | ChangeKind.OPERATION
                if changed:
                    kinds |= ChangeKind.REFS
                    if any(r.startswith("refs/stash") for r in changed):
                        kinds |= ChangeKind.STASH
                if head_after != head_before:
                    kinds |= ChangeKind.HEAD
                self.session.emit(kinds)

    # -- undo / redo ----------------------------------------------------------
    def can_undo(self) -> JournalEntry | None:
        live = [e for e in self.entries() if not e.undone]
        return live[-1] if live else None

    def can_redo(self) -> JournalEntry | None:
        undone = [e for e in self.entries() if e.undone]
        return undone[0] if undone else None

    async def undo(self) -> JournalEntry:
        entry = self.can_undo()
        if entry is None:
            raise UndoRefused("Nothing to undo")
        async with self.session.lock:
            await self._restore(
                entry,
                head_from=entry.head_after,
                head_to=entry.head_before,
                refs_from=entry.refs_after,
                refs_to=entry.refs_before,
                snapshot=entry.backup,
                undoing=True,
            )
            entries = self.entries()
            for e in entries:
                if e.id == entry.id:
                    e.undone = True
            self._write(entries)
        self._emit_all()
        return entry

    async def redo(self) -> JournalEntry:
        entry = self.can_redo()
        if entry is None:
            raise UndoRefused("Nothing to redo")
        async with self.session.lock:
            await self._restore(
                entry,
                head_from=entry.head_before,
                head_to=entry.head_after,
                refs_from=entry.refs_before,
                refs_to=entry.refs_after,
                snapshot=entry.backup_after,
                undoing=False,
            )
            entries = self.entries()
            for e in entries:
                if e.id == entry.id:
                    e.undone = False
            self._write(entries)
        self._emit_all()
        return entry

    def _emit_all(self) -> None:
        from .session import ChangeKind

        self.session.emit(ChangeKind.ALL)

    async def _restore(
        self,
        entry: JournalEntry,
        *,
        head_from: HeadState,
        head_to: HeadState,
        refs_from: dict[str, str | None],
        refs_to: dict[str, str | None],
        snapshot: str | None,
        undoing: bool,
    ) -> None:
        git = self.git
        verb = "undo" if undoing else "redo"
        from .operations import detect

        op = await detect(git)
        if op is not None:
            raise UndoRefused(f"Cannot {verb} while a {op.kind} is in progress")

        now_refs = await all_refs(git)
        for ref, expected in refs_from.items():
            if ref.startswith("refs/remotes/"):
                continue  # remote-tracking refs are not restored
            if now_refs.get(ref) != expected:
                short = ref.removeprefix("refs/heads/")
                raise UndoRefused(f"Cannot {verb} '{entry.op}': {short} has changed since")
        now_head = await head_state(git)
        if now_head != head_from:
            raise UndoRefused(f"Cannot {verb} '{entry.op}': HEAD has moved since")

        # branches checked out in other worktrees must not be moved underneath them
        others = {
            wt.branch
            for wt in await self.session.worktrees()
            if wt.branch and wt.path.resolve() != self.session.active.path.resolve()
        }
        for ref in refs_to:
            if ref in others:
                branch = ref.removeprefix("refs/heads/")
                raise UndoRefused(f"Cannot {verb}: {branch} is checked out in another worktree")

        if undoing:
            await self._refuse_if_pushed(entry, now_refs)

        # 1. switch HEAD to the other branch if the op changed which branch is checked out
        if head_to.ref != head_from.ref:
            target = head_to.ref
            if target and (refs_to.get(target) or now_refs.get(target)):
                branch = target.removeprefix("refs/heads/")
                if (
                    target in refs_to
                    and refs_to[target] is not None
                    and now_refs.get(target) is None
                ):
                    await git.run("branch", branch, refs_to[target] or "")
                await git.run("checkout", "-q", branch)
            elif head_to.sha:
                await git.run("checkout", "-q", "--detach", head_to.sha)

        # 2. move refs
        current = (await head_state(git)).ref
        for ref, sha in refs_to.items():
            if ref.startswith("refs/remotes/"):
                continue
            if ref == current:
                if sha is not None:
                    await git.run("reset", f"--{entry.reset_mode}", "-q", sha)
                continue
            if sha is None:
                await git.run("update-ref", "-d", ref)
            else:
                await git.run("update-ref", ref, sha)

        # 3. detached HEAD that moved
        if head_to.ref is None and head_to.sha:
            now = await head_state(git)
            if now.sha != head_to.sha:
                await git.run("reset", f"--{entry.reset_mode}", "-q", head_to.sha)

        # 4. working tree / index contents
        if snapshot:
            await restore_snapshot(git, snapshot, entry.paths)

    async def _refuse_if_pushed(self, entry: JournalEntry, now_refs: dict[str, str]) -> None:
        git = self.git
        for ref, after in entry.refs_after.items():
            before = entry.refs_before.get(ref)
            if not ref.startswith("refs/heads/") or after is None or after == before:
                continue
            branch = ref.removeprefix("refs/heads/")
            up = await git.run(
                "rev-parse",
                "--symbolic-full-name",
                f"{branch}@{{upstream}}",
                check=False,
                read_only=True,
            )
            upstream = up.text.strip()
            if not up.ok or not upstream or upstream not in now_refs:
                continue
            if upstream in entry.refs_after:
                continue  # this op fetched those commits from the remote itself
            try:
                await git.run("merge-base", "--is-ancestor", after, upstream, read_only=True)
            except GitError:
                continue
            if before is not None:
                res = await git.run(
                    "merge-base", "--is-ancestor", after, before, check=False, read_only=True
                )
                if res.ok:
                    continue  # undo moves forward, nothing published is dropped
            raise UndoRefused(
                f"Cannot undo '{entry.op}': its commits are already pushed to "
                f"{upstream.removeprefix('refs/remotes/')}. Revert them instead."
            )

    async def prune(self, keep: int = 200, max_age: timedelta = timedelta(days=30)) -> None:
        entries = self.entries()
        cutoff = datetime.now(UTC) - max_age
        kept = [e for e in entries[-keep:] if e.when >= cutoff]
        dropped = {e.id for e in entries} - {e.id for e in kept}
        for eid in dropped:
            for suffix in ("", "-after"):
                await self.git.run("update-ref", "-d", f"{BACKUP_PREFIX}{eid}{suffix}", check=False)
        self._write(kept)
