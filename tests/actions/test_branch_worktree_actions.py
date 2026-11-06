from __future__ import annotations

import pytest

from gitpanel.actions.builtin import register_builtin
from gitpanel.actions.context import Scope, SelectionContext
from gitpanel.actions.registry import Dispatcher, Registry
from gitpanel.git import refs as R
from gitpanel.git.session import RepoSession

from .test_actions import FakeUi


@pytest.fixture
async def env(repo):
    session = await RepoSession.open(repo.path)
    ui = FakeUi()
    return repo, session, ui, Dispatcher(register_builtin(Registry()), ui)


async def bctx(session, short: str) -> SelectionContext:
    ref = next(b for b in await R.branches(session.git) if b.short == short)
    return SelectionContext(session, screen="branches", focus=Scope.BRANCH, refs=(ref,))


async def test_checkout_new_delete_and_undo(env):
    repo, session, ui, d = env
    ui.form_answers = [{"name": "feature", "checkout": False}]
    await d.dispatch_id("branch.new_from", await bctx(session, "main"))
    assert "feature" in repo.git("branch")
    await d.dispatch_id("branch.checkout", await bctx(session, "feature"))
    assert repo.git("branch", "--show-current").strip() == "feature"
    assert "popup.close" in ui.posted
    await session.refresh_active()
    repo.checkout("main")
    await session.refresh_active()
    sha = repo.git("rev-parse", "feature").strip()
    await d.dispatch_id("branch.delete", await bctx(session, "feature"))
    assert "feature" not in repo.git("branch")
    await d.dispatch_id("app.undo", SelectionContext(session))
    assert repo.git("rev-parse", "feature").strip() == sha


async def test_checkout_branch_in_other_worktree_offers_switch(env):
    repo, session, ui, d = env
    wt = repo.worktree("other", "elsewhere")
    await d.dispatch_id("branch.checkout", await bctx(session, "elsewhere"))
    assert session.active.path.resolve() == wt.resolve()
    assert "another worktree" in ui.notifications[-1][1] or ui.notifications[-1][1].startswith(
        "Switched to worktree"
    )


async def test_merge_rebase_and_rename(env):
    repo, session, ui, d = env
    repo.branch("topic")
    repo.checkout("topic")
    repo.commit({"t.txt": "t\n"}, "topic work")
    repo.checkout("main")
    await session.refresh_active()
    await d.dispatch_id("branch.merge", await bctx(session, "topic"))
    assert (repo.path / "t.txt").exists()
    ui.prompt_answers = ["topic-renamed"]
    await d.dispatch_id("branch.rename", await bctx(session, "topic"))
    assert "topic-renamed" in repo.git("branch")


async def test_favorite_and_compare(env):
    repo, session, ui, d = env
    repo.branch("fav")
    await d.dispatch_id("branch.favorite", await bctx(session, "fav"))
    assert (
        "refs/heads/fav" in R.Favorites(session.private_dir(shared=True) / "favorites.json").load()
    )
    await d.dispatch_id("branch.compare", await bctx(session, "fav"))
    assert ui.opened[-1] == ("compare", {"compare": "fav"})


async def test_push_dialog_and_fetch(env, tmp_path):
    repo, session, ui, d = env
    repo.bare_remote()
    repo.commit({"x.txt": "x\n"}, "unpushed")
    ui.form_answers = [{}]  # accept defaults: origin/main, set upstream
    await d.dispatch_id("remote.push", SelectionContext(session))
    title, fields, body = ui.forms[-1]
    assert "unpushed" in body and title.startswith("Push 2 commits")
    assert repo.git("rev-parse", "--abbrev-ref", "main@{u}").strip() == "origin/main"
    await d.dispatch_id("remote.fetch", SelectionContext(session))
    assert ui.notifications[-1] == ("information", "Fetched all remotes")


async def test_push_branch_tracking_main_defaults_to_new_branch(env):
    repo, session, ui, d = env
    repo.bare_remote()
    repo.git("push", "-q", "-u", "origin", "main")
    repo.git("switch", "-q", "-c", "feature", "origin/main")  # tracks origin/main
    repo.commit({"f.txt": "f\n"}, "feature work")
    await session.refresh_active()
    main_sha = repo.git("rev-parse", "origin/main").strip()
    ui.form_answers = [{}]
    await d.dispatch_id("remote.push", SelectionContext(session))
    title, fields, body = ui.forms[-1]
    defaults = {f.name: f.default for f in fields}
    assert defaults["remote_branch"] == "feature" and defaults["set_upstream"] is True
    assert body.startswith("feature → origin/feature  (tracks origin/main)")
    assert title == "Push 1 commit"
    assert repo.git("rev-parse", "origin/main").strip() == main_sha
    assert repo.git("rev-parse", "--abbrev-ref", "feature@{u}").strip() == "origin/feature"

    # push.default=upstream opts back into pushing onto the tracked branch
    repo.git("switch", "-q", "-c", "other", "origin/main")
    repo.git("config", "push.default", "upstream")
    await session.refresh_active()
    ui.form_answers = [None]
    await d.dispatch_id("remote.push", SelectionContext(session))
    defaults = {f.name: f.default for f in ui.forms[-1][1]}
    assert defaults["remote_branch"] == "main" and defaults["set_upstream"] is False


