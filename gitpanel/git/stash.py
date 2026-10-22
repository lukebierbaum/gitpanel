"""git stash."""

from __future__ import annotations

from collections.abc import Sequence

from .models import StashEntry
from .runner import Git, GitResult


async def stashes(git: Git) -> list[StashEntry]:
    res = await git.run("stash", "list", "--format=%gd%x00%H%x00%gs", read_only=True)
    out: list[StashEntry] = []
    for line in res.text.splitlines():
        ref, sha, msg = (line.split("\0") + ["", ""])[:3]
        idx = int(ref[ref.index("{") + 1 : ref.index("}")]) if "{" in ref else len(out)
        out.append(StashEntry(idx, sha, msg))
    return out


async def push(
    git: Git,
    message: str = "",
    *,
    include_untracked: bool = False,
    keep_index: bool = False,
    paths: Sequence[str] = (),
) -> GitResult:
    args = ["stash", "push", "--quiet"]
    if message:
        args += ["-m", message]
    if include_untracked:
        args.append("--include-untracked")
    if keep_index:
        args.append("--keep-index")
    if paths:
        args += ["--", *paths]
    return await git.run(*args)


async def apply(git: Git, ref: str, *, restore_index: bool = False) -> GitResult:
    args = ["stash", "apply", "--quiet"]
    if restore_index:
        args.append("--index")
    return await git.run(*args, ref, check=False)


async def pop(git: Git, ref: str, *, restore_index: bool = False) -> GitResult:
    args = ["stash", "pop", "--quiet"]
    if restore_index:
        args.append("--index")
    return await git.run(*args, ref, check=False)


async def drop(git: Git, ref: str) -> None:
    await git.run("stash", "drop", "--quiet", ref)


async def branch(git: Git, name: str, ref: str) -> None:
    await git.run("stash", "branch", name, ref)
