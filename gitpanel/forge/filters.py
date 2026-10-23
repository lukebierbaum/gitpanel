"""Filtering a loaded PR list as the user types, in GitHub's search syntax.

Words match the number, title, branch, author or labels; qualifiers narrow further:
`author:` `label:` `base:` `head:` `status:success|failure|pending|none`
`review:approved|changes_requested|required|none` `draft:true|false` / `is:draft`.
A leading `-` negates a term. Unknown qualifiers are ignored (GitHub may still understand
them when the text is sent as a search).
"""

from __future__ import annotations

import shlex

from .models import CIState, PullRequest

_REVIEW = {
    "approved": "APPROVED",
    "changes_requested": "CHANGES_REQUESTED",
    "changes": "CHANGES_REQUESTED",
    "required": "REVIEW_REQUIRED",
    "none": "",
}
_STATUS = {"success": "success", "failure": "failure", "pending": "pending", "none": "none"}


def _terms(text: str) -> list[str]:
    try:
        return shlex.split(text)
    except ValueError:  # unbalanced quote while typing
        return text.split()


def pr_matches(
    pr: PullRequest, text: str, *, ci: CIState | None = None, review: str | None = None
) -> bool:
    """Does `pr` match the filter `text`? `ci`/`review` = state known so far (None: unknown,
    which never excludes a PR)."""
    for raw in _terms(text):
        negate = raw.startswith("-") and len(raw) > 1
        term = raw[1:] if negate else raw
        hit = _term(pr, term.lower(), ci, review)
        if hit is None:
            continue
        if hit == negate:
            return False
    return True


def _term(pr: PullRequest, term: str, ci: CIState | None, review: str | None) -> bool | None:
    key, sep, value = term.partition(":")
    if not sep or not value:
        number = term.lstrip("#")
        if number.isdigit() and str(pr.number) == number:
            return True
        hay = " ".join((pr.title, pr.head, pr.author, *pr.labels)).lower()
        return term in hay
    if key == "author":
        return pr.author.lower() == value.lstrip("@")
    if key == "label":
        return any(lbl.lower() == value for lbl in pr.labels)
    if key == "base":
        return pr.base.lower() == value
    if key == "head":
        return pr.head.lower() == value
    if key == "is" and value == "draft":
        return pr.draft
    if key == "draft" and value in ("true", "false"):
        return pr.draft == (value == "true")
    if key == "status" and value in _STATUS:
        state = ci if ci is not None else pr.ci if pr.checks else None
        return None if state is None else state == _STATUS[value]
    if key == "review" and value in _REVIEW:
        decision = review if review is not None else pr.review_decision or None
        return None if decision is None else decision == _REVIEW[value]
    return None
