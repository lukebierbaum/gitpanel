from __future__ import annotations

import pytest

from gitpanel.git import cherry_pick as CP
from gitpanel.git import patch as P
from gitpanel.git.operations import OpKind
from gitpanel.git.session import RepoSession


@pytest.fixture
async def setup(repo):
    """main + feature with three commits: a (new file), b (edits shared.txt), c (new file)."""
    repo.commit({"shared.txt": "base\n"}, "base")
    repo.branch("feature")
    repo.checkout("feature")
    a = repo.commit({"a.txt": "a\n"}, "feature a")
    b = repo.commit({"shared.txt": "feature\n"}, "feature b")
    c = repo.commit({"c.txt": "c\n"}, "feature c")
    repo.checkout("main")
    session = await RepoSession.open(repo.path)
    return repo, session, (a, b, c)


def subjects(repo, n):
    return repo.git("log", f"-{n}", "--format=%s").split("\n")[:n]


async def test_pick_in_order_with_origin(setup):
    repo, session, (a, b, c) = setup
    res = await CP.cherry_pick(session, [c, a], record_origin=True)  # log order: newest first
    assert res.status == "done"
    assert subjects(repo, 2) == ["feature c", "feature a"]
    assert f"(cherry picked from commit {c})" in repo.git("log", "-1", "--format=%B")


async def test_already_applied_is_dropped(setup):
    repo, session, (a, b, c) = setup
    await CP.cherry_pick(session, [a])
    head = repo.head()
    res = await CP.cherry_pick(session, [a])
    assert res.status == "done" and repo.head() == head


async def test_conflict_progress_and_continue(setup):
    repo, session, (a, b, c) = setup
    repo.commit({"shared.txt": "main\n"}, "main edit")
    res = await CP.cherry_pick(session, [c, b, a])
    assert res.status == "conflict"
    st = res.state
    assert st.kind is OpKind.CHERRY_PICK and (st.step, st.total) == (2, 3)
    (repo.path / "shared.txt").write_text("resolved\n")
    repo.git("add", "shared.txt")
    repo.git("-c", "core.editor=true", "cherry-pick", "--continue")
    assert subjects(repo, 3) == ["feature c", "feature b", "feature a"]


async def test_picked_detection_patch_id_and_trailer(setup):
    repo, session, (a, b, c) = setup
    await CP.cherry_pick(session, [a])  # patch-id equivalent
    await CP.cherry_pick(session, [c], record_origin=True)
    repo.git("commit", "--amend", "-q", "-m", "reworded c\n\n(cherry picked from commit " + c + ")")
    picked = await CP.picked_shas(session.git, [a, b, c])
    assert picked == {a, c}
    cmp = await CP.compare(session.git, "HEAD", "feature")
    marks = {commit.subject: p for commit, p in cmp.only_b}
    assert marks == {"feature a": True, "feature b": False, "feature c": True}
    assert [commit.subject for commit, _ in cmp.only_a] == ["reworded c", "feature a"]


async def test_pick_onto_branch_not_checked_out(setup):
    repo, session, (a, b, c) = setup
    repo.branch("release")
    repo.write("dirty.txt", "local work\n")
    main_head = repo.head()
    async with session.journal.mutation("Pick to release"):
        res = await CP.pick_onto_branch(session, "release", [a, c])
    assert res.status == "done"
    assert repo.head() == main_head and repo.read("dirty.txt") == "local work\n"
    assert repo.git("log", "-2", "--format=%s", "release").split("\n")[:2] == [
        "feature c",
        "feature a",
    ]
    assert len(await session.worktrees()) == 1  # temp worktree cleaned up
    await session.journal.undo()
    assert repo.git("rev-parse", "release").strip() == main_head


async def test_pick_onto_branch_in_other_worktree(setup):
    repo, session, (a, b, c) = setup
    wt = repo.worktree("rel", "release")
    res = await CP.pick_onto_branch(session, "release", [a])
    assert res.status == "done" and res.worktree.resolve() == wt.resolve()
    assert (wt / "a.txt").exists()


async def test_pick_onto_branch_conflict_keeps_worktree(setup):
    repo, session, (a, b, c) = setup
    repo.branch("release")
    repo.checkout("release")
    repo.commit({"shared.txt": "release\n"}, "release edit")
    repo.checkout("main")
    res = await CP.pick_onto_branch(session, "release", [b])
    assert res.status == "conflict" and res.worktree is not None and res.worktree.exists()
    with pytest.raises(CP.PickError):
        await CP.pick_onto_branch(session, "release", [b])


async def test_apply_hunk_and_get_file(setup):
    repo, session, (a, b, c) = setup
    base = "".join(f"{i}\n" for i in range(30))
    repo.checkout("feature")
    repo.commit({"big.txt": base}, "big")
    changed = base.replace("2\n", "two\n", 1).replace("25\n", "twentyfive\n")
    sha = repo.commit({"big.txt": changed}, "two changes")
    repo.checkout("main")
    repo.commit({"big.txt": base}, "big on main")
    [fd] = await P.load_diff(session.git, P.CommitDiff(sha))
    await CP.apply_from_commit(session.git, sha, P.Selection.hunks(fd, [1]))
    content = repo.read("big.txt")
    assert "twentyfive" in content and "two\n" not in content
    await CP.get_file_from_commit(session.git, a, ["a.txt"])
    assert repo.read("a.txt") == "a\n"
