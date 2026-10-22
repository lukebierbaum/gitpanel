from __future__ import annotations

import contextlib
from datetime import timedelta

from gitpanel.git import commit as C
from gitpanel.git.journal import UndoRefused
from gitpanel.git.session import RepoSession


async def three_commits(repo, session):
    shas = []
    for i in range(3):
        repo.write(f"j{i}.txt", f"{i}\n")
        async with session.journal.mutation(f"Commit {i}", reset_mode="soft"):
            await C.stage_paths(session.git, [f"j{i}.txt"])
            await C.commit(session.git, f"c{i}")
        shas.append(repo.head())
    return shas


async def test_undo_to_and_redo_to(repo):
    session = await RepoSession.open(repo.path)
    base = repo.head()
    shas = await three_commits(repo, session)
    first = session.journal.entries()[0]
    done = await session.journal.undo_to(first.id)
    assert len(done) == 3 and repo.head() == base
    done = await session.journal.redo_to(session.journal.entries()[1].id)
    assert len(done) == 2 and repo.head() == shas[1]


async def test_undo_to_stops_on_refusal(repo):
    session = await RepoSession.open(repo.path)
    shas = await three_commits(repo, session)
    repo.git("branch", "-f", "elsewhere")  # unrelated; fine
    repo.commit({"external.txt": "x\n"}, "external commit")  # moves main behind gitpanel's back
    first = session.journal.entries()[0]
    with contextlib.suppress(UndoRefused):
        await session.journal.undo_to(first.id)
    assert repo.git("log", "-1", "--format=%s").strip() == "external commit"
    assert all(not e.undone for e in session.journal.entries())
    assert shas


async def test_prune_drops_backup_refs(repo):
    session = await RepoSession.open(repo.path)
    repo.write("README.md", "dirty\n")
    async with session.journal.mutation("discard", destructive=True):
        await C.rollback(session.git, ["README.md"])
    assert "refs/gitpanel/backup/1" in repo.git("for-each-ref", "refs/gitpanel")
    await session.journal.prune(keep=0, max_age=timedelta(days=30))
    assert session.journal.entries() == []
    assert repo.git("for-each-ref", "refs/gitpanel").strip() == ""
