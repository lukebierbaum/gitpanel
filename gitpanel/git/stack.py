"""Stacks of local branches: gh-stack's record and branches built on each other.

A stack here is plain branch names, bottom → top; the forge layer joins them with PRs.
gh-stack (`gh stack init` / `add`) keeps its record in `<common-dir>/gh-stack`; gitpanel
reads it and never writes it. Branches it doesn't list are chained by ancestry.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Literal

from .runner import Git
from .session import RepoSession

MAX_CANDIDATES = 40  # branches considered for ancestry chains (one git call each)


@dataclass(frozen=True)
class LocalStack:
    trunk: str
    branches: tuple[str, ...]  # bottom → top
    # each layer's parent head when the layer was made (gh-stack's `base`); "" = unknown
    bases: tuple[str, ...]
    source: Literal["gh-stack", "ancestry"]


def read_gh_stack(session: RepoSession) -> list[LocalStack]:
    """Stacks from gh-stack's record (empty if gh-stack isn't used here)."""
    try:
        data = json.loads((session.common_dir / "gh-stack").read_text())
    except (OSError, ValueError):
        return []
    out = []
    for st in data.get("stacks") or []:
        layers = [b for b in st.get("branches") or [] if b.get("branch")]
        if not layers:
            continue
        out.append(
            LocalStack(
                trunk=str((st.get("trunk") or {}).get("branch") or ""),
                branches=tuple(str(b["branch"]) for b in layers),
                bases=tuple(str(b.get("base") or "") for b in layers),
                source="gh-stack",
            )
        )
    return out


async def local_chains(
    git: Git, trunk: str, exclude: frozenset[str] | set[str] = frozenset()
) -> list[LocalStack]:
    """Unmerged local branches built on each other (not counting `exclude`), as stacks of
    two or more. A branch's parent is the nearest other candidate it contains."""
    res = await git.run(
        "for-each-ref", "--no-merged", trunk, "--format=%(refname:short)%00%(objectname)",
        "refs/heads", read_only=True, check=False,
    )  # fmt: skip
    tips: dict[str, str] = {}
    for line in res.text.splitlines():
        name, _, sha = line.partition("\0")
        if name and name not in exclude and name != trunk:
            tips[name] = sha
    names = sorted(tips)[:MAX_CANDIDATES]
    if len(names) < 2:
        return []

    async def contained(branch: str) -> set[str]:
        r = await git.run(
            "for-each-ref", "--merged", branch, "--format=%(refname:short)", "refs/heads",
            read_only=True, check=False,
        )  # fmt: skip
        found = {n for n in r.text.splitlines() if n in tips and n != branch}
        return {n for n in found if tips[n] != tips[branch]}  # same commit: not stacked

    ancestors = dict(zip(names, await asyncio.gather(*(contained(n) for n in names)), strict=True))
    parent: dict[str, str] = {}
    for b in names:
        if ancestors[b]:
            # the nearest one contains all the others
            parent[b] = max(ancestors[b], key=lambda c: (len(ancestors.get(c, ())), c))
    children: dict[str, list[str]] = {}
    for child, p in parent.items():
        children.setdefault(p, []).append(child)
    stacks = []
    for root in names:
        if root in parent or root not in children:
            continue
        order: list[str] = []
        todo = [root]
        while todo:  # depth-first, children in name order
            b = todo.pop()
            order.append(b)
            todo.extend(sorted(children.get(b, []), reverse=True))
        stacks.append(LocalStack(trunk, tuple(order), ("",) * len(order), "ancestry"))
    return stacks
