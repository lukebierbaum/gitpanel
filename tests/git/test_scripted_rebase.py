from __future__ import annotations

import pytest

from gitpanel.git import scripted_rebase as SR
from gitpanel.git.session import RepoSession


@pytest.fixture
async def history(repo):
    shas = [repo.commit({f"f{i}.txt": f"{i}\n"}, f"commit {i}") for i in range(1, 4)]
    session = await RepoSession.open(repo.path)
    return repo, session, shas


def subjects(repo, n=4):
    return repo.git("log", f"-{n}", "--format=%s").split("\n")[:n]


async def test_reword_middle_commit(history):
    repo, session, (c1, c2, c3) = history
    base = repo.git("rev-parse", f"{c2}^").strip()
    todo = await SR.todo_for(session.git, base)
    assert [t.sha for t in todo] == [c2, c3]
    todo[0].action = "reword"
    async with session.journal.mutation("Reword"):
        res = await SR.run(session, base, todo, messages={c2: "commit 2, reworded\n\nbody"})
    assert res.status == "done"
    assert subjects(repo, 3) == ["commit 3", "commit 2, reworded", "commit 1"]
    assert repo.git("rev-parse", "HEAD~2").strip() == c1  # untouched below
    assert repo.git("log", "-1", "--format=%b", "HEAD~1").strip() == "body"
    await session.journal.undo()
    assert repo.head() == c3


async def test_edit_stop_callback_amends(history):
    repo, session, (c1, c2, c3) = history
    base = c1
    todo = await SR.todo_for(session.git, base)
    todo[0].action = "edit"
    seen = []

    async def on_stop(stop: SR.StopInfo) -> None:
        seen.append(stop.item.sha)
        (repo.path / "extra.txt").write_text("x\n")
        await session.git.run("add", "extra.txt")
        await session.git.run("commit", "--amend", "--no-edit", "-q")

    res = await SR.run(session, base, todo, on_stop=on_stop)
    assert res.status == "done" and seen == [c2]
    assert "extra.txt" in repo.git("show", "--name-only", "--format=", "HEAD~1")


async def test_drop_and_reorder(history):
    repo, session, (c1, c2, c3) = history
    todo = await SR.todo_for(session.git, c1)
    todo.reverse()
    todo[1].action = "drop"
    res = await SR.run(session, c1, todo)
    assert res.status == "done"
    assert subjects(repo, 2) == ["commit 3", "commit 1"]


async def test_conflict_then_resume(repo):
    repo.commit({"f.txt": "base\n"}, "base")
    base = repo.head()
    repo.commit({"f.txt": "one\n"}, "one")
    repo.commit({"f.txt": "two\n"}, "two")
    session = await RepoSession.open(repo.path)
    todo = await SR.todo_for(session.git, base)
    todo.reverse()  # applying "two" before "one" conflicts
    res = await SR.run(session, base, todo)
    assert res.status == "conflict" and res.state is not None
    (repo.path / "f.txt").write_text("resolved\n")
    repo.git("add", "f.txt")
    res = await SR.resume(session)
    if res.status == "conflict":  # second commit conflicts too
        (repo.path / "f.txt").write_text("resolved again\n")
        repo.git("add", "f.txt")
        res = await SR.resume(session)
    assert res.status == "done"


async def test_rejects_merges(repo):
    base = repo.head()
    repo.branch("side")
    repo.checkout("side")
    repo.commit({"s.txt": "s\n"})
    repo.checkout("main")
    repo.commit({"m.txt": "m\n"})
    repo.merge("side")
    session = await RepoSession.open(repo.path)
    with pytest.raises(SR.RebaseError):
        await SR.todo_for(session.git, base)
