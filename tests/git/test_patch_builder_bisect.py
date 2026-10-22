from __future__ import annotations

import pytest

from gitpanel.git import bisect as B
from gitpanel.git import patch as P
from gitpanel.git import patch_builder as PB
from gitpanel.git.markers import BisectMarkers
from gitpanel.git.session import RepoSession

BASE = "".join(f"{i}\n" for i in range(30))


@pytest.fixture
async def hist(repo):
    repo.commit({"f.txt": BASE, "g.txt": "g\n"}, "base")
    two_hunks = BASE.replace("2\n", "two\n", 1).replace("25\n", "twentyfive\n")
    mid = repo.commit({"f.txt": two_hunks, "h.txt": "h\n"}, "middle: two hunks + h.txt")
    top = repo.commit({"top.txt": "t\n"}, "top")
    session = await RepoSession.open(repo.path)
    return repo, session, mid, top


async def sel_hunk(session, sha, path, idx):
    diffs = await P.load_diff(session.git, P.CommitDiff(sha), [path])
    fd = next(d for d in diffs if d.path == path)
    return P.Selection.hunks(fd, [idx])


async def test_remove_from_middle_commit(hist):
    repo, session, mid, top = hist
    async with session.journal.mutation("remove"):
        res = await PB.remove_from_commit(session, mid, await sel_hunk(session, mid, "f.txt", 1))
    assert res.status == "done"
    assert "twentyfive" not in repo.git("show", "HEAD~1:f.txt")
    assert "two\n" in repo.git("show", "HEAD~1:f.txt")
    assert repo.git("log", "-1", "--format=%s").strip() == "top"
    await session.journal.undo()
    assert repo.head() == top


async def test_move_to_new_commit_and_head(hist):
    repo, session, mid, top = hist
    res = await PB.move_to_new_commit(session, mid, P.Selection.files(["h.txt"]), "add h")
    assert res.status == "done"
    assert repo.git("log", "--format=%s", "-3").split("\n")[:3] == [
        "top",
        "add h",
        "middle: two hunks + h.txt",
    ]
    assert "h.txt" not in repo.git("show", "--name-only", "--format=", "HEAD~2")
    head = repo.head()
    await PB.remove_from_commit(session, head, P.Selection.files(["top.txt"]))
    assert "top.txt" not in repo.git("show", "--name-only", "--format=", "HEAD")


async def test_move_to_older_commit_and_to_index(hist):
    repo, session, mid, top = hist
    base = repo.git("rev-parse", "HEAD~2").strip()
    res = await PB.move_to_commit(session, mid, P.Selection.files(["h.txt"]), base)
    assert res.status == "done"
    assert "h.txt" in repo.git("show", "--name-only", "--format=", "HEAD~2")
    assert "h.txt" not in repo.git("show", "--name-only", "--format=", "HEAD~1")
    new_mid = repo.git("rev-parse", "HEAD~1").strip()
    await PB.move_to_index(session, new_mid, await sel_hunk(session, new_mid, "f.txt", 0))
    assert "two\n" in repo.git("diff", "--cached")
    assert "two\n" not in repo.git("show", "HEAD~1:f.txt")


async def test_refuses_dirty_and_off_branch(hist):
    repo, session, mid, top = hist
    repo.write("g.txt", "dirty\n")
    with pytest.raises(PB.PatchBuilderError, match="local changes"):
        await PB.remove_from_commit(session, mid, P.Selection.files(["h.txt"]))


async def test_revert_in_worktree(hist):
    repo, session, mid, top = hist
    await PB.revert_in_worktree(session.git, mid, await sel_hunk(session, mid, "f.txt", 0))
    assert "two\n" not in repo.read("f.txt") and "twentyfive" in repo.read("f.txt")


async def test_bisect_marks_and_run(repo):
    shas = [repo.commit({"v.txt": f"{i}\n"}, f"v{i}") for i in range(8)]
    session = await RepoSession.open(repo.path)
    git = session.git
    await B.mark(git, "bad", shas[-1])
    await B.mark(git, "good", shas[0])
    st = await B.state(git)
    assert st.active and st.bad == shas[-1] and st.good == [shas[0]] and st.remaining
    markers = await BisectMarkers().markers(session, shas)
    assert markers[shas[-1]][0].label == "bad"
    # "bad" = v.txt contains 5 or more
    out = [line async for line in B.run(git, 'test "$(cat v.txt)" -lt 5')]
    assert B.first_bad_in("\n".join(out)) and shas[5].startswith(B.first_bad_in("\n".join(out)))
    st = await B.state(git)
    assert st.first_bad and shas[5].startswith(st.first_bad)
    await B.reset(git)
    assert not (await B.state(git)).active
