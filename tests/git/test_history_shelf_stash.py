from __future__ import annotations

import pytest

from gitpanel.git import history as H
from gitpanel.git import shelf as SH
from gitpanel.git import stash as ST
from gitpanel.git.changelists import DEFAULT, Changelists
from gitpanel.git.session import RepoSession


async def test_file_history_follows_rename(repo):
    repo.commit({"old.txt": "".join(f"line {i}\n" for i in range(20))}, "add old")
    repo.commit({"old.txt": "".join(f"line {i}\n" for i in range(21))}, "grow old")
    repo.git("mv", "old.txt", "new.txt")
    repo.git("commit", "-qm", "rename")
    repo.commit({"new.txt": "".join(f"line {i}\n" for i in range(22))}, "grow new")
    revs = await H.file_history(repo.g, "new.txt")
    assert [r.commit.subject for r in revs] == ["grow new", "rename", "grow old", "add old"]
    assert revs[1].status == "R" and revs[1].old_path == "old.txt"
    assert revs[-1].path == "old.txt"


async def test_selection_history_and_blame(repo):
    repo.commit({"f.txt": "a\nb\nc\nd\n"}, "one")
    repo.commit({"f.txt": "a\nB\nc\nd\n"}, "two")
    repo.commit({"f.txt": "a\nB\nc\nD\n"}, "three")
    hist = await H.selection_history(repo.g, "f.txt", 2, 2)
    assert [c.subject for c in hist] == ["two", "one"]
    repo.write("f.txt", "a\nB\nc\nD\nlocal\n")
    lines = await H.blame(repo.g, "f.txt")
    assert [ln.summary for ln in lines[:4]] == ["one", "two", "one", "three"]
    assert lines[1].text == "B" and lines[1].line == 2
    assert lines[4].uncommitted
    at_one = await H.blame(repo.g, "f.txt", "HEAD~2")
    assert {ln.summary for ln in at_one} == {"one"}


async def test_stash_roundtrip(repo):
    repo.commit({"f.txt": "1\n"})
    repo.write("f.txt", "2\n")
    repo.git("add", "f.txt")
    repo.write("u.txt", "u\n")
    await ST.push(repo.g, "wip", include_untracked=True)
    assert repo.git("status", "--porcelain") == ""
    [s] = await ST.stashes(repo.g)
    assert s.index == 0 and "wip" in s.message
    res = await ST.apply(repo.g, s.ref, restore_index=True)
    assert res.ok and repo.read("u.txt") == "u\n"
    assert "f.txt" in repo.git("diff", "--cached", "--name-only")
    await ST.drop(repo.g, s.ref)
    assert await ST.stashes(repo.g) == []
    await ST.push(repo.g, "again", include_untracked=True)
    await ST.branch(repo.g, "from-stash", "stash@{0}")
    assert repo.git("branch", "--show-current").strip() == "from-stash"


async def test_shelve_and_unshelve(repo):
    repo.commit({"a.txt": "a\n", "b.txt": "b\n"})
    session = await RepoSession.open(repo.path)
    repo.write("a.txt", "a changed\n")
    repo.write("b.txt", "b changed\n")
    repo.write("new.txt", "new\n")
    shelf = await SH.shelve(session, "experiment", ["a.txt", "new.txt"])
    assert set(shelf.paths) == {"a.txt", "new.txt"}
    assert repo.read("a.txt") == "a\n" and not (repo.path / "new.txt").exists()
    assert repo.read("b.txt") == "b changed\n"  # not shelved
    [listed] = SH.list_shelves(session)
    assert listed.name == "experiment"
    await SH.unshelve(session, listed, ["new.txt"])
    assert repo.read("new.txt") == "new\n" and repo.read("a.txt") == "a\n"
    assert SH.list_shelves(session)  # partial unshelve keeps the shelf
    repo.write("new.txt", None)
    await SH.unshelve(session, listed)
    assert repo.read("a.txt") == "a changed\n" and repo.read("new.txt") == "new\n"
    assert SH.list_shelves(session) == []
    with pytest.raises(ValueError):
        await SH.shelve(session, "nothing", ["README.md"])


def test_changelists(tmp_path):
    f = tmp_path / "cl.json"
    cl = Changelists.load(f)
    assert cl.lists == [DEFAULT] and cl.active == DEFAULT
    cl.add("Feature")
    cl.assign(["a.py"], "Feature")
    cl.save()
    cl = Changelists.load(f)
    assert cl.group(["a.py", "b.py"]) == {DEFAULT: ["b.py"], "Feature": ["a.py"]}
    cl.set_active("Feature", changed=["b.py"])
    assert cl.group(["a.py", "b.py", "c.py"]) == {DEFAULT: ["b.py"], "Feature": ["a.py", "c.py"]}
    cl.rename("Feature", "Feat")
    assert cl.active == "Feat"
    cl.remove("Feat")
    assert cl.active == DEFAULT and cl.list_for("a.py") == DEFAULT
    with pytest.raises(ValueError):
        cl.remove(DEFAULT)
