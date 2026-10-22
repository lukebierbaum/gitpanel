"""Merge conflicts: stages, a 3-way merge model with per-chunk resolutions, resolving."""

from __future__ import annotations

import tempfile
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from .operations import OpKind, detect
from .runner import Git

MARKER = 15  # long markers so file content is unlikely to collide


@dataclass(frozen=True)
class Conflict:
    path: str
    code: str  # UU, AA, DU, UD, AU, UA, DD

    @property
    def deleted_by_us(self) -> bool:
        return self.code in ("DU", "DD")

    @property
    def deleted_by_them(self) -> bool:
        return self.code in ("UD", "DD")

    @property
    def describe(self) -> str:
        return {
            "UU": "both modified",
            "AA": "both added",
            "DU": "deleted by us",
            "UD": "deleted by them",
            "AU": "added by us",
            "UA": "added by them",
            "DD": "both deleted",
        }.get(self.code, self.code)


async def conflicts(git: Git) -> list[Conflict]:
    from .status import status

    st = await status(git)
    return [Conflict(f.path, f.conflict or "UU") for f in st.unmerged]


@dataclass(frozen=True)
class Stages:
    base: str | None
    ours: str | None
    theirs: str | None
    binary: bool = False


async def _stage(git: Git, n: int, path: str) -> bytes | None:
    res = await git.run("show", f":{n}:{path}", check=False, read_only=True)
    return res.stdout if res.ok else None


async def stages(git: Git, path: str) -> Stages:
    raw = [await _stage(git, n, path) for n in (1, 2, 3)]
    binary = any(r is not None and b"\0" in r[:8000] for r in raw)
    text = [None if r is None else r.decode("utf-8", "surrogateescape") for r in raw]
    return Stages(text[0], text[1], text[2], binary)


# --- merge model -------------------------------------------------------------------

Resolution = Literal["ours", "theirs", "both", "both_reversed", "custom"]


@dataclass
class Common:
    lines: list[str]


@dataclass
class Chunk:
    ours: list[str]
    base: list[str]
    theirs: list[str]
    resolution: Resolution | None = None
    custom: list[str] = field(default_factory=list)

    @property
    def resolved(self) -> bool:
        return self.resolution is not None

    def lines(self) -> list[str] | None:
        match self.resolution:
            case "ours":
                return self.ours
            case "theirs":
                return self.theirs
            case "both":
                return self.ours + self.theirs
            case "both_reversed":
                return self.theirs + self.ours
            case "custom":
                return self.custom
        return None


@dataclass
class MergeModel:
    path: str
    segments: list[Common | Chunk]

    @property
    def chunks(self) -> list[Chunk]:
        return [s for s in self.segments if isinstance(s, Chunk)]

    @property
    def unresolved(self) -> int:
        return sum(1 for c in self.chunks if not c.resolved)

    def result(self) -> str:
        out: list[str] = []
        for seg in self.segments:
            if isinstance(seg, Common):
                out += seg.lines
                continue
            lines = seg.lines()
            if lines is None:
                out.append("<<<<<<< ours\n")
                out += _ensure_nl(seg.ours)
                out.append("=======\n")
                out += _ensure_nl(seg.theirs)
                out.append(">>>>>>> theirs\n")
            else:
                out += lines
        return "".join(out)


def _ensure_nl(lines: list[str]) -> list[str]:
    if lines and not lines[-1].endswith("\n"):
        return lines[:-1] + [lines[-1] + "\n"]
    return lines


def parse_merge_output(path: str, text: str) -> MergeModel:
    start, mid_base, sep, end = "<" * MARKER, "|" * MARKER, "=" * MARKER, ">" * MARKER
    segments: list[Common | Chunk] = []
    common: list[str] = []
    state = "common"
    ours: list[str] = []
    base: list[str] = []
    theirs: list[str] = []
    for line in text.splitlines(keepends=True):
        bare = line.rstrip("\r\n")
        if state == "common" and bare.startswith(start + " "):
            if common:
                segments.append(Common(common))
                common = []
            state, ours, base, theirs = "ours", [], [], []
        elif state == "ours" and bare.startswith(mid_base):
            state = "base"
        elif state in ("ours", "base") and bare == sep:
            state = "theirs"
        elif state == "theirs" and bare.startswith(end):
            segments.append(Chunk(ours, base, theirs))
            state = "common"
        elif state == "common":
            common.append(line)
        elif state == "ours":
            ours.append(line)
        elif state == "base":
            base.append(line)
        else:
            theirs.append(line)
    if common:
        segments.append(Common(common))
    return MergeModel(path, segments)


async def merge_model(git: Git, path: str, st: Stages | None = None) -> MergeModel:
    st = st or await stages(git, path)
    with tempfile.TemporaryDirectory(prefix="gitpanel-merge-") as d:
        files = []
        for name, content in (("ours", st.ours), ("base", st.base), ("theirs", st.theirs)):
            p = Path(d) / name
            p.write_bytes((content or "").encode("utf-8", "surrogateescape"))
            files.append(str(p))
        res = await git.run(
            "merge-file",
            "-p",
            "--diff3",
            f"--marker-size={MARKER}",
            "-L",
            "ours",
            "-L",
            "base",
            "-L",
            "theirs",
            *files,
            check=False,
            read_only=True,
        )
    if res.returncode < 0:
        raise RuntimeError(res.stderr)
    return parse_merge_output(path, res.text)


# --- resolving ---------------------------------------------------------------------


async def resolve_with(git: Git, path: str, text: str) -> None:
    (git.cwd / path).write_bytes(text.encode("utf-8", "surrogateescape"))
    await git.run("add", "--", path)


async def accept(git: Git, paths: Sequence[str], side: Literal["ours", "theirs"]) -> None:
    by_path = {c.path: c for c in await conflicts(git)}
    for p in paths:
        c = by_path.get(p)
        if c is None:
            continue
        deleted = c.deleted_by_us if side == "ours" else c.deleted_by_them
        if deleted:
            await git.run("rm", "-q", "--", p)
        else:
            await git.run("checkout", f"--{side}", "--", p)
            await git.run("add", "--", p)


@dataclass(frozen=True)
class Labels:
    ours: str
    theirs: str


async def labels(git: Git) -> Labels:
    """Human names for the two sides, the way PyCharm shows them."""
    op = await detect(git)
    branch_res = await git.run("branch", "--show-current", check=False, read_only=True)
    branch = branch_res.text.strip() or "HEAD"

    async def describe(rev: str | None) -> str:
        if not rev:
            return "?"
        res = await git.run("log", "-1", "--format=%h %s", rev, check=False, read_only=True)
        return res.text.strip()[:60] or rev[:8]

    if op is None:
        return Labels(f"Yours ({branch})", "Theirs")
    if op.kind is OpKind.MERGE:
        name = await git.run(
            "name-rev", "--name-only", "--no-undefined", "MERGE_HEAD", check=False, read_only=True
        )
        return Labels(f"Yours ({branch})", f"Theirs ({name.text.strip() or 'MERGE_HEAD'})")
    if op.kind is OpKind.REBASE:
        # during a rebase "ours" is the branch being rebased onto, "theirs" your commit
        onto = await describe(op.onto)
        return Labels(f"Onto ({onto})", f"Your commit ({await describe(op.current)})")
    if op.kind in (OpKind.CHERRY_PICK, OpKind.REVERT):
        verb = "Picked" if op.kind is OpKind.CHERRY_PICK else "Reverting"
        return Labels(f"Yours ({branch})", f"{verb} ({await describe(op.current)})")
    return Labels(f"Yours ({branch})", "Theirs")
