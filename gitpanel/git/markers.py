"""Decorations for Log rows. New kinds of labels are new providers, never graph changes."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from .refs import head_ref, list_refs
from .session import ChangeKind, RepoSession


@dataclass(frozen=True)
class Marker:
    kind: str  # head | branch | remote | tag | worktree | picked | bisect-* | match
    label: str
    style: str = ""  # theme token / CSS class suffix
    dims_row: bool = False
    order: int = 50  # lower sorts first


class MarkerProvider(Protocol):
    name: str
    invalidated_by: ChangeKind

    async def markers(
        self, session: RepoSession, shas: Sequence[str]
    ) -> Mapping[str, Sequence[Marker]]: ...


class RefMarkers:
    """HEAD, local branches, remote branches and tags."""

    name = "refs"
    invalidated_by = ChangeKind.REFS | ChangeKind.HEAD

    async def markers(
        self, session: RepoSession, shas: Sequence[str]
    ) -> Mapping[str, Sequence[Marker]]:
        git = session.git
        wanted = set(shas)
        out: dict[str, list[Marker]] = defaultdict(list)
        current = await head_ref(git)
        head = await git.try_rev_parse("HEAD")
        if head in wanted and current is None:
            out[head].append(Marker("head", "HEAD", "head", order=0))
        for ref in await list_refs(git):
            if ref.sha not in wanted:
                continue
            if ref.is_local:
                is_head = ref.name == current
                label = ref.short
                out[ref.sha].append(
                    Marker(
                        "head" if is_head else "branch",
                        label,
                        "head" if is_head else "branch",
                        order=0 if is_head else 10,
                    )
                )
            elif ref.is_remote:
                out[ref.sha].append(Marker("remote", ref.short, "remote", order=20))
            elif ref.is_tag:
                out[ref.sha].append(Marker("tag", ref.short, "tag", order=30))
        return {k: sorted(v, key=lambda m: (m.order, m.label)) for k, v in out.items()}


class WorktreeMarkers:
    """Commits checked out in *other* worktrees of this repository."""

    name = "worktrees"
    invalidated_by = ChangeKind.WORKTREES | ChangeKind.REFS | ChangeKind.HEAD

    async def markers(
        self, session: RepoSession, shas: Sequence[str]
    ) -> Mapping[str, Sequence[Marker]]:
        wanted = set(shas)
        out: dict[str, list[Marker]] = defaultdict(list)
        active = session.active.path.resolve()
        for wt in await session.worktrees():
            if wt.head in wanted and wt.path.resolve() != active and not wt.bare:
                out[wt.head].append(Marker("worktree", f"⎇ {wt.name}", "worktree", order=5))
        return out


def default_providers() -> list[MarkerProvider]:
    return [RefMarkers(), WorktreeMarkers()]


async def collect(
    providers: Sequence[MarkerProvider], session: RepoSession, shas: Sequence[str]
) -> dict[str, list[Marker]]:
    merged: dict[str, list[Marker]] = defaultdict(list)
    for p in providers:
        for sha, ms in (await p.markers(session, shas)).items():
            merged[sha].extend(ms)
    return {k: sorted(v, key=lambda m: (m.order, m.label)) for k, v in merged.items()}
