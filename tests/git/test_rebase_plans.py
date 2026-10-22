from __future__ import annotations

import pytest

from gitpanel.git import scripted_rebase as SR


def T(*items):
    return [SR.TodoItem("pick", s, s.upper()) for s in items]


def test_squash_plan_moves_selected_next_to_oldest():
    todo = T("a", "b", "c", "d")
    new, msg_sha = SR.squash_plan(todo, ["d", "b"])
    assert [(t.action, t.sha) for t in new] == [
        ("pick", "a"),
        ("pick", "b"),
        ("squash", "d"),
        ("pick", "c"),
    ]
    assert msg_sha == "d"


def test_fixup_plan_and_validate():
    todo = T("a", "b", "fx")
    new = SR.fixup_plan(todo, "a", "fx")
    assert [(t.action, t.sha) for t in new] == [("pick", "a"), ("fixup", "fx"), ("pick", "b")]
    with pytest.raises(SR.RebaseError):
        SR.validate([SR.TodoItem("squash", "a"), SR.TodoItem("pick", "b")])
    with pytest.raises(SR.RebaseError):
        SR.validate([SR.TodoItem("drop", "a")])
    with pytest.raises(SR.RebaseError):
        SR.squash_plan(T("a"), ["a"])
