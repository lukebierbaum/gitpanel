from __future__ import annotations

import pytest

from gitpanel.actions.builtin import register_builtin
from gitpanel.actions.context import Scope, SelectionContext
from gitpanel.actions.registry import Dispatcher, Registry
from gitpanel.git import scripted_rebase as SR
from gitpanel.git.log import commit_details
from gitpanel.git.operations import detect
from gitpanel.git.session import RepoSession

from .test_actions import FakeUi


@pytest.fixture
async def env(repo):
    shas = [repo.commit({f"f{i}.txt": f"{i}\n"}, f"commit {i}") for i in range(1, 5)]
    session = await RepoSession.open(repo.path)
    ui = FakeUi()
    return repo, session, ui, Dispatcher(register_builtin(Registry()), ui), shas


async def lctx(session, shas):
    commits = tuple([await commit_details(session.git, s) for s in shas])
    return SelectionContext(session, screen="log", focus=Scope.COMMIT, commits=commits)


def subjects(repo):
    return [s for s in repo.git("log", "--format=%s").split("\n") if s]


async def test_squash_non_adjacent_with_message(env):
    repo, session, ui, d, (c1, c2, c3, c4) = env
    ui.prompt_answers = ["one and three"]
    await d.dispatch_id("log.squash", await lctx(session, [c3, c1]))  # log order
    assert subjects(repo) == ["commit 4", "commit 2", "one and three", "initial"]
    assert set(repo.git("show", "--name-only", "--format=", "HEAD~2").split()) == {
        "f1.txt",
        "f3.txt",
    }
    await d.dispatch_id("app.undo", SelectionContext(session))
    assert repo.head() == c4


async def test_drop(env):
    repo, session, ui, d, (c1, c2, c3, c4) = env
    await d.dispatch_id("log.drop", await lctx(session, [c2]))
    assert subjects(repo) == ["commit 4", "commit 3", "commit 1", "initial"]


async def test_fixup_staged(env):
    repo, session, ui, d, (c1, c2, c3, c4) = env
    repo.write("f2.txt", "2 fixed\n")
    repo.git("add", "f2.txt")
    await d.dispatch_id("log.fixup_staged", await lctx(session, [c2]))
    assert subjects(repo) == ["commit 4", "commit 3", "commit 2", "commit 1", "initial"]
    assert repo.git("show", "HEAD~2:f2.txt") == "2 fixed\n"
    assert repo.git("status", "--porcelain") == ""


async def test_editor_start_with_edit_stop_then_continue_applies_later_reword(env):
    repo, session, ui, d, (c1, c2, c3, c4) = env
    base = c1
    todo = await SR.todo_for(session.git, base)  # c2, c3, c4
    todo[0].action = "edit"
    todo[2].action = "reword"
    form = {"todo": tuple(todo), "base": base, "messages": {c4: "commit 4 reworded"}}
    await d.dispatch_id("rebase.start", SelectionContext(session, screen="rebase", form=form))
    op = await detect(session.git)
    assert op is not None and op.kind.value == "rebase"
    repo.write("f2.txt", "2 amended\n")
    repo.git("add", "f2.txt")
    repo.git("commit", "--amend", "--no-edit", "-q")
    await d.dispatch_id("op.continue", SelectionContext(session, operation=op))
    assert await detect(session.git) is None
    assert subjects(repo)[:3] == ["commit 4 reworded", "commit 3", "commit 2"]
    assert repo.git("show", "HEAD~2:f2.txt") == "2 amended\n"
    assert "rebase.done" in ui.posted
