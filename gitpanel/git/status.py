"""Parse `git status --porcelain=v2 -z`."""

from __future__ import annotations

from .models import BranchInfo, ChangeType, FileStatus, RepoStatus
from .runner import Git


def _ct(ch: str) -> ChangeType:
    try:
        return ChangeType(ch)
    except ValueError:
        return ChangeType.MODIFIED


def parse_status(raw: bytes) -> RepoStatus:
    records = raw.decode("utf-8", "surrogateescape").split("\0")
    head: str | None = None
    branch: str | None = None
    upstream: str | None = None
    ahead = behind = 0
    files: list[FileStatus] = []
    i = 0
    while i < len(records):
        rec = records[i]
        i += 1
        if not rec:
            continue
        kind = rec[0]
        if kind == "#":
            parts = rec.split(" ")
            key = parts[1]
            if key == "branch.oid":
                head = None if parts[2] == "(initial)" else parts[2]
            elif key == "branch.head":
                branch = None if parts[2] == "(detached)" else " ".join(parts[2:])
            elif key == "branch.upstream":
                upstream = " ".join(parts[2:])
            elif key == "branch.ab":
                ahead = int(parts[2].lstrip("+"))
                behind = int(parts[3].lstrip("-"))
        elif kind == "1":
            # 1 XY sub mH mI mW hH hI path
            fields = rec.split(" ", 8)
            xy, sub, path = fields[1], fields[2], fields[8]
            files.append(FileStatus(path, _ct(xy[0]), _ct(xy[1]), submodule=sub[0] == "S"))
        elif kind == "2":
            # 2 XY sub mH mI mW hH hI Xscore path<NUL>origPath
            fields = rec.split(" ", 9)
            xy, sub, path = fields[1], fields[2], fields[9]
            orig = records[i]
            i += 1
            files.append(
                FileStatus(path, _ct(xy[0]), _ct(xy[1]), orig_path=orig, submodule=sub[0] == "S")
            )
        elif kind == "u":
            # u XY sub m1 m2 m3 mW h1 h2 h3 path
            fields = rec.split(" ", 10)
            xy, sub, path = fields[1], fields[2], fields[10]
            files.append(
                FileStatus(
                    path,
                    ChangeType.UNMERGED,
                    ChangeType.UNMERGED,
                    submodule=sub[0] == "S",
                    conflict=xy,
                )
            )
        elif kind == "?":
            files.append(FileStatus(rec[2:], ChangeType.UNCHANGED, ChangeType.UNTRACKED))
        # "!" (ignored) is never requested
    return RepoStatus(BranchInfo(head, branch, upstream, ahead, behind), tuple(files))


async def status(git: Git) -> RepoStatus:
    res = await git.run(
        "status",
        "--porcelain=v2",
        "-z",
        "--branch",
        "--untracked-files=all",
        "--ignore-submodules=dirty",
        read_only=True,
    )
    return parse_status(res.stdout)
