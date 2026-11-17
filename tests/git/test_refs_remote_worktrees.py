from __future__ import annotations

import shutil

import pytest

from gitpanel.git import refs as R
from gitpanel.git import remote as RM
from gitpanel.git import tags as T
from gitpanel.git import worktrees as W
from gitpanel.git.runner import Git, GitError
from gitpanel.git.session import RepoSession


@pytest.fixture
def cloned(repo, repo_factory, tmp_path):
    """repo pushed to a bare origin, plus a second clone that can push to it."""
    origin = repo.bare_remote()
    repo.git("push", "-q", "-u", "origin", "main")
    other = tmp_path / "other"
    repo.git("clone", "-q", str(origin), str(other))
    return repo, origin, other


async def test_branches_ahead_behind_and_worktree(cloned):
    repo, origin, other = cloned
    import subprocess

    subprocess.run(
        ["git", "-C", str(other), "commit", "-q", "--allow-empty", "-m", "remote work"], check=True
    )
    subprocess.run(["git", "-C", str(other), "push", "-q"], check=True)
    repo.commit({"l.txt": "l\n"}, "local work")
    await RM.fetch(repo.g)
    bs = {b.short: b for b in await R.branches(repo.g)}
    assert bs["main"].ahead == 1 and bs["main"].behind == 1
    assert bs["main"].upstream_short == "origin/main"
    assert bs["origin/main"].is_remote and bs["origin/main"].remote == "origin"
    assert bs["main"].worktree is not None


async def test_recent_and_favorites(repo, tmp_path):
    repo.branch("a")
    repo.branch("b")
    repo.checkout("a")
    repo.checkout("b")
    repo.checkout("main")
    assert (await R.recent_branches(repo.g))[:3] == ["main", "b", "a"]
    fav = R.Favorites(tmp_path / "fav.json")
    assert "refs/heads/main" in fav.load()
    assert fav.toggle("refs/heads/a") is True
    assert "refs/heads/a" in R.Favorites(tmp_path / "fav.json").load()
    assert fav.toggle("refs/heads/a") is False


async def test_checkout_remote_creates_tracking(cloned):
    repo, origin, other = cloned
    import subprocess

    subprocess.run(["git", "-C", str(other), "switch", "-q", "-c", "feature"], check=True)
    subprocess.run(
        ["git", "-C", str(other), "commit", "-q", "--allow-empty", "-m", "f"], check=True
    )
    subprocess.run(["git", "-C", str(other), "push", "-q", "-u", "origin", "feature"], check=True)
    await RM.fetch(repo.g)
    local = await R.checkout_remote(repo.g, "refs/remotes/origin/feature")
    assert local == "feature"
    assert repo.git("rev-parse", "--abbrev-ref", "feature@{u}").strip() == "origin/feature"


async def test_delete_unmerged_requires_force(repo):
    repo.branch("wip")
    repo.checkout("wip")
    repo.commit({"w.txt": "w\n"})
    repo.checkout("main")
    with pytest.raises(GitError):
        await R.delete(repo.g, "wip")
    assert not await R.is_merged(repo.g, "wip")
    await R.delete(repo.g, "wip", force=True)
    assert "wip" not in repo.git("branch")


async def test_pull_push_outgoing_force_with_lease(cloned):
    repo, origin, other = cloned
    import subprocess

    repo.commit({"p.txt": "1\n"}, "to push")
    out = await RM.outgoing(repo.g, "main", "origin/main")
    assert [c.subject for c in out] == ["to push"]
    await RM.push(repo.g, "main", "origin")
    assert await RM.outgoing(repo.g, "main", "origin/main") == []
    # rewrite and force-with-lease
    repo.git("commit", "--amend", "-q", "-m", "to push (amended)")
    with pytest.raises(GitError):
        await RM.push(repo.g, "main", "origin")
    await RM.push(repo.g, "main", "origin", force_with_lease=True)
    # other clone pulls with rebase
    subprocess.run(
        ["git", "-C", str(other), "commit", "-q", "--allow-empty", "-m", "theirs"], check=True
    )
    res = await RM.pull(RM.Git(other), "rebase")
    assert res.ok, res.stderr
    log = subprocess.run(
        ["git", "-C", str(other), "log", "--format=%s", "-2"], capture_output=True, text=True
    ).stdout.split("\n")
    assert log[:2] == ["theirs", "to push (amended)"]


