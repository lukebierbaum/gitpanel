from __future__ import annotations

from gitpanel.git.session import ChangeKind, RepoSession


async def test_open_from_linked_worktree_and_switch(repo):
    wt_path = repo.worktree("feature", "feature")
    session = await RepoSession.open(wt_path)
    assert session.common_dir.resolve() == (repo.path / ".git").resolve()
    assert session.active.path.resolve() == wt_path.resolve()
    assert session.active.branch == "refs/heads/feature"
    assert not session.active.is_main

    wts = await session.worktrees()
    assert [w.is_main for w in wts] == [True, False]
    assert wts[0].path.resolve() == repo.path.resolve()

    seen: list[ChangeKind] = []
    session.subscribe(seen.append)
    await session.switch(repo.path)
    assert session.active.is_main
    assert session.git.cwd.resolve() == repo.path.resolve()
    assert seen == [ChangeKind.ALL]
    assert session.private_dir().parent.resolve() == (repo.path / ".git").resolve()


async def test_private_dir_per_worktree(repo):
    wt_path = repo.worktree("w2", "w2")
    session = await RepoSession.open(wt_path)
    assert "worktrees" in str(session.private_dir())
    assert session.private_dir(shared=True).parent.resolve() == (repo.path / ".git").resolve()


async def test_poll_detects_external_commit(repo):
    session = await RepoSession.open(repo.path)
    assert not session.poll_changes()
    repo.commit({"z.txt": "z\n"})
    changed = session.poll_changes()
    assert changed & ChangeKind.REFS or changed & ChangeKind.HEAD
    assert changed & ChangeKind.INDEX
