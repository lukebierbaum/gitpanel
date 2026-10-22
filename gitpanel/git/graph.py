"""Commit graph lane assignment for the Log (incremental across pages)."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from .models import Commit

NODE = "●"
MERGE_NODE = "◆"
ROOT_NODE = "◉"


@dataclass(frozen=True)
class GraphRow:
    sha: str
    column: int
    cells: tuple[tuple[str, int], ...]  # (char, lane colour) for 2 * lanes columns

    @property
    def width(self) -> int:
        return len(self.cells)

    @property
    def text(self) -> str:
        return "".join(c for c, _ in self.cells).rstrip()


@dataclass
class _Lane:
    sha: str
    color: int


class GraphBuilder:
    """Feed commits in topological order (newest first); get one GraphRow per commit.

    State is kept between `add()` calls so pages can be appended.
    """

    def __init__(self, max_lanes: int = 24) -> None:
        self.lanes: list[_Lane | None] = []
        self._next_color = 0
        self.max_lanes = max_lanes

    def _color(self) -> int:
        c = self._next_color
        self._next_color += 1
        return c

    def _free_slot(self, lanes: list[_Lane | None], avoid: int = -1) -> int:
        for i, lane in enumerate(lanes):
            if lane is None and i != avoid:
                return i
        lanes.append(None)
        return len(lanes) - 1

    def add_all(self, commits: Iterable[Commit]) -> list[GraphRow]:
        return [self.add(c) for c in commits]

    def add(self, commit: Commit) -> GraphRow:
        prev = list(self.lanes)
        sha = commit.sha
        hits = [i for i, lane in enumerate(prev) if lane is not None and lane.sha == sha]
        if hits:
            col = hits[0]
            color = prev[col].color  # type: ignore[union-attr]
        else:
            col = self._free_slot(prev)
            color = self._color()
        merging_in = hits[1:]

        nxt: list[_Lane | None] = list(prev)
        while len(nxt) <= col:
            nxt.append(None)
        for i in merging_in:
            nxt[i] = None
        parents: Sequence[str] = commit.parents
        nxt[col] = _Lane(parents[0], color) if parents else None

        branch_out: list[int] = []
        for p in parents[1:]:
            existing = next(
                (i for i, lane in enumerate(nxt) if lane is not None and lane.sha == p), None
            )
            if existing is not None:
                branch_out.append(existing)
            else:
                slot = self._free_slot(nxt, avoid=col)
                if slot >= self.max_lanes:
                    continue  # too wide: drop the edge rather than the row
                nxt[slot] = _Lane(p, self._color())
                branch_out.append(slot)

        while nxt and nxt[-1] is None:
            nxt.pop()
        self.lanes = nxt

        width = max(len(prev), len(nxt), col + 1)
        cells: list[tuple[str, int]] = [(" ", -1)] * (2 * width)

        def lane_at(lanes: list[_Lane | None], i: int) -> _Lane | None:
            return lanes[i] if i < len(lanes) else None

        # pass-through lanes
        for i in range(width):
            if i == col:
                continue
            before, after = lane_at(prev, i), lane_at(nxt, i)
            if before is not None and after is not None and before.sha == after.sha:
                cells[2 * i] = ("│", before.color)

        # horizontal connectors to merging/branching lanes: lines first, then endpoints
        targets = [(t, True) for t in merging_in] + [(t, False) for t in branch_out]

        def tcolor_of(t: int) -> int:
            return (lane_at(nxt, t) or lane_at(prev, t) or _Lane("", color)).color

        for t, _merge in targets:
            lo, hi = sorted((col, t))
            for x in range(2 * lo + 1, 2 * hi):
                ch, c = cells[x]
                if ch in ("│", "┼"):
                    cells[x] = ("┼", c)
                elif ch == " ":
                    cells[x] = ("─", tcolor_of(t))
        for t, merge in targets:
            right = t > col
            farther = any((o > t) if right else (o < t) for o, _ in targets)
            if farther:
                glyph = "┴" if merge else "┬"
            elif merge:
                glyph = "╯" if right else "╰"
            else:
                glyph = "╮" if right else "╭"
            cells[2 * t] = (glyph, tcolor_of(t))

        node = MERGE_NODE if len(parents) > 1 else (ROOT_NODE if not parents else NODE)
        cells[2 * col] = (node, color)
        return GraphRow(sha, col, tuple(cells))


def rows_for(commits: Sequence[Commit]) -> list[GraphRow]:
    return GraphBuilder().add_all(commits)
