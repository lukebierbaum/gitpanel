from __future__ import annotations

import pytest

from gitpanel.git import commit as C
from gitpanel.git import patch as P
from gitpanel.git.journal import UndoRefused
from gitpanel.git.runner import GitError
from gitpanel.git.session import RepoSession


@pytest.fixture
async def session(repo):
    return await RepoSession.open(repo.path)


async def test_commit_undo_redo_soft(repo, session):
    before = repo.head()
    repo.write("a.txt", "a\n")
    async with session.journal.mutation("Commit", reset_mode="soft"):
        await C.stage_paths(session.git, ["a.txt"])
        await C.commit(session.git, "add a")
    after = repo.head()
    assert after != before
    [e] = session.journal.entries()
    assert e.refs_before == {"refs/heads/main": before}
    assert e.refs_after == {"refs/heads/main": after}

    await session.journal.undo()
    assert repo.head() == before
    assert "a.txt" in repo.git("diff", "--cached", "--name-only")  # soft: changes stay staged

    await session.journal.redo()
    assert repo.head() == after
    assert repo.git("status", "--porcelain") == ""


async def test_amend_undo(repo, session):
    repo.commit({"f.txt": "1\n"}, "first")
    orig = repo.head()
    repo.write("f.txt", "2\n")
    async with session.journal.mutation("Amend", reset_mode="soft"):
        await C.stage_paths(session.git, ["f.txt"])
        await C.commit(session.git, "first amended", C.CommitOptions(amend=True))
    assert repo.head() != orig
    await session.journal.undo()
    assert repo.head() == orig
    assert repo.index_content("f.txt") == "2\n"


async def test_nested_mutations_collapse(repo, session):
    async with session.journal.mutation("outer"):
        async with session.journal.mutation("inner"):
            repo.write("x.txt", "x\n")
            await C.stage_paths(session.git, ["x.txt"])
            await C.commit(session.git, "x")
        await session.git.run("branch", "side")
    [e] = session.journal.entries()
    assert e.op == "outer"
    assert set(e.refs_after) == {"refs/heads/main", "refs/heads/side"}


async def test_failed_mutation_recorded_if_refs_moved(repo, session):
    with pytest.raises(GitError):
        async with session.journal.mutation("Branch then fail"):
            await session.git.run("branch", "made-it")
            await session.git.run("checkout", "does-not-exist")
    [e] = session.journal.entries()
    assert e.failed and e.op.endswith("(failed)")


async def test_noop_mutation_not_recorded(repo, session):
    async with session.journal.mutation("nothing"):
        pass
    assert session.journal.entries() == []


async def test_undo_refuses_when_ref_moved(repo, session):
    async with session.journal.mutation("Branch"):
        await session.git.run("branch", "b1")
    repo.git("branch", "-f", "b1", "HEAD")  # same sha, should still be fine
    repo.commit({"q.txt": "q\n"})
    repo.git("branch", "-f", "b1", "HEAD")  # moved
    with pytest.raises(UndoRefused, match="b1 has changed"):
        await session.journal.undo()


async def test_branch_delete_undo(repo, session):
    repo.branch("keep-me")
    sha = repo.git("rev-parse", "keep-me").strip()
    async with session.journal.mutation("Delete branch"):
        await session.git.run("branch", "-D", "keep-me")
    await session.journal.undo()
    assert repo.git("rev-parse", "keep-me").strip() == sha


async def test_checkout_undo(repo, session):
    repo.branch("other")
    async with session.journal.mutation("Checkout other"):
        await session.git.run("checkout", "-q", "other")
    await session.journal.undo()
    assert repo.git("branch", "--show-current").strip() == "main"


async def test_hard_reset_undo_restores_uncommitted(repo, session):
    repo.commit({"k.txt": "1\n"}, "c1")
    c1 = repo.head()
    repo.commit({"k.txt": "2\n"}, "c2")
    c2 = repo.head()
    repo.write("k.txt", "dirty\n")
    repo.write("untracked.txt", "u\n")
    async with session.journal.mutation("Reset hard", destructive=True, reset_mode="hard"):
        await session.git.run("reset", "--hard", "-q", c1)
        await session.git.run("clean", "-fdq")
    assert repo.read("k.txt") == "1\n"
    assert not (repo.path / "untracked.txt").exists()

    await session.journal.undo()
    assert repo.head() == c2
    assert repo.read("k.txt") == "dirty\n"
    assert repo.read("untracked.txt") == "u\n"

    await session.journal.redo()
    assert repo.head() == c1
    assert repo.read("k.txt") == "1\n"
    assert not (repo.path / "untracked.txt").exists()


async def test_discard_hunk_undo(repo, session):
    base = "".join(f"{i}\n" for i in range(30))
    repo.commit({"d.txt": base})
    changed = base.replace("2\n", "two\n", 1).replace("25\n", "twentyfive\n")
    repo.write("d.txt", changed)
    [fd] = await P.load_diff(session.git, P.WorkingTree())
    async with session.journal.mutation("Rollback hunk", destructive=True, paths=["d.txt"]):
        await P.apply(
            session.git,
            P.build_patch([fd], P.Selection.hunks(fd, [0]), reverse=True),
            to="worktree",
            reverse=True,
        )
    assert "two\n" not in repo.read("d.txt")
    await session.journal.undo()
    assert repo.read("d.txt") == changed


async def test_undo_refuses_pushed(repo, session):
    repo.bare_remote()
    repo.git("push", "-q", "-u", "origin", "main")
    async with session.journal.mutation("Commit", reset_mode="soft"):
        repo.write("p.txt", "p\n")
        await C.stage_paths(session.git, ["p.txt"])
        await C.commit(session.git, "p")
    repo.git("push", "-q")
    with pytest.raises(UndoRefused, match="already pushed"):
        await session.journal.undo()


async def test_new_mutation_drops_redo(repo, session):
    async with session.journal.mutation("b1"):
        await session.git.run("branch", "b1")
    await session.journal.undo()
    assert session.journal.can_redo() is not None
    async with session.journal.mutation("b2"):
        await session.git.run("branch", "b2")
    assert session.journal.can_redo() is None
    assert [e.op for e in session.journal.entries()] == ["b2"]


async def test_undo_refuses_branch_in_other_worktree(repo, session):
    async with session.journal.mutation("Create wt branch"):
        await session.git.run("branch", "wtb")
    repo.git("worktree", "add", "-q", str(repo.path.parent / "wtb"), "wtb")
    with pytest.raises(UndoRefused, match="another worktree"):
        await session.journal.undo()