async def test_push_new_branch_set_upstream(cloned):
    repo, *_ = cloned
    repo.branch("topic")
    await RM.push(repo.g, "topic", "origin", set_upstream=True)
    assert repo.git("rev-parse", "--abbrev-ref", "topic@{u}").strip() == "origin/topic"
    await RM.delete_remote_branch(repo.g, "origin", "topic")
    assert "origin/topic" not in repo.git("branch", "-r")


async def test_auth_error_detection(repo):
    repo.git("remote", "add", "nope", "https://127.0.0.1:1/x.git")
    with pytest.raises(GitError):
        await RM.fetch(repo.g, "nope")
    err = GitError(
        RM.GitResult(
            ("fetch",),
            128,
            b"",
            "fatal: could not read Username for 'https://x': terminal prompts disabled",
        )
    )
    assert RM.is_auth_error(err)


async def test_tags(cloned):
    repo, *_ = cloned
    sha = repo.head()
    await T.create(repo.g, "v1", sha)
    await T.create(repo.g, "v2", sha, "release two")
    assert {t.short for t in await T.tags(repo.g)} == {"v1", "v2"}
    assert all(t.sha == sha for t in await T.tags(repo.g))
    await T.push_tag(repo.g, "origin", "v2")
    assert "v2" in repo.git("ls-remote", "--tags", "origin")
    await T.delete_remote_tag(repo.g, "origin", "v2")
    await T.delete(repo.g, "v1")
    assert {t.short for t in await T.tags(repo.g)} == {"v2"}


async def test_worktree_lifecycle(repo, tmp_path):
    session = await RepoSession.open(repo.path)
    git = session.git
    p1 = W.default_path(repo.path, "feature/x y")
    assert p1.name == "feature-x-y" and p1.parent.name == "repo.worktrees"
    await W.add(git, p1, new_branch="feature/x", start="HEAD")
    p2 = tmp_path / "wt-detached"
    await W.add(git, p2, detach=repo.head())
    repo.branch("existing")
    p3 = tmp_path / "wt-existing"
    await W.add(git, p3, branch="existing")
    wts = await session.worktrees()
    assert len(wts) == 4
    assert W.for_branch(wts, "existing").path.resolve() == p3.resolve()

    (p1 / "new.txt").write_text("n\n")
    (p1 / "README.md").write_text("changed\n")
    s = await W.summary(W.for_branch(wts, "feature/x"))
    assert (s.untracked, s.unstaged, s.dirty) == (1, 1, True)

    await W.lock(git, p2, "on a usb stick")
    assert next(w for w in await session.worktrees() if w.path.resolve() == p2.resolve()).locked
    await W.unlock(git, p2)
    p2b = tmp_path / "wt-moved"
    await W.move(git, p2, p2b)
    with pytest.raises(GitError):
        await W.remove(git, p1)  # dirty
    await W.remove(git, p1, force=True)
    shutil.rmtree(p3)
    assert any(w.prunable is not None for w in await session.worktrees())
    await W.prune(git)
    assert len(await session.worktrees()) == 2


async def test_summary_flags_worktree_whose_repository_moved(repo, tmp_path):
    wt_path = repo.worktree("feat", "feat")
    session = await RepoSession.open(repo.path)
    wt = next(w for w in await session.worktrees() if not w.is_main)
    moved = tmp_path / "moved"
    repo.path.rename(moved)  # e.g. ~/hmnd/ubuntu_config → ~/code/thedotfiles
    s = await W.summary(wt)
    assert s.broken is not None and "gitfile" in s.broken
    assert not s.missing and not s.dirty
    await W.repair(Git(moved))  # from the repository's new place
    assert (await W.summary(wt)).broken is None
    assert wt_path.exists()
