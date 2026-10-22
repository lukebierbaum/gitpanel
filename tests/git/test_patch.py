from __future__ import annotations

import pytest

from gitpanel.git import patch as P

THREE_HUNKS_BASE = "".join(f"line {i}\n" for i in range(1, 41))


def three_hunk_change() -> str:
    lines = THREE_HUNKS_BASE.splitlines(keepends=True)
    lines[2] = "line 3 changed\n"
    lines[19] = "line 20 changed\n"
    lines.insert(35, "inserted\n")
    return "".join(lines)


async def test_roundtrip_matches_git_diff(repo):
    repo.commit(
        {
            "a.txt": THREE_HUNKS_BASE,
            "noeol.txt": "x\ny",
            "crlf.txt": "one\r\ntwo\r\n",
            "uni ✓ space.txt": "α\n",
            "old_name.txt": "".join(f"r{i}\n" for i in range(30)),
            "gone.txt": "bye\n",
            "script.sh": "echo hi\n",
            "bin.dat": b"\x00\x01\x02",
        }
    )
    repo.write("a.txt", three_hunk_change())
    repo.write("noeol.txt", "x\nz")
    repo.write("crlf.txt", "one\r\nTWO\r\n")
    repo.write("uni ✓ space.txt", "β\n")
    repo.write("gone.txt", None)
    repo.write("new.txt", "fresh\n")
    repo.write("bin.dat", b"\x00\x09\x09")
    repo.git("mv", "old_name.txt", "new_name.txt")
    (repo.path / "script.sh").chmod(0o755)
    repo.git("add", "-A")

    raw = (await repo.g.run("diff", "--cached", *P.DIFF_OPTS, "-U3")).stdout
    diffs = P.parse_diff(raw)
    by_path = {d.path: d for d in diffs}
    assert by_path["new_name.txt"].status == "R"
    assert by_path["new_name.txt"].old_path == "old_name.txt"
    assert by_path["gone.txt"].status == "D"
    assert by_path["new.txt"].status == "A"
    assert by_path["bin.dat"].binary
    assert by_path["uni ✓ space.txt"].status == "M"
    assert len(by_path["a.txt"].hunks) == 3
    assert not by_path["script.sh"].partial_allowed

    rebuilt = P.build_patch(diffs, P.Selection.files(d.path for d in diffs))
    assert rebuilt == raw


async def test_stage_one_hunk_of_three(repo):
    repo.commit({"a.txt": THREE_HUNKS_BASE})
    repo.write("a.txt", three_hunk_change())
    [fd] = await P.load_diff(repo.g, P.WorkingTree())
    assert len(fd.hunks) == 3
    patch = P.build_patch([fd], P.Selection.hunks(fd, [1]))
    await P.apply(repo.g, patch, to="index")
    staged = repo.staged_diff()
    assert "+line 20 changed" in staged
    assert "line 3 changed" not in staged
    assert "inserted" not in staged
    unstaged = repo.unstaged_diff()
    assert "+line 3 changed" in unstaged and "+inserted" in unstaged


async def test_stage_last_hunk_only_with_offset(repo):
    repo.commit({"a.txt": THREE_HUNKS_BASE})
    lines = THREE_HUNKS_BASE.splitlines(keepends=True)
    lines[1:3] = []  # delete 2 lines in hunk 0
    lines.insert(30, "added\n")
    repo.write("a.txt", "".join(lines))
    [fd] = await P.load_diff(repo.g, P.WorkingTree())
    patch = P.build_patch([fd], P.Selection.hunks(fd, [len(fd.hunks) - 1]))
    await P.apply(repo.g, patch, to="index")
    expected = THREE_HUNKS_BASE.splitlines(keepends=True)
    expected.insert(32, "added\n")
    assert repo.index_content("a.txt") == "".join(expected)


async def test_stage_single_plus_and_minus_lines(repo):
    repo.commit({"f.txt": "a\nb\nc\nd\n"})
    repo.write("f.txt", "a\nB\nC\nd\n")
    [fd] = await P.load_diff(repo.g, P.WorkingTree())
    [h] = fd.hunks
    kinds = [(i, ln.kind, ln.text) for i, ln in enumerate(h.lines)]
    minus_b = next(i for i, k, t in kinds if k == "-" and t == "b")
    plus_c = next(i for i, k, t in kinds if k == "+" and t == "C")
    sel = P.Selection()
    sel.add_lines("f.txt", [(0, minus_b), (0, plus_c)])
    await P.apply(repo.g, P.build_patch([fd], sel), to="index")
    # b removed, c kept (its removal not selected), C added after c
    assert repo.index_content("f.txt") == "a\nc\nC\nd\n"


