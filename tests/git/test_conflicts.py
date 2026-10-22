from __future__ import annotations

from gitpanel.git import conflicts as CF
from gitpanel.git.operations import detect

BASE = (
    "header\nspacer a\nspacer b\none\ntwo\nthree\nmiddle 1\nmiddle 2\nmiddle 3\nmiddle 4\n"
    "four\nfive\nspacer c\nspacer d\nfooter\n"
)


def make_conflict(repo):
    repo.commit({"f.txt": BASE}, "base")
    repo.branch("other")
    repo.commit(
        {
            "f.txt": BASE.replace("one", "ONE-main")
            .replace("five", "FIVE-main")
            .replace("header", "header (main)")
        },
        "main edits",
    )
    repo.checkout("other")
    repo.commit(
        {
            "f.txt": BASE.replace("one", "one-other")
            .replace("five", "five-other")
            .replace("footer", "footer (other)")
        },
        "other edits",
    )
    repo.checkout("main")
    repo.merge("other", check=False)


async def test_model_chunks_and_resolution(repo):
    make_conflict(repo)
    [c] = await CF.conflicts(repo.g)
    assert c.path == "f.txt" and c.code == "UU" and c.describe == "both modified"
    model = await CF.merge_model(repo.g, "f.txt")
    chunks = model.chunks
    assert len(chunks) == 2 and model.unresolved == 2
    assert chunks[0].ours == ["ONE-main\n"] and chunks[0].theirs == ["one-other\n"]
    assert chunks[0].base == ["one\n"]
    text = model.result()
    assert "header (main)" in text and "footer (other)" in text  # non-conflicting kept
    assert text.count("<<<<<<<") == 2
    chunks[0].resolution = "ours"
    chunks[1].resolution = "both"
    out = model.result()
    assert "ONE-main\n" in out and "one-other" not in out
    assert "FIVE-main\nfive-other\n" in out and "<<<<<<<" not in out
    await CF.resolve_with(repo.g, "f.txt", out)
    assert await CF.conflicts(repo.g) == []
    op = await detect(repo.g)
    assert op.conflicts == 0


async def test_accept_whole_file_and_delete_modify(repo):
    repo.commit({"keep.txt": "base\n", "gone.txt": "base\n"}, "base")
    repo.branch("other")
    repo.commit({"keep.txt": "main\n", "gone.txt": "main change\n"}, "main")
    repo.checkout("other")
    repo.write("gone.txt", None)
    repo.commit({"keep.txt": "other\n"}, "other deletes gone.txt")
    repo.checkout("main")
    repo.merge("other", check=False)
    codes = {c.path: c.code for c in await CF.conflicts(repo.g)}
    assert codes == {"keep.txt": "UU", "gone.txt": "UD"}
    await CF.accept(repo.g, ["keep.txt"], "theirs")
    await CF.accept(repo.g, ["gone.txt"], "theirs")  # theirs deleted it
    assert repo.read("keep.txt") == "other\n"
    assert not (repo.path / "gone.txt").exists()
    assert await CF.conflicts(repo.g) == []


async def test_labels_merge_rebase_pick(repo):
    make_conflict(repo)
    lab = await CF.labels(repo.g)
    assert lab.ours == "Yours (main)" and "other" in lab.theirs
    repo.git("merge", "--abort")
    repo.checkout("other")
    repo.git("rebase", "main", check=False)
    lab = await CF.labels(repo.g)
    assert lab.ours.startswith("Onto (") and "other edits" in lab.theirs
    repo.git("rebase", "--abort")
    repo.checkout("main")
    repo.git("cherry-pick", "other", check=False)
    lab = await CF.labels(repo.g)
    assert lab.theirs.startswith("Picked (") and "other edits" in lab.theirs


def test_parse_no_conflicts():
    m = CF.parse_merge_output("x", "a\nb\n")
    assert m.chunks == [] and m.result() == "a\nb\n"
