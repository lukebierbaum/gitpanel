from __future__ import annotations

from gitpanel.forge.filters import pr_matches
from gitpanel.forge.models import PullRequest

FUZZ = PullRequest(
    1, "Harden parser with fuzzing", "lin", "OPEN", False, "feature/fuzz", "main",
    labels=("security",), review_decision="REVIEW_REQUIRED",
)  # fmt: skip
CLI = PullRequest(2, "Add CLI flags", "me", "OPEN", True, "feature/cli", "release")


def test_words_match_title_branch_author_labels_and_number():
    assert pr_matches(FUZZ, "parser")
    assert pr_matches(FUZZ, "Feature/FUZZ")
    assert pr_matches(FUZZ, "lin security")
    assert pr_matches(FUZZ, "#1") and not pr_matches(CLI, "#1")
    assert not pr_matches(FUZZ, "parser cli")
    assert pr_matches(FUZZ, "")


def test_qualifiers_and_negation():
    assert pr_matches(FUZZ, "author:lin") and not pr_matches(CLI, "author:@lin")
    assert pr_matches(FUZZ, "label:security") and not pr_matches(CLI, "label:security")
    assert pr_matches(CLI, "base:release head:feature/cli")
    assert pr_matches(CLI, "is:draft") and not pr_matches(FUZZ, "is:draft")
    assert pr_matches(FUZZ, "draft:false") and pr_matches(FUZZ, "-is:draft")
    assert pr_matches(FUZZ, "-author:me") and not pr_matches(CLI, "-author:me")
    assert pr_matches(FUZZ, "sort:updated")  # unknown qualifiers are left to GitHub


def test_status_and_review_use_known_state_and_never_hide_unknown():
    assert pr_matches(FUZZ, "status:failure", ci="failure")
    assert not pr_matches(FUZZ, "status:success", ci="failure")
    assert pr_matches(FUZZ, "status:success")  # CI not loaded yet → keep it
    assert pr_matches(FUZZ, "review:required")
    assert not pr_matches(FUZZ, "review:approved")
    assert pr_matches(FUZZ, "review:approved", review="APPROVED")
    assert pr_matches(CLI, "review:none", review="")