async def test_unstage_selected_line(repo):
    repo.commit({"f.txt": "a\nb\nc\n"})
    repo.write("f.txt", "a\nX\nb\nY\nc\n")
    repo.git("add", "f.txt")
    [fd] = await P.load_diff(repo.g, P.Staged())
    [h] = fd.hunks
    y = next(i for i, ln in enumerate(h.lines) if ln.kind == "+" and ln.text == "Y")
    sel = P.Selection()
    sel.add_lines("f.txt", [(0, y)])
    await P.apply(repo.g, P.build_patch([fd], sel, reverse=True), to="index", reverse=True)
    assert repo.index_content("f.txt") == "a\nX\nb\nc\n"
    assert repo.read("f.txt") == "a\nX\nb\nY\nc\n"


async def test_rollback_hunk_in_worktree(repo):
    repo.commit({"a.txt": THREE_HUNKS_BASE})
    repo.write("a.txt", three_hunk_change())
    [fd] = await P.load_diff(repo.g, P.WorkingTree())
    patch = P.build_patch([fd], P.Selection.hunks(fd, [0]), reverse=True)
    await P.apply(repo.g, patch, to="worktree", reverse=True)
    content = repo.read("a.txt")
    assert "line 3 changed" not in content
    assert "line 20 changed" in content and "inserted" in content


async def test_partial_new_file_forward_and_unstage(repo):
    repo.write("n.txt", "one\ntwo\nthree\n")
    diffs = await P.load_diff(repo.g, P.WorkingTree(), include_untracked=["n.txt"])
    [fd] = diffs
    assert fd.status == "A" and fd.path == "n.txt"
    [h] = fd.hunks
    sel = P.Selection()
    sel.add_lines("n.txt", [(0, 0), (0, 2)])
    await P.apply(repo.g, P.build_patch([fd], sel), to="index")
    assert repo.index_content("n.txt") == "one\nthree\n"

    # unstage "three" from the now-added file: file must stay in the index
    [sd] = await P.load_diff(repo.g, P.Staged())
    three = next(i for i, ln in enumerate(sd.hunks[0].lines) if ln.text == "three")
    sel = P.Selection()
    sel.add_lines("n.txt", [(0, three)])
    await P.apply(repo.g, P.build_patch([sd], sel, reverse=True), to="index", reverse=True)
    assert repo.index_content("n.txt") == "one\n"


async def test_partial_deletion_keeps_file(repo):
    repo.commit({"d.txt": "1\n2\n3\n"})
    repo.write("d.txt", None)
    [fd] = await P.load_diff(repo.g, P.WorkingTree())
    assert fd.status == "D"
    sel = P.Selection()
    sel.add_lines("d.txt", [(0, 1)])  # only delete "2"
    await P.apply(repo.g, P.build_patch([fd], sel), to="index")
    assert repo.index_content("d.txt") == "1\n3\n"


async def test_no_newline_at_eof(repo):
    repo.commit({"e.txt": "a\nb"})
    repo.write("e.txt", "a\nb\nc")
    [fd] = await P.load_diff(repo.g, P.WorkingTree())
    await P.apply(repo.g, P.build_patch([fd], P.Selection.hunks(fd, [0])), to="index")
    assert repo.index_content("e.txt") == "a\nb\nc"


async def test_binary_partial_rejected(repo):
    repo.commit({"b.bin": b"\x00\x01"})
    repo.write("b.bin", b"\x00\x02")
    [fd] = await P.load_diff(repo.g, P.WorkingTree())
    assert fd.binary and not fd.partial_allowed
    sel = P.Selection()
    sel.add_lines("b.bin", [(0, 0)])
    with pytest.raises(ValueError):
        P.build_patch([fd], sel)
    await P.apply(repo.g, P.build_patch([fd], P.Selection.files(["b.bin"])), to="index")
    assert repo.staged_diff()


async def test_commit_diff_and_root(repo):
    root = repo.git("rev-list", "--max-parents=0", "HEAD").strip()
    [fd] = await P.load_diff(repo.g, P.CommitDiff(root))
    assert fd.status == "A" and fd.path == "README.md"
    sha = repo.commit({"README.md": "hello\nworld\n"})
    [fd] = await P.load_diff(repo.g, P.CommitDiff(sha))
    assert [ln.text for ln in fd.hunks[0].lines if ln.kind == "+"] == ["world"]


async def test_quoted_paths(repo):
    name = 'we"ird\tname.txt'
    repo.commit({name: "a\n"})
    repo.write(name, "b\n")
    [fd] = await P.load_diff(repo.g, P.WorkingTree())
    assert fd.path == name
    await P.apply(repo.g, P.build_patch([fd], P.Selection.hunks(fd, [0])), to="index")
    assert repo.index_content(name) == "b\n"


def test_unquote_octal_utf8():
    assert P.unquote_path('"a/\\316\\261.txt"') == "a/α.txt"


def test_quote_path_roundtrip():
    for name in ["plain.txt", "sp ace.txt", 'q"uote', "tab\there", "α.txt", "back\\slash"]:
        assert P.unquote_path(P.quote_path("a/", name)) == "a/" + name
