from __future__ import annotations

import pytest

from gitpanel.actions.builtin import register_builtin
from gitpanel.actions.context import Scope, SelectionContext
from gitpanel.actions.registry import Dispatcher, Registry
from gitpanel.git.log import commit_details
from gitpanel.git.models import Commit
from gitpanel.git.session import RepoSession

from .test_actions import FakeUi


@pytest.fixture
async def env(repo):
    shas = [repo.commit({f"f{i}.txt": f"{i}\n"}, f"commit {i}") for i in range(1, 4)]
    session = await RepoSession.open(repo.path)
    ui = FakeUi()
    return repo, session, ui, Dispatcher(register_builtin(Registry()), ui), shas


async def ctx_for(session, *shas: str) -> SelectionContext:
    commits = tuple([await commit_details(session.git, s) for s in shas])
    return SelectionContext(session, screen="log", focus=Scope.COMMIT, commits=commits)


@pytest.mark.parametrize("mode", ["soft", "mixed", "keep", "hard"])
async def test_reset_each_mode_and_undo(env, mode):
    repo, session, ui, d, (c1, c2, c3) = env
    repo.write("dirty.txt", "local\n")
    ui.choose_answers = [mode]
    await d.dispatch_id("log.reset", await ctx_for(session, c1))
    assert repo.head() == c1
    if mode in ("hard", "keep"):  # both update files the reset commits touched
        assert not (repo.path / "f3.txt").exists()
    else:
        assert (repo.path / "f3.txt").exists()
    await d.dispatch_id("app.undo", SelectionContext(session))
    assert repo.head() == c3
    assert repo.read("dirty.txt") == "local\n"
    assert repo.git("status", "--porcelain").strip() == "?? dirty.txt"


async def test_revert_creates_inverse(env):
    repo, session, ui, d, (c1, c2, c3) = env
    await d.dispatch_id("log.revert", await ctx_for(session, c2))
    assert not (repo.path / "f2.txt").exists()
    assert repo.git("log", "-1", "--format=%s").startswith('Revert "commit 2"')


async def test_reword_head_and_older(env):
    repo, session, ui, d, (c1, c2, c3) = env
    ui.prompt_answers = ["commit 3 better"]
    await d.dispatch_id("log.reword", await ctx_for(session, c3))
    assert repo.git("log", "-1", "--format=%s").strip() == "commit 3 better"
    ui.prompt_answers = ["commit 1 better\n\nwith body"]
    await d.dispatch_id("log.reword", await ctx_for(session, c1))
    assert repo.git("log", "--format=%s").split("\n")[:4] == [
        "commit 3 better",
        "commit 2",
        "commit 1 better",
        "initial",
    ]
    entries = session.journal.entries()
    assert [e.op for e in entries][-1].startswith("Reword")
    await d.dispatch_id("app.undo", SelectionContext(session))
    assert repo.git("log", "--format=%s").split("\n")[2] == "commit 1"


async def test_reword_not_on_branch(env):
    repo, session, ui, d, _ = env
    repo.branch("side")
    repo.checkout("side")
    side = repo.commit({"s.txt": "s\n"}, "side")
    repo.checkout("main")
    ui.prompt_answers = ["new"]
    await d.dispatch_id("log.reword", await ctx_for(session, side))
    assert ui.notifications[-1] == ("error", f"{side[:8]} is not on the current branch")


async def test_new_branch_and_tag(env):
    repo, session, ui, d, (c1, c2, c3) = env
    ui.prompt_answers = ["from-c1"]
    await d.dispatch_id("log.new_branch", await ctx_for(session, c1))
    assert repo.git("branch", "--show-current").strip() == "from-c1" and repo.head() == c1
    ui.prompt_answers = ["v0.1"]
    await d.dispatch_id("log.new_tag", await ctx_for(session, c2))
    assert repo.git("rev-parse", "v0.1").strip() == c2


async def test_checkout_and_copy(env):
    repo, session, ui, d, (c1, c2, c3) = env
    await d.dispatch_id("log.checkout_revision", await ctx_for(session, c2))
    assert repo.git("symbolic-ref", "-q", "HEAD", check=False) == "" and repo.head() == c2
    await d.dispatch_id("log.copy_hash", await ctx_for(session, c1, c2))
    assert ui.clipboard == [f"{c1} {c2}"]


def test_commit_short():
    assert Commit("0123456789abcdef").short == "01234567"
