"""git bisect."""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Literal

from .runner import Git, GitResult

Verdict = Literal["good", "bad", "skip"]
# "<sha> is the first bad commit" (older git) / "is the first 'bad' commit" (newer);
# `git bisect log` records "# first 'bad' commit: [<sha>] subject"
FIRST_BAD = re.compile(
    r"^(?:([0-9a-f]{7,64}) is the first '?bad'? commit"
    r"|# first '?bad'? commit: \[([0-9a-f]{7,64})\])",
    re.M,
)


@dataclass
class BisectState:
    active: bool = False
    bad: str | None = None
    good: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    current: str | None = None
    remaining: int | None = None
    first_bad: str | None = None


async def _refs(git: Git) -> dict[str, str]:
    res = await git.run(
        "for-each-ref", "--format=%(refname) %(objectname)", "refs/bisect/", read_only=True
    )
    out = {}
    for line in res.text.splitlines():
        name, _, sha = line.partition(" ")
        out[name] = sha
    return out


async def state(git: Git) -> BisectState:
    log = await git.run("bisect", "log", check=False, read_only=True)
    if not log.ok:
        return BisectState()
    refs = await _refs(git)
    st = BisectState(active=True)
    st.bad = refs.get("refs/bisect/bad")
    st.good = [sha for name, sha in refs.items() if name.startswith("refs/bisect/good")]
    st.skipped = [sha for name, sha in refs.items() if name.startswith("refs/bisect/skip")]
    st.current = await git.try_rev_parse("HEAD")
    if st.bad and st.good:
        res = await git.run(
            "rev-list", "--count", st.bad, "--not", *st.good, check=False, read_only=True
        )
        if res.ok:
            n = int(res.text.strip() or 0)
            st.remaining = max(n - len(st.skipped), 0)
    st.first_bad = first_bad_in(log.text)
    return st


async def mark(git: Git, verdict: Verdict, sha: str | None = None) -> GitResult:
    """Mark a commit (default: HEAD); starts a bisect session if none is running."""
    if not (await git.run("bisect", "log", check=False, read_only=True)).ok:
        await git.run("bisect", "start")
    args = ["bisect", verdict]
    if sha:
        args.append(sha)
    return await git.run(*args, check=False)


async def reset(git: Git) -> None:
    await git.run("bisect", "reset")


async def run(git: Git, command: str) -> AsyncIterator[str]:
    """`git bisect run sh -c <command>`, streaming its output line by line."""
    async for line in git.stream("bisect", "run", "sh", "-c", command, sep=b"\n", read_only=False):
        yield line.decode("utf-8", "replace")


def first_bad_in(output: str) -> str | None:
    m = FIRST_BAD.search(output)
    return (m.group(1) or m.group(2)) if m else None
