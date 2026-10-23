"""Formatting shared by every window: dates, ages, durations, counts, branch states."""

from __future__ import annotations

from datetime import UTC, datetime


def fmt_date(d: datetime | None) -> str:
    """Local date and time: 2026-10-23 14:05."""
    return d.astimezone().strftime("%Y-%m-%d %H:%M") if d else ""


def fmt_day(d: datetime | None) -> str:
    return d.astimezone().strftime("%Y-%m-%d") if d else ""


def fmt_time(d: datetime | None) -> str:
    """Local time of day with seconds: 14:05:09."""
    return d.astimezone().strftime("%H:%M:%S") if d else ""


def fmt_stamp(d: datetime | None) -> str:
    """Short date and time for logs of recent events: 10-23 14:05:09."""
    return d.astimezone().strftime("%m-%d %H:%M:%S") if d else ""


def ago(when: datetime | None) -> str:
    """How long ago, in the largest unit: 3d, 5h, 12m, now."""
    if when is None:
        return ""
    s = (datetime.now(UTC) - when).total_seconds()
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if s >= size:
            return f"{int(s // size)}{unit}"
    return "now"


def fmt_duration(seconds: float | None) -> str:
    """12 ms · 3.4 s · 2m05s."""
    if seconds is None:
        return ""
    if seconds < 1:
        return f"{seconds * 1000:.0f} ms"
    if seconds < 60:
        return f"{seconds:.1f} s"
    return f"{int(seconds // 60)}m{int(seconds % 60):02d}s"


def plural(n: int, word: str, many: str | None = None) -> str:
    """'1 file', '3 files' ('1 entry', '2 entries' with `many`)."""
    return f"{n} {word if n == 1 else many or word + 's'}"


def ahead_behind(ahead: int, behind: int) -> str:
    return f"↑{ahead} ↓{behind}"


def branch_or_detached(branch: str | None, head: str | None) -> str:
    """The branch name, or where a detached HEAD points."""
    if branch:
        return branch
    return f"detached @ {head[:8]}" if head else "detached (unborn)"