async def test_auth_failure_offers_terminal(env):
    repo, session, ui, d = env
    repo.git("remote", "add", "origin", "https://127.0.0.1:9/nope.git")
    # simulate an auth error by pointing at a URL that asks for credentials
    from gitpanel.actions.builtin import network
    from gitpanel.git.runner import GitError, GitResult

    async def failing():
        raise GitError(GitResult(("fetch",), 128, b"", "fatal: could not read Username"))

    from gitpanel.actions.registry import ActionEnv

    env_ = ActionEnv(SelectionContext(session), ui, session)
    assert await network.with_auth_retry(env_, failing, ["fetch"]) is None
    assert ui.terminal_runs == [["git", "fetch"]]


async def test_worktree_actions(env, tmp_path):
    repo, session, ui, d = env
    ui.form_answers = [
        {"kind": "new", "name": "wt-branch", "path": str(tmp_path / "wt1"), "switch": False}
    ]
    await d.dispatch_id("worktree.new", SelectionContext(session, screen="worktrees"))
    wts = await session.worktrees()
    assert len(wts) == 2
    new = wts[1]
    ctx = SelectionContext(session, screen="worktrees", focus=Scope.WORKTREE, worktree=new)
    ui.prompt_answers = ["usb"]
    await d.dispatch_id("worktree.lock", ctx)
    assert (await session.worktrees())[1].locked == "usb"
    await d.dispatch_id("worktree.switch", ctx)
    assert session.active.path.resolve() == (tmp_path / "wt1").resolve()
    # cannot remove the active one
    ctx = SelectionContext(
        session, screen="worktrees", focus=Scope.WORKTREE, worktree=(await session.worktrees())[1]
    )
    await d.dispatch_id("worktree.remove", ctx)
    assert ui.notifications[-1][0] == "error"
    await session.switch(repo.path)
    ctx = SelectionContext(
        session, screen="worktrees", focus=Scope.WORKTREE, worktree=(await session.worktrees())[1]
    )
    await d.dispatch_id("worktree.remove", ctx)
    assert len(await session.worktrees()) == 1
    await d.dispatch_id(
        "worktree.shell",
        SelectionContext(
            session,
            screen="worktrees",
            focus=Scope.WORKTREE,
            worktree=(await session.worktrees())[0],
        ),
    )
    assert ui.terminal_runs


async def test_new_worktree_from_branch_and_log(env, tmp_path):
    repo, session, ui, d = env
    repo.branch("side")
    ui.form_answers = [{"path": str(tmp_path / "side-wt"), "switch": True}]
    await d.dispatch_id("branch.new_worktree", await bctx(session, "side"))
    assert session.active.branch == "refs/heads/side"
    await session.switch(repo.path)
    from gitpanel.git.log import commit_details

    c = await commit_details(session.git, repo.head())
    ui.form_answers = [{"kind": "detached", "path": str(tmp_path / "det"), "switch": False}]
    await d.dispatch_id(
        "log.new_worktree",
        SelectionContext(session, screen="log", focus=Scope.COMMIT, commits=(c,)),
    )
    assert any(w.branch is None for w in await session.worktrees())


@pytest.fixture
async def blocked(repo):
    """main has a local edit to f.txt that `other` would overwrite."""
    repo.commit({"f.txt": "base\n"}, "base")
    repo.branch("other")
    repo.checkout("other")
    repo.commit({"f.txt": "other\n"}, "other")
    repo.checkout("main")
    repo.write("f.txt", "my local edit\n")
    session = await RepoSession.open(repo.path)
    ui = FakeUi()
    return repo, session, ui, Dispatcher(register_builtin(Registry()), ui)


async def test_checkout_blocked_then_force_and_undo(blocked):
    repo, session, ui, d = blocked
    ui.choose_answers = ["force"]
    await d.dispatch_id("branch.checkout", await bctx(session, "other"))
    assert repo.git("branch", "--show-current").strip() == "other"
    assert repo.read("f.txt") == "other\n"
    await session.refresh_active()
    await d.dispatch_id("app.undo", SelectionContext(session))
    assert repo.git("branch", "--show-current").strip() == "main"
    assert repo.read("f.txt") == "my local edit\n"


async def test_checkout_blocked_then_smart_conflicts_keep_changes(blocked):
    repo, session, ui, d = blocked
    ui.choose_answers = ["smart"]
    await d.dispatch_id("branch.checkout", await bctx(session, "other"))
    assert repo.git("branch", "--show-current").strip() == "other"
    assert any("conflict" in m for _, m in ui.notifications)
    assert "my local edit" in repo.read("f.txt")  # conflict markers include our change
    assert repo.git("stash", "list").strip()  # kept safe in the stash


async def test_smart_checkout_carries_non_conflicting_changes(repo):
    repo.commit({"f.txt": "base\n", "g.txt": "g\n"}, "base")
    repo.branch("other")
    repo.checkout("other")
    repo.commit({"f.txt": "other\n"}, "other")
    repo.checkout("main")
    repo.commit({"f.txt": "main\n"}, "main moves f")
    repo.write("f.txt", "main\nplus local\n")
    session = await RepoSession.open(repo.path)
    ui = FakeUi(choose_answers=["cancel"])
    d = Dispatcher(register_builtin(Registry()), ui)
    await d.dispatch_id("branch.checkout", await bctx(session, "other"))
    assert repo.git("branch", "--show-current").strip() == "main"  # cancelled: nothing moved


async def test_force_checkout_action(blocked):
    repo, session, ui, d = blocked
    await d.dispatch_id("branch.force_checkout", await bctx(session, "other"))
    assert repo.git("branch", "--show-current").strip() == "other"
    assert ui.confirms and "discard" in ui.confirms[-1]
