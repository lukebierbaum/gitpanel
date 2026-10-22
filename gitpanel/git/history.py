"""File history, selection (line range) history and annotate (blame)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from .log import LOG_FORMAT, parse_record
from .models import Commit
from .runner import Git

NOT_COMMITTED = "0" * 40


@dataclass(frozen=True)
class FileRevision:
    commit: Commit
    path: str  # the file's path in this revision (renames followed)
    status: str  # A M D R…
    old_path: str | None = None


async def file_history(git: Git, path: str, limit: int = 2000) -> list[FileRevision]:
    res = await git.run(
        "log",
        "--follow",
        "-M",
        "-z",
        "--name-status",
        f"--max-count={limit}",
        f"--format=%x01{LOG_FORMAT}",
        "--",
        path,
        read_only=True,
    )
    out: list[FileRevision] = []
    for block in res.text.split("\x01"):
        if not block.strip():
            continue
        header, _, rest = block.partition("\0")
        commit = parse_record(header.strip("\n"))
        fields = [f for f in rest.strip("\n").split("\0") if f != ""]
        fields = [f.strip("\n") for f in fields]
        status, old, new = "M", None, path
        if fields:
            status = fields[0][:1]
            if status in "RC" and len(fields) >= 3:
                old, new = fields[1], fields[2]
            elif len(fields) >= 2:
                new = fields[1]
        out.append(FileRevision(commit, new, status, old))
    return out


async def selection_history(git: Git, path: str, start: int, end: int) -> list[Commit]:
    res = await git.run(
        "log",
        f"-L{start},{end}:{path}",
        "--no-patch",
        "-z",
        f"--format={LOG_FORMAT}",
        read_only=True,
    )
    return [parse_record(r.strip("\n")) for r in res.text.split("\0") if r.strip()]


@dataclass(frozen=True)
class BlameLine:
    sha: str
    author: str
    time: datetime | None
    summary: str
    line: int  # 1-based line in the annotated file
    orig_line: int
    orig_path: str
    text: str

    @property
    def uncommitted(self) -> bool:
        return self.sha == NOT_COMMITTED


async def blame(git: Git, path: str, rev: str | None = None) -> list[BlameLine]:
    args = ["blame", "--porcelain", "-M"]
    if rev:
        args.append(rev)
    res = await git.run(*args, "--", path, read_only=True)
    info: dict[str, dict[str, str]] = {}
    lines: list[BlameLine] = []
    cur_sha = ""
    cur: dict[str, int] = {}
    for raw in res.text.split("\n"):
        if raw.startswith("\t"):
            meta = info.get(cur_sha, {})
            t = meta.get("author-time")
            lines.append(
                BlameLine(
                    sha=cur_sha,
                    author=meta.get("author", ""),
                    time=datetime.fromtimestamp(int(t), UTC) if t else None,
                    summary=meta.get("summary", ""),
                    line=cur["final"],
                    orig_line=cur["orig"],
                    orig_path=meta.get("filename", path),
                    text=raw[1:],
                )
            )
            continue
        parts = raw.split(" ")
        if len(parts) >= 3 and len(parts[0]) in (40, 64) and parts[1].isdigit():
            cur_sha = parts[0]
            cur = {"orig": int(parts[1]), "final": int(parts[2])}
            info.setdefault(cur_sha, {})
        elif cur_sha and parts:
            key, _, value = raw.partition(" ")
            info[cur_sha][key] = value
    return lines
