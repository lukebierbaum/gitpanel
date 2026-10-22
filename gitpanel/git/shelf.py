"""PyCharm-style shelves: named patches kept outside git's stash."""

from __future__ import annotations

import json
import shutil
import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from . import commit as C
from .patch import DIFF_OPTS, parse_diff
from .runner import Git, GitError
from .session import RepoSession
from .status import status


@dataclass(frozen=True)
class Shelf:
    id: str
    name: str
    created: datetime
    base: str | None
    paths: tuple[str, ...]
    dir: Path

    @property
    def patch_file(self) -> Path:
        return self.dir / "changes.patch"

    def patch(self) -> bytes:
        return self.patch_file.read_bytes()


def shelf_root(session: RepoSession) -> Path:
    return session.private_dir(shared=True) / "shelf"


def list_shelves(session: RepoSession) -> list[Shelf]:
    root = shelf_root(session)
    out: list[Shelf] = []
    if not root.exists():
        return out
    for d in root.iterdir():
        meta_file = d / "meta.json"
        if not meta_file.exists():
            continue
        meta = json.loads(meta_file.read_text())
        out.append(
            Shelf(
                id=d.name,
                name=meta["name"],
                created=datetime.fromisoformat(meta["created"]),
                base=meta.get("base"),
                paths=tuple(meta.get("paths", [])),
                dir=d,
            )
        )
    return sorted(out, key=lambda s: s.created, reverse=True)


async def _patch_for(git: Git, paths: Sequence[str]) -> bytes:
    """HEAD → working tree for `paths`, including untracked files."""
    st = await status(git)
    untracked = {f.path for f in st.untracked}
    tracked = [p for p in paths if p not in untracked]
    chunks: list[bytes] = []
    if tracked:
        base = "HEAD" if await git.try_rev_parse("HEAD") else None
        if base:
            res = await git.run("diff", *DIFF_OPTS, base, "--", *tracked, read_only=True)
        else:
            res = await git.run("diff", "--cached", *DIFF_OPTS, "--", *tracked, read_only=True)
        chunks.append(res.stdout)
    for p in paths:
        if p in untracked:
            r = await git.run(
                "diff", "--no-index", *DIFF_OPTS, "--", "/dev/null", p, check=False, read_only=True
            )
            chunks.append(r.stdout)
    return b"".join(chunks)


async def shelve(session: RepoSession, name: str, paths: Sequence[str]) -> Shelf:
    git = session.git
    patch = await _patch_for(git, paths)
    if not patch.strip():
        raise ValueError("No changes to shelve")
    shelf_id = time.strftime("%Y%m%d-%H%M%S") + f"-{int(time.time() * 1000) % 1000:03d}"
    d = shelf_root(session) / shelf_id
    d.mkdir(parents=True)
    (d / "changes.patch").write_bytes(patch)
    touched = sorted({f.path for f in parse_diff(patch)})
    meta = {
        "name": name or "Shelved changes",
        "created": datetime.now(UTC).isoformat(timespec="seconds"),
        "base": await git.try_rev_parse("HEAD"),
        "paths": touched,
    }
    (d / "meta.json").write_text(json.dumps(meta, indent=1))
    # roll the shelved changes back (untracked files are removed)
    st = await status(git)
    untracked = {f.path for f in st.untracked}
    tracked = [p for p in touched if p not in untracked]
    await C.rollback(git, tracked, delete_added=True)
    for p in touched:
        if p in untracked:
            (git.cwd / p).unlink(missing_ok=True)
    return next(s for s in list_shelves(session) if s.id == shelf_id)


async def unshelve(
    session: RepoSession, shelf: Shelf, paths: Sequence[str] | None = None, *, keep: bool = False
) -> None:
    git = session.git
    patch = shelf.patch()
    if paths is not None:
        from .patch import Selection, build_patch

        patch = build_patch(parse_diff(patch), Selection.files(paths))
    try:
        await git.run("apply", "--whitespace=nowarn", "-", input=patch)
    except GitError:
        await git.run("apply", "--3way", "--whitespace=nowarn", "-", input=patch)
    partial = paths is not None and set(paths) != set(shelf.paths)
    if not keep and not partial:
        delete(shelf)


def delete(shelf: Shelf) -> None:
    shutil.rmtree(shelf.dir, ignore_errors=True)


def rename(shelf: Shelf, name: str) -> None:
    meta_file = shelf.dir / "meta.json"
    meta = json.loads(meta_file.read_text())
    meta["name"] = name
    meta_file.write_text(json.dumps(meta, indent=1))
