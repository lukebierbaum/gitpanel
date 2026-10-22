"""Reset, revert and checkout of revisions."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

from .runner import Git, GitResult

ResetKind = Literal["soft", "mixed", "keep", "hard"]


async def reset(git: Git, sha: str, mode: ResetKind) -> GitResult:
    return await git.run("reset", f"--{mode}", "-q", sha)


async def revert(git: Git, shas: Sequence[str], *, mainline: int | None = None) -> GitResult:
    args = ["revert", "--no-edit"]
    if mainline:
        args += ["-m", str(mainline)]
    return await git.run(*args, *shas, check=False)


async def checkout_detached(git: Git, sha: str) -> GitResult:
    return await git.run("switch", "--detach", "-q", sha)
