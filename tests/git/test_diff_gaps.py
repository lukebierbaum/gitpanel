from __future__ import annotations

from gitpanel.git import patch as P
from gitpanel.git.session import RepoSession

LINES = [f"line {i}" for i in range(1, 41)]


async def test_gaps_and_unchanged_text(repo):
    repo.commit({"f.txt": "\n".join(LINES) + "\n"}, "base")
    changed = list(LINES)
    changed[4] = "FIVE"  # line 5
    changed.insert(30, "inserted")  # new line 31
    (repo.path / "f.txt").write_text("\n".join(changed) + "\n")
    session = await RepoSession.open(repo.path)
    [diff] = await P.load_diff(session.git, P.WorkingTree())
    text = await P.unchanged_text(session.git, P.WorkingTree(), diff)
    assert text is not None and len(text) == 41 and text[30] == "inserted"
    first, second, end = P.gaps(diff, len(text))
    assert first == (1, 1, 0)  # line 1, before the 3 lines of context around line 5
    a, b, offset = second
    assert (a, b) == (9, 27) and offset == 0  # between the two hunks
    a, b, offset = end
    assert (a, b) == (35, 41) and offset == -1  # after the insertion: old = new - 1
    assert text[end[0] - 1] == "line 34"
    # added files have nothing hidden
    (repo.path / "new.txt").write_text("x\n")
    [*_, new] = await P.load_diff(session.git, P.WorkingTree(), include_untracked=["new.txt"])
    assert await P.unchanged_text(session.git, P.WorkingTree(), new) is None
