from __future__ import annotations

import asyncio
import shutil

import pytest

from gitpanel.git.runner import Git, GitError
from gitpanel.git.session import ChangeKind, RepoSession, WorktreeGone


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


async def test_console_closes_entries_for_cancelled_calls(repo):
    import asyncio
    import contextlib

    from gitpanel.git.console import CONSOLE

    CONSOLE.clear()
    task = asyncio.create_task(repo.g.run("log", "--all", read_only=True))
    await asyncio.sleep(0)  # let it start, then cancel mid-flight
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
    assert CONSOLE.entries and not any(e.running for e in CONSOLE.entries)


# -- worktree folder / repository moved or deleted while open ------------------------------


async def test_refresh_active_falls_back_when_worktree_folder_is_deleted(repo):
    wt_path = repo.worktree("gone", "gone")
    session = await RepoSession.open(wt_path)
    seen: list[ChangeKind] = []
    session.subscribe(seen.append)
    shutil.rmtree(wt_path)
    active = await session.refresh_active()
    assert active.is_main and active.path.resolve() == repo.path.resolve()
    assert session.git.cwd.resolve() == repo.path.resolve()
    assert session.git_dir.resolve() == (repo.path / ".git").resolve()
    assert seen == [ChangeKind.ALL]  # like switch(): every view reloads


async def test_refresh_active_raises_when_the_repository_moved(repo, tmp_path):
    session = await RepoSession.open(repo.path)
    repo.path.rename(tmp_path / "moved")
    with pytest.raises(WorktreeGone, match="no longer exists"):
        await session.refresh_active()
    session.private_dir()
    session.private_dir(shared=True)
    assert not repo.path.exists()  # nothing re-created at the old place
    # the watcher keeps going on vanished files
    assert session.poll_changes()
    assert not session.poll_changes()
    session.start_watching(interval=0.01)
    await asyncio.sleep(0.05)
    assert session._watch_task is not None and not session._watch_task.done()
    session.stop_watching()


async def test_refresh_active_in_linked_worktree_of_a_moved_repository(repo, tmp_path):
    wt_path = repo.worktree("linked", "linked")
    session = await RepoSession.open(wt_path)
    repo.path.rename(tmp_path / "moved")  # the worktree's .git file now points nowhere
    with pytest.raises(WorktreeGone, match="git worktree repair"):
        await session.refresh_active()


async def test_git_in_a_missing_folder_fails_with_git_error(tmp_path):
    git = Git(tmp_path / "nowhere")
    with pytest.raises(GitError, match="cannot change to"):
        await git.run("status", read_only=True)
    with pytest.raises(GitError, match="cannot change to"):
        _ = [r async for r in git.stream("log")]
