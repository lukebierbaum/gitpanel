"""Stacks of local branches: gh-stack's record and branches built on each other.

A stack here is plain branch names, bottom → top; the forge layer joins them with PRs.
gh-stack (`gh stack init` / `add`) keeps its record in `<common-dir>/gh-stack`; gitpanel
reads it and never writes it. Branches it doesn't list are chained by ancestry.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from . import scripted_rebase
from .runner import Git
from .scripted_rebase import RebaseError, RebaseResult, TodoItem
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


# -- building on stacks ------------------------------------------------------------------


async def is_ancestor(git: Git, ancestor: str, rev: str) -> bool:
    res = await git.run("merge-base", "--is-ancestor", ancestor, rev, read_only=True, check=False)
    return res.returncode == 0


async def nearest_base(git: Git, branch: str, candidates: Sequence[str]) -> str | None:
    """The candidate ref `branch` is built on most directly: contained in it, with the fewest
    commits between (None if none is)."""
    best: tuple[int, str] | None = None
    for c in candidates:
        if c == branch or not await is_ancestor(git, c, branch):
            continue
        res = await git.run("rev-list", "--count", f"{c}..{branch}", read_only=True, check=False)
        n = int(res.text.strip() or 0) if res.ok else 0
        if n and (best is None or n < best[0]):
            best = (n, c)
    return best[1] if best else None


# -- restacking --------------------------------------------------------------------------


@dataclass(frozen=True)
class RestackLayer:
    branch: str  # the local branch to move
    head: str  # its tip before the restack
    old_base: str  # the layer's own commits are old_base..head


async def own_commits(git: Git, old_base: str, head: str) -> list[TodoItem]:
    """The layer's commits, oldest first, as picks."""
    res = await git.run(
        "log", "--reverse", "--topo-order", "--format=%H%x1f%P%x1f%s", f"{old_base}..{head}",
        read_only=True,
    )  # fmt: skip
    items = []
    for line in res.text.splitlines():
        sha, parents, subject = line.split("\x1f", 2)
        if len(parents.split()) > 1:
            raise RebaseError(f"{subject!r} is a merge commit; restacking merges isn't supported")
        items.append(TodoItem("pick", sha, subject))
    return items


async def restack_todo(git: Git, layers: Sequence[RestackLayer]) -> list[TodoItem]:
    """One todo for the whole stack: each layer's own commits, then an update-ref moving
    its branch there (the top branch is the one being rebased, so it moves by itself)."""
    todo: list[TodoItem] = []
    for i, layer in enumerate(layers):
        todo += await own_commits(git, layer.old_base, layer.head)
        if i < len(layers) - 1:
            todo.append(TodoItem("update-ref", None, arg=f"refs/heads/{layer.branch}"))
    if not any(t.action == "pick" for t in todo):
        raise RebaseError("The stack has no commits of its own to move")
    return todo


async def restack(
    session: RepoSession, new_base: str, layers: Sequence[RestackLayer]
) -> RebaseResult:
    """Rebase every layer of a stack onto `new_base` in one rebase: checks out the top
    branch, then `scripted_rebase.run` with update-refs for the layers below. Callers run it
    inside one journal mutation, so the whole restack is one undo step."""
    if not layers:
        raise RebaseError("Nothing to restack")
    git = session.git
    todo = await restack_todo(git, layers)
    await git.run("switch", "--quiet", layers[-1].branch)
    return await scripted_rebase.run(session, new_base, todo, autostash=False)
