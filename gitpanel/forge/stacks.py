"""Putting PRs and branches together into stacks (pure; no gh or git calls).

Three kinds, in order of precedence:
- `github` — a GitHub stack (`PullRequest.stack`), ordered by position;
- `chain` — open same-repo PRs whose bases chain onto each other's branches but that
  aren't a GitHub stack yet;
- `local` — a local stack (gh-stack's record, or branches built on each other) where at
  least one layer has no open PR yet. When its PRs already form a GitHub stack or chain,
  the PR-less layers are added on top of that stack instead.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Literal

from ..git.stack import LocalStack
from .models import PRStatus, PullRequest

StackKind = Literal["github", "chain", "local"]


@dataclass(frozen=True)
class Layer:
    branch: str
    pr: int | None = None  # open PR for this branch, if any
    depth: int = 0  # indentation for chains that fork (GitHub stacks are linear)


@dataclass(frozen=True)
class Stack:
    kind: StackKind
    base: str  # the branch the bottom layer builds on
    layers: tuple[Layer, ...]  # bottom → top
    number: int | None = None  # GitHub stack number

    @property
    def key(self) -> str:
        return (
            f"stack:{self.number}"
            if self.number is not None
            else f"{self.kind}:{self.base}:" + (self.layers[0].branch if self.layers else "")
        )

    @property
    def label(self) -> str:
        if self.kind == "github":
            return f"Stack #{self.number}"
        if self.kind == "local":
            return "local · not a GitHub stack yet"
        return "not a GitHub stack yet"

    @property
    def local_layers(self) -> int:
        """Layers without a PR yet."""
        return sum(ly.pr is None for ly in self.layers)

    @property
    def prs(self) -> list[int]:
        return [ly.pr for ly in self.layers if ly.pr is not None]

    def index_of(self, *, pr: int | None = None, branch: str | None = None) -> int | None:
        for i, ly in enumerate(self.layers):
            if (pr is not None and ly.pr == pr) or (branch is not None and ly.branch == branch):
                return i
        return None


def build_stacks(
    prs: Sequence[PullRequest],
    statuses: Mapping[int, PRStatus],
    local: Sequence[LocalStack] = (),
) -> list[Stack]:
    """Stacks among `prs` (open PRs) and the `local` branch stacks."""
    out: list[Stack] = []
    by_number = {p.number: p for p in prs}
    stacked: set[int] = set()

    # GitHub stacks
    groups: dict[int, list[tuple[int, PullRequest]]] = {}
    bases: dict[int, str] = {}
    for p in prs:
        st = statuses.get(p.number)
        if st is not None and st.stack is not None:
            groups.setdefault(st.stack.number, []).append((st.stack.position, p))
            bases[st.stack.number] = st.stack.base
    for number in sorted(groups):
        members = [p for _, p in sorted(groups[number], key=lambda t: t[0])]
        layers = tuple(Layer(p.head, p.number) for p in members)
        out.append(Stack("github", bases[number] or members[0].base, layers, number))
        stacked |= {p.number for p in members}

    # chained PRs
    heads = {p.head: p for p in prs if not p.cross_repo and p.number not in stacked}
    parent: dict[int, int] = {}
    for p in prs:
        if p.number in stacked:
            continue
        up = heads.get(p.base)
        if up is not None and up.number != p.number:
            parent[p.number] = up.number
    children: dict[int, list[int]] = {}
    for child, up in parent.items():
        children.setdefault(up, []).append(child)
    for p in sorted(prs, key=lambda p: p.number):
        if p.number in parent or p.number not in children or p.number in stacked:
            continue
        layers_l: list[Layer] = []
        todo = [(p.number, 0)]
        while todo:
            n, depth = todo.pop()
            layers_l.append(Layer(by_number[n].head, n, depth))
            todo.extend((c, depth + 1) for c in sorted(children.get(n, []), reverse=True))
        out.append(Stack("chain", p.base, tuple(layers_l)))
        stacked |= {ly.pr for ly in layers_l if ly.pr is not None}

    # local stacks with a layer that has no PR yet
    pr_for_branch = {p.head: p.number for p in prs if not p.cross_repo}
    owner = {ly.pr: i for i, st in enumerate(out) for ly in st.layers if ly.pr is not None}
    seen_local: set[tuple[str, ...]] = set()
    placed: set[str] = set()
    for ls in local:
        if len(ls.branches) < 2 or ls.branches in seen_local:
            continue
        seen_local.add(ls.branches)
        layers = [Layer(b, pr_for_branch.get(b)) for b in ls.branches if b not in placed]
        bare = [ly for ly in layers if ly.pr is None]
        if not bare:
            continue  # every layer has a PR: shown as a GitHub stack or chain instead
        owners = {owner.get(ly.pr) for ly in layers if ly.pr is not None}
        if owners and None not in owners and len(owners) == 1:
            # its PRs are already a stack: the PR-less layers go on top of it
            i = owners.pop()
            assert i is not None
            out[i] = replace(out[i], layers=out[i].layers + tuple(bare))
        else:
            out.append(Stack("local", ls.trunk, tuple(layers)))
        placed |= {ly.branch for ly in bare}
    return out
