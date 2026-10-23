from __future__ import annotations

import pytest

from gitpanel.forge import github as GH
from gitpanel.forge.models import PullRequest
from gitpanel.forge.runner import ForgeError
from gitpanel.git.console import CONSOLE
from gitpanel.git.session import RepoSession


@pytest.fixture
async def gh(gh_repo):
    session = await RepoSession.open(gh_repo.repo.path)
    provider = await GH.detect(session)
    assert provider is not None
    return gh_repo, session, provider


async def test_detect_needs_github_remote(repo):
    session = await RepoSession.open(repo.path)
    assert await GH.detect(session) is None


async def test_list_queries_and_parse(gh):
    gh_repo, session, provider = gh
    mine = await provider.list_prs("mine")
    assert [p.number for p in mine] == [2] and mine[0].draft
    review = await provider.list_prs("review")
    assert [p.number for p in review] == [1]
    pr = review[0]
    assert pr.author == "lin" and pr.head == "feature/fuzz" and pr.labels == ("security",)
    assert [c.state for c in pr.checks] == ["success", "failure"] and pr.ci == "failure"
    assert pr.checks[0].duration == 150 and pr.checks[1].run_id == "222"
    assert [p.number for p in await provider.list_prs("open", "parser")] == [1]
    assert await provider.list_prs("closed") == []
    assert any(e.kind == "gh" and e.command.startswith("gh pr list") for e in CONSOLE.entries)


async def test_details_threads_and_current(gh):
    gh_repo, session, provider = gh
    pr = await provider.pr(1)
    assert pr.detailed and pr.body == "Body of #1" and pr.mergeable == "MERGEABLE"
    [thread] = await provider.threads(1)
    assert thread.path == "app.py" and thread.line == 4 and thread.side == "RIGHT"
    assert thread.comments[0].body == "Why upper-case here?"
    assert await provider.current_pr() is None  # on main
    gh_repo.repo.checkout("feature/cli")
    current = await provider.current_pr()
    assert current is not None and current.number == 2


async def test_fetch_head_merge_base_and_checkout(gh):
    gh_repo, session, provider = gh
    pr = await provider.pr(1)
    head = await provider.fetch_head(pr)
    assert head == pr.head_sha
    base = await provider.merge_base(pr)
    assert base == gh_repo.repo.git("rev-parse", "main").strip()
    await provider.checkout(1)
    assert gh_repo.repo.git("branch", "--show-current").strip() == "feature/fuzz"


async def test_actions_record_and_update(gh):
    gh_repo, session, provider = gh
    await provider.review(1, "approve")
    await provider.review(1, "request-changes", "please add tests")
    await provider.comment(1, "nice\nwork")
    await provider.ready(2)
    await provider.merge(1, "squash", delete_branch=True)
    await provider.close(2)
    pr1 = PullRequest.parse(next(p for p in gh_repo.state["prs"] if p["number"] == 1))
    assert pr1.state == "MERGED" and pr1.review_decision == "CHANGES_REQUESTED"
    calls = gh_repo.calls()
    assert ["pr", "merge", "1", "--squash", "--delete-branch"] in calls
    assert ["pr", "ready", "2"] in calls and ["pr", "close", "2"] in calls
    assert gh_repo.state["prs"][0]["comments"][-1]["body"] == "nice\nwork"


async def test_create_rerun_and_threads(gh):
    gh_repo, session, provider = gh
    repo = gh_repo.repo
    repo.branch("feature/new")
    repo.checkout("feature/new")
    repo.commit({"n.txt": "n\n"}, "New thing")
    repo.git("push", "-q", "-u", "origin", "feature/new")
    url = await provider.create(
        "feature/new", "main", "New thing", "Body\n- point", draft=True, reviewers=["lin"]
    )
    assert url.endswith("/pull/3")
    pr3 = await provider.pr(3)
    assert pr3.draft and pr3.body == "Body\n- point" and pr3.reviewers_requested == ("lin",)

    pr1 = await provider.pr(1)
    assert await provider.rerun_failed(pr1) == ["222"]
    assert ["run", "rerun", "222", "--failed"] in gh_repo.calls()
    with pytest.raises(ForgeError):
        await provider.rerun_failed(pr3)

    await provider.add_line_comment(pr1, "app.py", 26, "RIGHT", "and here")
    threads = await provider.threads(1)
    assert len(threads) == 2 and threads[1].line == 26
    await provider.reply(pr1, 1001, "because of the spec")
    await provider.set_resolved("T1", True)
    threads = await provider.threads(1)
    assert threads[0].resolved and threads[0].comments[-1].body == "because of the spec"


async def test_error_explanations(gh, monkeypatch):
    gh_repo, session, provider = gh
    with pytest.raises(ForgeError, match="not found"):
        await provider.pr(99)
    monkeypatch.setenv("PATH", "/nonexistent")
    with pytest.raises(ForgeError, match="isn't installed"):
        await provider.list_prs()
