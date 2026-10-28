from __future__ import annotations

import json
import subprocess

import pytest

from gitpanel.actions.builtin import register_builtin
from gitpanel.actions.context import Scope, SelectionContext
from gitpanel.actions.registry import Dispatcher, Registry
from gitpanel.forge import github as GH
from gitpanel.forge.stacks import build_stacks
from gitpanel.git.session import RepoSession
from gitpanel.git.stack import read_gh_stack

from ..actions.test_actions import FakeUi
from .conftest import _pr


def add_pr(gh_repo, number: int, branch: str, base: str, files: dict[str, str]) -> None:  # type: ignore[no-untyped-def]
    """A pushed branch on top of `base` with an open PR."""
    repo = gh_repo.repo
    repo.git("switch", "-q", "-c", branch, base)
    repo.commit(files, f"work on {branch}")
    repo.git("push", "-q", "-u", "origin", branch)
    sha = repo.head()
    repo.git("--git-dir", str(gh_repo.bare), "update-ref", f"refs/pull/{number}/head", sha)
    gh_repo.update(
        lambda st: st["prs"].append(
            _pr(number, f"Layer {branch}", "me", branch, sha, baseRefName=base)
        )
    )
    repo.checkout("main")


@pytest.fixture
async def env(gh_repo):
    # #1 feature/fuzz ← #3 feature/fuzz-2 ← #4 feature/fuzz-3: a chain, not linked yet
    add_pr(gh_repo, 3, "feature/fuzz-2", "feature/fuzz", {"fuzz2.py": "2\n"})
    add_pr(gh_repo, 4, "feature/fuzz-3", "feature/fuzz-2", {"fuzz3.py": "3\n"})
    gh_repo.update(lambda st: [p.update(isDraft=False) for p in st["prs"]])
    session = await RepoSession.open(gh_repo.repo.path)
    provider = await GH.detect(session)
    assert provider is not None
    ui = FakeUi()
    return gh_repo, session, provider, ui, Dispatcher(register_builtin(Registry()), ui)


async def stack_ctx(session, provider, *, pr=None, branch=None, local=False):  # type: ignore[no-untyped-def]
    """The PR window's context for a PR (or local branch row) in a stack."""
    index = await provider.open_pr_index()
    statuses = await provider.statuses([p.number for p in index])
    stacks = build_stacks(index, statuses, read_gh_stack(session) if local else [])
    for stack in stacks:
        i = stack.index_of(pr=pr, branch=branch)
        if i is not None:
            break
    else:
        raise AssertionError("not in a stack")
    form = {"provider": provider, "stack": stack, "stack_index": i, "where": "list"}
    if pr is not None:
        form["pr"] = await provider.pr(pr)
    else:
        from gitpanel.ui.screens.prs import BranchRow

        form["branch_row"] = BranchRow(branch)
    return SelectionContext(session, screen="prs", focus=Scope.PR, form=form)


def options(ui) -> list[str]:  # type: ignore[no-untyped-def]
    return [v for v, _ in ui.forms[-1][1][0].options]


async def test_link_chain_then_merge_choices(env):
    gh_repo, session, provider, ui, d = env
    await d.dispatch_id("pr.stack_link", await stack_ctx(session, provider, pr=3))
    assert gh_repo.state["stacks"][0]["prs"] == [1, 3, 4]
    assert any("Link #1–#4" in c for c in ui.confirms)

    # top: the whole stack is the only choice; cancel
    ui.form_answers = [None]
    await d.dispatch_id("pr.merge", await stack_ctx(session, provider, pr=4))
    assert options(ui) == ["whole"]
    # bottom: whole stack (default) or just this one
    ui.form_answers = [None]
    await d.dispatch_id("pr.merge", await stack_ctx(session, provider, pr=1))
    assert options(ui) == ["whole", "upto"]
    # middle: up to here (default) or the whole stack — merge up to here
    await d.dispatch_id("pr.merge", await stack_ctx(session, provider, pr=3))
    assert options(ui) == ["upto", "whole"]
    states = {p["number"]: p["state"] for p in gh_repo.state["prs"]}
    assert states[1] == states[3] == "MERGED" and states[4] == "OPEN"
    assert ["stack", "merge", "3", "--yes", "--merge-method", "squash"] in gh_repo.calls()
    assert gh_repo.state["stacks"][0]["prs"] == [4]


async def test_merging_a_chain_links_it_first(env):
    gh_repo, session, provider, ui, d = env
    await d.dispatch_id("pr.merge", await stack_ctx(session, provider, pr=4))
    assert options(ui) == ["link-whole", "plain"]
    calls = gh_repo.calls()
    assert ["stack", "link", "1", "3", "4"] in calls
    assert ["stack", "merge", "4", "--yes", "--merge-method", "squash"] in calls
    assert all(p["state"] == "MERGED" for p in gh_repo.state["prs"] if p["number"] in (1, 3, 4))


