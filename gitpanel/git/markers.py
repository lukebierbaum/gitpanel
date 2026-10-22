"""Decorations for Log rows. New kinds of labels are new providers, never graph changes."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from .refs import head_ref, list_refs
from .runner import Git
from .session import ChangeKind, RepoSession


@dataclass(frozen=True)
class Marker:
    kind: str  # head | branch | remote | tag | worktree | picked | bisect-* | match
    label: str
    style: str = ""  # theme token / CSS class suffix
    dims_row: bool = False
    order: int = 50  # lower sorts first
    node_color: str | None = None  # tint the commit's graph node (an empty label shows no badge)


class MarkerProvider(Protocol):
    name: str
    invalidated_by: ChangeKind
    slow: bool  # True: the Log shows rows first and fills these markers in afterwards

    async def markers(
        self, session: RepoSession, shas: Sequence[str]
    ) -> Mapping[str, Sequence[Marker]]: ...


class RefMarkers:
    """HEAD, local branches, remote branches and tags."""

    name = "refs"
    slow = False
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
    slow = False
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
    slow = True
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
    slow = False
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


UNPUSHED = "#E3B23C"  # amber: on a local branch, on no remote
INCOMING = "#A66BE0"  # purple: on a remote branch, on no local branch


async def refs_state(git: Git) -> str:
    """Cheap fingerprint of HEAD + all branch/remote/tag tips (changes when any ref moves)."""
    res = await git.run(
        "for-each-ref", "--format=%(objectname) %(refname)", read_only=True, check=False
    )
    return f"{await git.try_rev_parse('HEAD')}\n{res.text}"


class SyncMarkers:
    """Which commits exist only locally (not pushed) or only on a remote (not pulled)."""

    name = "sync"
    slow = True
    invalidated_by = ChangeKind.REFS | ChangeKind.HEAD
    LIMIT = 20000

    def __init__(self) -> None:
        # (refs fingerprint, local-only shas, remote-only shas); recomputed when refs move
        self._cache: tuple[str, set[str], set[str]] | None = None

    async def markers(
        self, session: RepoSession, shas: Sequence[str]
    ) -> Mapping[str, Sequence[Marker]]:
        git = session.git
        state = await refs_state(git)
        if "refs/remotes/" not in state:
            return {}  # no remotes: everything is local, colours would just be noise
        if self._cache is None or self._cache[0] != state:
            local = await git.run(
                "rev-list",
                f"--max-count={self.LIMIT}",
                "HEAD",
                "--branches",
                "--not",
                "--remotes",
                check=False,
                read_only=True,
            )
            incoming = await git.run(
                "rev-list",
                f"--max-count={self.LIMIT}",
                "--remotes",
                "--not",
                "HEAD",
                "--branches",
                check=False,
                read_only=True,
            )
            self._cache = (state, set(local.text.split()), set(incoming.text.split()))
        _, local_only, remote_only = self._cache
        out: dict[str, list[Marker]] = {}
        for sha in shas:
            if sha in local_only:
                out[sha] = [Marker("unpushed", "", "unpushed", order=60, node_color=UNPUSHED)]
            elif sha in remote_only:
                out[sha] = [Marker("incoming", "", "incoming", order=60, node_color=INCOMING)]
        return out


def default_providers() -> list[MarkerProvider]:
    return [RefMarkers(), WorktreeMarkers(), SyncMarkers(), PickedMarkers(), BisectMarkers()]


async def collect(
    providers: Sequence[MarkerProvider],
    session: RepoSession,
    shas: Sequence[str],
    *,
    slow: bool | None = None,
) -> dict[str, list[Marker]]:
    """Markers from `providers` (only the fast or slow ones when `slow` is given)."""
    providers = [p for p in providers if slow is None or getattr(p, "slow", False) == slow]
    merged: dict[str, list[Marker]] = defaultdict(list)
    for p in providers:
        for sha, ms in (await p.markers(session, shas)).items():
            merged[sha].extend(ms)
    return {k: sorted(v, key=lambda m: (m.order, m.label)) for k, v in merged.items()}
