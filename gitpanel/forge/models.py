"""Pull request data, parsed from `gh --json` output."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

CheckState = Literal["success", "failure", "pending", "skipped", "neutral", "cancelled"]
CIState = Literal["success", "failure", "pending", "none"]

_RUN_ID = re.compile(r"/actions/runs/(\d+)")


def _time(value: Any) -> datetime | None:
    if not value or not isinstance(value, str) or value.startswith("0001-"):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _login(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("login") or value.get("name") or "")
    return str(value or "")


@dataclass(frozen=True)
class Check:
    name: str
    state: CheckState
    workflow: str = ""
    url: str = ""
    started: datetime | None = None
    completed: datetime | None = None

    @property
    def run_id(self) -> str | None:
        m = _RUN_ID.search(self.url)
        return m.group(1) if m else None

    @property
    def duration(self) -> float | None:
        if self.started and self.completed:
            return (self.completed - self.started).total_seconds()
        return None

    @classmethod
    def parse(cls, d: dict[str, Any]) -> Check:
        if d.get("__typename") == "StatusContext" or "context" in d:
            state = str(d.get("state", "")).upper()
            mapped: CheckState = {
                "SUCCESS": "success",
                "FAILURE": "failure",
                "ERROR": "failure",
                "PENDING": "pending",
                "EXPECTED": "pending",
            }.get(state, "pending")  # type: ignore[assignment]
            return cls(
                str(d.get("context", "")),
                mapped,
                "",
                str(d.get("targetUrl") or ""),
                _time(d.get("startedAt")),
                None,
            )
        status = str(d.get("status", "")).upper()
        conclusion = str(d.get("conclusion", "")).upper()
        if status != "COMPLETED":
            mapped = "pending"
        else:
            mapped = {
                "SUCCESS": "success",
                "FAILURE": "failure",
                "TIMED_OUT": "failure",
                "ACTION_REQUIRED": "failure",
                "STARTUP_FAILURE": "failure",
                "CANCELLED": "cancelled",
                "SKIPPED": "skipped",
                "NEUTRAL": "neutral",
                "STALE": "neutral",
            }.get(conclusion, "neutral")  # type: ignore[assignment]
        return cls(
            str(d.get("name", "")),
            mapped,
            str(d.get("workflowName") or ""),
            str(d.get("detailsUrl") or ""),
            _time(d.get("startedAt")),
            _time(d.get("completedAt")),
        )


# GraphQL StatusState of a commit's check rollup → CIState
ROLLUP_STATE: dict[str, CIState] = {
    "SUCCESS": "success",
    "FAILURE": "failure",
    "ERROR": "failure",
    "PENDING": "pending",
    "EXPECTED": "pending",
}


def rollup(checks: tuple[Check, ...]) -> CIState:
    if not checks:
        return "none"
    states = {c.state for c in checks}
    if states & {"failure"}:
        return "failure"
    if "pending" in states:
        return "pending"
    if "cancelled" in states:
        return "failure"
    return "success"


@dataclass(frozen=True)
class Review:
    author: str
    state: str  # APPROVED, CHANGES_REQUESTED, COMMENTED, DISMISSED, PENDING
    body: str = ""
    submitted: datetime | None = None


@dataclass(frozen=True)
class Comment:
    author: str
    body: str
    created: datetime | None = None


@dataclass(frozen=True)
class PullRequest:
    number: int
    title: str
    author: str
    state: str  # OPEN, CLOSED, MERGED
    draft: bool
    head: str
    base: str
    head_sha: str = ""
    additions: int = 0
    deletions: int = 0
    changed_files: int = 0
    review_decision: str = ""  # APPROVED, CHANGES_REQUESTED, REVIEW_REQUIRED, ""
    checks: tuple[Check, ...] = ()
    labels: tuple[str, ...] = ()
    url: str = ""
    updated: datetime | None = None
    created: datetime | None = None
    cross_repo: bool = False
    head_owner: str = ""
    # details (only filled by `pr view`)
    body: str = ""
    mergeable: str = ""  # MERGEABLE, CONFLICTING, UNKNOWN
    merge_state: str = ""
    reviewers_requested: tuple[str, ...] = ()
    reviews: tuple[Review, ...] = ()
    comments: tuple[Comment, ...] = ()
    auto_merge: bool = False
    detailed: bool = False
    # overall CI state when the individual checks weren't fetched (see GitHub.statuses)
    ci_state: CIState | None = None
    extra: dict[str, Any] = field(default_factory=dict, compare=False, hash=False)

    @property
    def ci(self) -> CIState:
        if not self.checks and self.ci_state is not None:
            return self.ci_state
        return rollup(self.checks)

    @classmethod
    def parse(cls, d: dict[str, Any], *, detailed: bool = False) -> PullRequest:
        checks = tuple(Check.parse(c) for c in d.get("statusCheckRollup") or [])
        return cls(
            number=int(d["number"]),
            title=str(d.get("title", "")),
            author=_login(d.get("author")),
            state=str(d.get("state", "")),
            draft=bool(d.get("isDraft")),
            head=str(d.get("headRefName", "")),
            base=str(d.get("baseRefName", "")),
            head_sha=str(d.get("headRefOid") or ""),
            additions=int(d.get("additions") or 0),
            deletions=int(d.get("deletions") or 0),
            changed_files=int(d.get("changedFiles") or 0),
            review_decision=str(d.get("reviewDecision") or ""),
            checks=checks,
            labels=tuple(str(lbl.get("name", "")) for lbl in d.get("labels") or []),
            url=str(d.get("url", "")),
            updated=_time(d.get("updatedAt")),
            created=_time(d.get("createdAt")),
            cross_repo=bool(d.get("isCrossRepository")),
            head_owner=_login(d.get("headRepositoryOwner")),
            body=str(d.get("body") or ""),
            mergeable=str(d.get("mergeable") or ""),
            merge_state=str(d.get("mergeStateStatus") or ""),
            reviewers_requested=tuple(_login(r) for r in d.get("reviewRequests") or []),
            reviews=tuple(
                Review(
                    _login(r.get("author")),
                    str(r.get("state", "")),
                    str(r.get("body") or ""),
                    _time(r.get("submittedAt")),
                )
                for r in d.get("latestReviews") or []
            ),
            comments=tuple(
                Comment(
                    _login(c.get("author")), str(c.get("body") or ""), _time(c.get("createdAt"))
                )
                for c in d.get("comments") or []
            ),
            auto_merge=bool(d.get("autoMergeRequest")),
            detailed=detailed,
        )


@dataclass(frozen=True)
class ThreadComment:
    id: str
    database_id: int
    author: str
    body: str
    created: datetime | None = None


@dataclass(frozen=True)
class ReviewThread:
    id: str
    path: str
    line: int | None  # None when the thread is outdated (its line no longer exists)
    side: Literal["LEFT", "RIGHT"]
    resolved: bool
    outdated: bool
    comments: tuple[ThreadComment, ...]

    @classmethod
    def parse(cls, d: dict[str, Any]) -> ReviewThread:
        nodes = (d.get("comments") or {}).get("nodes") or []
        return cls(
            id=str(d.get("id", "")),
            path=str(d.get("path", "")),
            line=d.get("line") if d.get("line") is not None else None,
            side="LEFT" if str(d.get("diffSide", "RIGHT")).upper() == "LEFT" else "RIGHT",
            resolved=bool(d.get("isResolved")),
            outdated=bool(d.get("isOutdated")),
            comments=tuple(
                ThreadComment(
                    str(c.get("id", "")),
                    int(c.get("databaseId") or 0),
                    _login(c.get("author")),
                    str(c.get("body") or ""),
                    _time(c.get("createdAt")),
                )
                for c in nodes
            ),
        )
