from __future__ import annotations

import pytest

from gitpanel.actions.builtin import register_builtin
from gitpanel.actions.context import FileRef, PatchSelection, Scope, SelectionContext
from gitpanel.actions.registry import Dispatcher, Registry
from gitpanel.git import patch as P
from gitpanel.git.log import commit_details
from gitpanel.git.session import RepoSession

from .test_actions import FakeUi


@pytest.fixture
async def env(repo):
    repo.commit({"shared.txt": "base\n"}, "base")
    repo.branch("feature")
    repo.checkout("feature")
    shas = [repo.commit({f"{n}.txt": f"{n}\n"}, f"feature {n}") for n in "abc"]
    repo.checkout("main")
    session = await RepoSession.open(repo.path)
    ui = FakeUi()
    return repo, session, ui, Dispatcher(register_builtin(Registry()), ui), shas


async def lctx(session, shas, picked=(), screen="log"):
    commits = tuple([await commit_details(session.git, s) for s in shas])
    return SelectionContext(
        session, screen=screen, focus=Scope.COMMIT, commits=commits, form={"picked": tuple(picked)}
    )


async def test_cherry_pick_skips_already_picked(env):
    repo, session, ui, d, (a, b, c) = env
    await d.dispatch_id("log.cherry_pick", await lctx(session, [a]))
    await d.dispatch_id("log.cherry_pick", await lctx(session, [c, b, a], picked=[a]))
    assert repo.git("log", "-3", "--format=%s").split("\n")[:3] == [
        "feature c",
        "feature b",
        "feature a",
    ]
    assert any("Skipping 1" in m for _, m in ui.notifications)
    await d.dispatch_id("app.undo", SelectionContext(session))
    assert repo.git("log", "-1", "--format=%s").strip() == "feature a"


async def test_cherry_pick_with_options_records_origin(env):
    repo, session, ui, d, (a, b, c) = env
    ui.form_answers = [{"record_origin": True, "no_commit": False}]
    await d.dispatch_id("log.cherry_pick_options", await lctx(session, [b], screen="compare"))
    assert f"cherry picked from commit {b}" in repo.git("log", "-1", "--format=%B")


async def test_cherry_pick_to_branch(env):
    repo, session, ui, d, (a, b, c) = env
    repo.branch("release", "main")
    ui.choose_answers = ["release"]
    await d.dispatch_id("log.cherry_pick_to", await lctx(session, [c]))
    assert repo.git("log", "-1", "--format=%s", "release").strip() == "feature c"
    assert repo.git("log", "-1", "--format=%s").strip() == "base"


async def test_apply_changes_and_get_from_revision(env):
    repo, session, ui, d, (a, b, c) = env
    [fd] = await P.load_diff(session.git, P.CommitDiff(b))
    ctx = SelectionContext(
        session,
        screen="log",
        focus=Scope.HUNK,
        patch=PatchSelection(P.CommitDiff(b), (fd,), P.Selection.hunks(fd, [0])),
    )
    await d.dispatch_id("log.apply_changes", ctx)
    assert repo.read("b.txt") == "b\n"
    repo.write("shared.txt", "local edit\n")
    ctx = SelectionContext(
        session,
        screen="log",
        focus=Scope.FILE,
        files=(FileRef("shared.txt", P.CommitDiff(a), "commit"),),
    )
    await d.dispatch_id("log.get_from_revision", ctx)
    assert repo.read("shared.txt") == "base\n"
    await d.dispatch_id("app.undo", SelectionContext(session))
    assert repo.read("shared.txt") == "local edit\n"