async def test_publish_a_local_stack_as_drafts(env):
    gh_repo, session, provider, ui, d = env
    repo = gh_repo.repo
    for name, parent in (("l1", "main"), ("l2", "l1")):
        repo.git("switch", "-q", "-c", name, parent)
        repo.commit({f"{name}.txt": name}, name)
    repo.checkout("main")
    (session.common_dir / "gh-stack").write_text(json.dumps({"stacks": [{
        "trunk": {"branch": "main"},
        "branches": [{"branch": "l1", "base": ""}, {"branch": "l2", "base": ""}],
    }]}))  # fmt: skip
    await d.dispatch_id(
        "pr.stack_link", await stack_ctx(session, provider, branch="l2", local=True)
    )
    assert ["stack", "link", "--base", "main", "l1", "l2"] in gh_repo.calls()
    new = {p["headRefName"]: p for p in gh_repo.state["prs"] if p["headRefName"] in ("l1", "l2")}
    assert new["l1"]["isDraft"] and new["l2"]["baseRefName"] == "l1"
    assert "open 2 draft pull requests" in ui.confirms[-1]


async def test_new_branch_on_top_and_undo(env):
    gh_repo, session, provider, ui, d = env
    ui.prompt_answers = ["feature/fuzz-4"]
    await d.dispatch_id("pr.create_on_top", await stack_ctx(session, provider, pr=4))
    repo = gh_repo.repo
    assert repo.git("branch", "--show-current").strip() == "feature/fuzz-4"
    assert repo.git("rev-parse", "HEAD").strip() == repo.git("rev-parse", "feature/fuzz-3").strip()
    await session.journal.undo()
    assert repo.git("branch", "--show-current").strip() == "main"
    assert "feature/fuzz-4" not in repo.git("branch", "--list", "feature/fuzz-4")


async def test_create_pr_on_a_stacked_branch_joins_the_stack(env):
    gh_repo, session, provider, ui, d = env
    await provider.stack_link(["1", "3", "4"])
    repo = gh_repo.repo
    repo.git("switch", "-q", "-c", "feature/fuzz-4", "feature/fuzz-3")
    repo.commit({"fuzz4.py": "4\n"}, "Fourth layer")
    await session.refresh_active()
    ui.confirm_answer = True  # push first
    ctx = SelectionContext(session, screen="prs", focus=Scope.GLOBAL, form={"provider": provider})
    await d.dispatch_id("pr.create", ctx)
    title, fields, body = ui.forms[-1]
    defaults = {f.name: f.default for f in fields}
    assert defaults["base"] == "feature/fuzz-3" and defaults["join"] is True
    assert "Fourth layer" in body and "work on" not in body  # only this layer's commits
    new = next(p for p in gh_repo.state["prs"] if p["headRefName"] == "feature/fuzz-4")
    assert new["baseRefName"] == "feature/fuzz-3"
    assert gh_repo.state["stacks"][0]["prs"] == [1, 3, 4, new["number"]]


def remote_log(gh_repo, rng: str) -> list[str]:  # type: ignore[no-untyped-def]
    out = subprocess.run(
        ["git", "--git-dir", str(gh_repo.bare), "log", "--format=%s", rng],
        capture_output=True, text=True, check=True,
    ).stdout  # fmt: skip
    return out.splitlines()


async def test_restack_after_the_bottom_was_squash_merged(env):
    gh_repo, session, provider, ui, d = env
    await provider.stack_link(["1", "3", "4"])
    await provider.stack_merge(1, "squash")  # main gets one squashed commit
    repo = gh_repo.repo
    before = {b: repo.git("rev-parse", b).strip() for b in ("feature/fuzz-2", "feature/fuzz-3")}
    await d.dispatch_id("pr.restack", await stack_ctx(session, provider, pr=3))
    assert ui.errors == []
    # each layer now has only its own commit on top of the new main — on GitHub too
    assert remote_log(gh_repo, "main..feature/fuzz-2") == ["work on feature/fuzz-2"]
    assert remote_log(gh_repo, "feature/fuzz-2..feature/fuzz-3") == ["work on feature/fuzz-3"]
    assert any("Force-push" in c for c in ui.confirms)
    assert repo.git("branch", "--show-current").strip() == "main"  # back where we were
    # undo puts the local branches back (the push stays)
    await session.journal.undo()
    after = {b: repo.git("rev-parse", b).strip() for b in before}
    assert after == before


async def test_restack_refuses_with_local_changes(env):
    gh_repo, session, provider, ui, d = env
    gh_repo.repo.write("app.py", "edited\n")
    await d.dispatch_id("pr.restack", await stack_ctx(session, provider, pr=3))
    assert any("local changes" in m for _, m in ui.notifications + ui.errors)
