from __future__ import annotations

from gitpanel.git.models import ChangeType
from gitpanel.git.status import status


async def test_status_all_record_kinds(repo):
    repo.commit({"mod.txt": "a\n", "del.txt": "x\n", "ren me.txt": "r\n" * 20, "both.txt": "1\n"})
    repo.write("mod.txt", "b\n")
    repo.write("del.txt", None)
    repo.git("mv", "ren me.txt", "renamed\nnewline.txt")
    repo.write("new.txt", "n\n")
    repo.git("add", "new.txt")
    repo.write("both.txt", "2\n")
    repo.git("add", "both.txt")
    repo.write("both.txt", "3\n")
    repo.write("untracked dir/u.txt", "u\n")

    st = await status(repo.g)
    assert st.branch.branch == "main"
    assert st.branch.head == repo.head()
    by = {f.path: f for f in st.files}
    assert by["mod.txt"].unstaged is ChangeType.MODIFIED and not by["mod.txt"].has_staged
    assert by["del.txt"].unstaged is ChangeType.DELETED
    ren = by["renamed\nnewline.txt"]
    assert ren.staged is ChangeType.RENAMED and ren.orig_path == "ren me.txt"
    assert by["new.txt"].staged is ChangeType.ADDED
    assert by["both.txt"].has_staged and by["both.txt"].has_unstaged
    assert by["untracked dir/u.txt"].untracked


async def test_status_unmerged_and_upstream(repo):
    remote = repo.bare_remote()
    repo.git("push", "-q", "-u", "origin", "main")
    repo.commit({"x.txt": "1\n"})
    st = await status(repo.g)
    assert st.branch.upstream == "origin/main" and st.branch.ahead == 1 and st.branch.behind == 0
    assert remote.exists()

    repo.conflict()
    st = await status(repo.g)
    [u] = st.unmerged
    assert u.path == "conflict.txt" and u.conflict == "UU"


async def test_status_unborn(repo_factory):
    r = repo_factory()
    r.write("a.txt", "a\n")
    st = await status(r.g)
    assert st.branch.head is None and st.branch.branch == "main"
    assert st.files[0].untracked
