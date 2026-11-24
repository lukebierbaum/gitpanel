"""Backend models → JSON-ready dicts for the browser UI."""

from __future__ import annotations

import dataclasses
import enum
from datetime import datetime
from pathlib import Path
from typing import Any

from ..actions.context import FileRef
from ..git.graph import GraphRow
from ..git.markers import Marker
from ..git.models import Commit, Ref
from ..git.operations import OperationState
from ..git.patch import FileDiff
from ..git.refs import BranchRef

MAX_DIFF_LINES = 4000  # per file; the rest is cut (the client says so)


def _date(d: datetime | None) -> str | None:
    return d.isoformat() if d else None


def commit_json(c: Commit) -> dict[str, Any]:
    return {
        "sha": c.sha,
        "short": c.short,
        "parents": list(c.parents),
        "subject": c.subject,
        "author": c.author_name,
        "email": c.author_email,
        "date": _date(c.author_date),
    }


def details_json(c: Commit) -> dict[str, Any]:
    body = c.body.split("\n", 1)[1].strip("\n") if "\n" in c.body else ""
    out = commit_json(c)
    out.update(
        body=body,
        committer=c.committer_name,
        committer_email=c.committer_email,
        commit_date=_date(c.commit_date),
    )
    return out


def graph_json(g: GraphRow) -> dict[str, Any]:
    return {
        "col": g.column,
        "color": g.color,
        "edges": [[e.kind, e.lane, e.color] for e in g.edges],
    }


def marker_json(m: Marker) -> dict[str, Any]:
    return {
        "kind": m.kind,
        "label": m.label,
        "style": m.style,
        "dims": m.dims_row,
        "node": m.node_color,
    }


def branch_json(b: BranchRef, current: str | None) -> dict[str, Any]:
    return {
        "name": b.name,
        "short": b.short,
        "sha": b.sha,
        "remote": b.is_remote,
        "upstream": b.upstream_short,
        "ahead": b.ahead,
        "behind": b.behind,
        "gone": b.gone,
        "date": _date(b.date),
        "subject": b.subject,
        "current": b.is_local and b.short == current,
        "worktree": str(b.worktree) if b.worktree else None,
    }


def tag_json(t: Ref) -> dict[str, Any]:
    return {"name": t.name, "short": t.short, "sha": t.sha}


def file_ref_json(ref: FileRef) -> dict[str, Any]:
    st = ref.status
    if ref.group == "conflict":
        letter = "U"
    elif ref.group == "untracked":
        letter = "?"
    elif st is None:
        letter = "M"
    else:
        letter = str(st.staged if ref.group == "staged" else st.unstaged)
    return {
        "path": ref.path,
        "group": ref.group,
        "status": letter,
        "orig_path": st.orig_path if st else None,
        "split": bool(st and st.has_staged and st.has_unstaged),
    }


def diff_summary_json(d: FileDiff) -> dict[str, Any]:
    return {
        "path": d.path,
        "old_path": d.old_path if d.old_path != d.new_path else None,
        "status": d.status,
        "binary": d.binary,
        "additions": d.additions,
        "deletions": d.deletions,
    }


def diff_json(d: FileDiff) -> dict[str, Any]:
    out = diff_summary_json(d)
    hunks, budget, cut = [], MAX_DIFF_LINES, False
    for h in d.hunks:
        if budget <= 0:
            cut = True
            break
        lines = h.lines[:budget]
        cut = cut or len(lines) < len(h.lines)
        budget -= len(lines)
        hunks.append(
            {
                "header": h.header,
                "section": h.section.strip(),
                "lines": [[ln.kind, ln.text, ln.old_no, ln.new_no] for ln in lines],
            }
        )
    out.update(
        hunks=hunks,
        truncated=cut,
        partial=d.partial_allowed,
        mode=(
            f"{d.old_mode} → {d.new_mode}"
            if d.old_mode and d.new_mode and d.old_mode != d.new_mode
            else None
        ),
    )
    return out


def operation_json(op: OperationState | None) -> dict[str, Any] | None:
    if op is None:
        return None
    return {
        "kind": str(op.kind),
        "label": op.label,
        "conflicts": op.conflicts,
        "current": op.current,
    }


def jsonable(value: Any) -> Any:
    """Best-effort conversion of action payloads (paths, dataclasses, enums …) to JSON."""
    if value is None or isinstance(value, bool | int | float | str):
        return value
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, Path | datetime):
        return str(value)
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, list | tuple | set | frozenset):
        return [jsonable(v) for v in value]
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {f.name: jsonable(getattr(value, f.name)) for f in dataclasses.fields(value)}
    return str(value)
