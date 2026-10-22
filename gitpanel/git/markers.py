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


class PickedMarkers:
    """Commits from other branches whose change the current branch already has."""

    name = "picked"
    invalidated_by = ChangeKind.REFS | ChangeKind.HEAD

    def __init__(self) -> None:
        self._cache: dict[tuple[str, frozenset[str]], set[str]] = {}

    async def markers(
        self, session: RepoSession, shas: Sequence[str]
    ) -> Mapping[str, Sequence[Marker]]:
        from .cherry_pick import picked_shas

        head = await session.git.try_rev_parse("HEAD")
        if not head:
            return {}
        key = (head, frozenset(shas))
        if key not in self._cache:
            self._cache = {key: await picked_shas(session.git, list(shas), head)}
        return {
            sha: [Marker("picked", "✓ picked", "picked", dims_row=True, order=90)]
            for sha in self._cache[key]
        }


class BisectMarkers:
    """good / bad / skipped / currently-testing commits during a bisect."""

    name = "bisect"
    invalidated_by = ChangeKind.REFS | ChangeKind.HEAD | ChangeKind.OPERATION

    async def markers(
        self, session: RepoSession, shas: Sequence[str]
    ) -> Mapping[str, Sequence[Marker]]:
        from .bisect import state

        st = await state(session.git)
        if not st.active:
            return {}
        out: dict[str, list[Marker]] = defaultdict(list)
        if st.bad:
            out[st.bad].append(Marker("bisect-bad", "bad", "bisect-bad", order=1))
        for sha in st.good:
            out[sha].append(Marker("bisect-good", "good", "bisect-good", order=1))
        for sha in st.skipped:
            out[sha].append(Marker("bisect-skip", "skip", "bisect-skip", order=1))
        if st.first_bad:
            out[st.first_bad].append(Marker("bisect-bad", "first bad", "bisect-bad", order=0))
        elif st.current:
            out[st.current].append(Marker("bisect-current", "▶ testing", "bisect-current", order=0))
        wanted = set(shas)
        return {k: v for k, v in out.items() if k in wanted}


def default_providers() -> list[MarkerProvider]:
    return [RefMarkers(), WorktreeMarkers(), PickedMarkers(), BisectMarkers()]


async def collect(
    providers: Sequence[MarkerProvider], session: RepoSession, shas: Sequence[str]
) -> dict[str, list[Marker]]:
    merged: dict[str, list[Marker]] = defaultdict(list)
    for p in providers:
        for sha, ms in (await p.markers(session, shas)).items():
            merged[sha].extend(ms)
    return {k: sorted(v, key=lambda m: (m.order, m.label)) for k, v in merged.items()}
