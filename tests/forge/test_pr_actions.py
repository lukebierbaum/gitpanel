from __future__ import annotations

import pytest

from gitpanel.actions.builtin import register_builtin
from gitpanel.actions.context import Scope, SelectionContext
from gitpanel.actions.registry import Dispatcher, Registry
from gitpanel.forge import github as GH
from gitpanel.git import refs as R
from gitpanel.git.session import RepoSession

from ..actions.test_actions import FakeUi


@pytest.fixture
async def env(gh_repo):
    session = await RepoSession.open(gh_repo.repo.path)
    provider = await GH.detect(session)
    ui = FakeUi()
    return gh_repo, session, provider, ui, Dispatcher(register_builtin(Registry()), ui)


async def ctx(session, provider, number, where="list", **form):
    pr = await provider.pr(number)
    return SelectionContext(session, screen="prs", focus=Scope.PR,
                            form={"provider": provider, "pr": pr, "where": where, **form})


async def test_checkout_and_checkout_in_worktree(env, tmp_path):
    gh_repo, session, provider, ui, d = env
    await d.dispatch_id("pr.checkout", await ctx(session, provider, 1))
    assert gh_repo.repo.git("branch", "--show-current").strip() == "feature/fuzz"
    gh_repo.repo.checkout("main")
    await session.refresh_active()
    ui.form_answers = [{"branch": "feature/cli", "path": str(tmp_path / "pr2"), "switch": True}]
    await d.dispatch_id("pr.checkout_worktree", await ctx(session, provider, 2))
    assert session.active.path.resolve() == (tmp_path / "pr2").resolve()
    assert session.active.branch == "refs/heads/feature/cli"


async def test_checkout_with_local_changes_offers_smart(env):
    gh_repo, session, provider, ui, d = env
    gh_repo.repo.write("app.py", "local edit everywhere\n")
    ui.choose_answers = ["smart"]
    await d.dispatch_id("pr.checkout", await ctx(session, provider, 1))
    assert gh_repo.repo.git("branch", "--show-current").strip() == "feature/fuzz"
    assert any("conflict" in m for _, m in ui.notifications) or "local edit" in gh_repo.repo.read(
        "app.py")


async def test_review_comment_merge_close_ready_rerun(env):
    gh_repo, session, provider, ui, d = env
    ui.choose_answers = ["request-changes"]
    ui.prompt_answers = ["Please add a test for the empty input"]
    await d.dispatch_id("pr.review", await ctx(session, provider, 1))
    ui.prompt_answers = ["LGTM otherwise"]
    await d.dispatch_id("pr.comment", await ctx(session, provider, 1))
    await d.dispatch_id("pr.rerun_failed", await ctx(session, provider, 1))
    await d.dispatch_id("pr.ready", await ctx(session, provider, 2))  # draft → ready
    ui.form_answers = [{"method": "squash", "delete": False, "auto": False}]
    await d.dispatch_id("pr.merge", await ctx(session, provider, 1))
    await d.dispatch_id("pr.close", await ctx(session, provider, 2))
    calls = gh_repo.calls()
    assert ["pr", "review", "1", "--request-changes", "--body-file", "-"] in calls
    assert ["run", "rerun", "222", "--failed"] in calls
    assert ["pr", "ready", "2"] in calls
    assert ["pr", "merge", "1", "--squash"] in calls
    assert ["pr", "close", "2"] in calls
    title, fields, body = ui.forms[-1]
    assert title == "Merge #1 into main" and "checks are failing" in body
    assert "prs.refresh" in ui.posted


async def test_create_pr_pushes_first_and_drafts_body(env):
    gh_repo, session, provider, ui, d = env
    repo = gh_repo.repo
    repo.branch("feature/new-thing")
    repo.checkout("feature/new-thing")
    repo.commit({"a.txt": "a\n"}, "Add a")
    repo.commit({"b.txt": "b\n"}, "Add b")
    await session.refresh_active()
    ui.form_answers = [{"title": "New thing", "reviewers": "lin", "draft": True}]
    await d.dispatch_id("pr.create", SelectionContext(
        session, screen="prs", form={"provider": provider}))
    assert ui.confirms and "isn't on GitHub yet" in ui.confirms[0]
    assert "feature/new-thing" in repo.git("ls-remote", "--heads", "origin")
    new = next(p for p in gh_repo.state["prs"] if p["headRefName"] == "feature/new-thing")
    assert new["title"] == "New thing" and new["isDraft"]
    assert new["body"] == "- Add a\n- Add b"
    assert new["reviewRequests"] == [{"login": "lin"}]


async def test_branch_create_pr_action_available_in_branches_popup(env):
    gh_repo, session, provider, ui, d = env
    registry = d.registry
    ref = next(b for b in await R.branches(session.git) if b.short == "feature/fuzz")
    bctx = SelectionContext(session, screen="branches", focus=Scope.BRANCH, refs=(ref,))
    assert registry.get("branch.create_pr").applies(bctx)


async def test_line_comment_reply_resolve(env):
    gh_repo, session, provider, ui, d = env
    ui.prompt_answers = ["Off by one?"]
    line = {"path": "app.py", "side": "RIGHT", "line": 26}
    await d.dispatch_id("pr.line_comment", await ctx(session, provider, 1, "files", line=line))
    [t1, t2] = await provider.threads(1)
    assert t2.line == 26 and t2.comments[0].body == "Off by one?"
    ui.prompt_answers = ["Fixed"]
    await d.dispatch_id("pr.reply", await ctx(session, provider, 1, "files", thread=t1))
    await d.dispatch_id("pr.resolve", await ctx(session, provider, 1, "files", thread=t1))
    [t1, _] = await provider.threads(1)
    assert t1.resolved and t1.comments[-1].body == "Fixed"
    assert ui.posted.count("prs.threads") == 3


async def test_create_pr_without_github_remote_explains(repo):
    session = await RepoSession.open(repo.path)
    ui = FakeUi()
    d = Dispatcher(register_builtin(Registry()), ui)
    ref = next(b for b in await R.branches(session.git) if b.short == "main")
    await d.dispatch_id("branch.create_pr", SelectionContext(
        session, screen="branches", focus=Scope.BRANCH, refs=(ref,)))
    assert ui.notifications[-1][0] == "error" and "GitHub remote" in ui.notifications[-1][1]
