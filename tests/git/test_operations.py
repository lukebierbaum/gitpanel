from __future__ import annotations

from gitpanel.git.operations import OpKind, detect


async def test_no_operation(repo):
    assert await detect(repo.g) is None


async def test_merge_conflict(repo):
    repo.conflict()
    op = await detect(repo.g)
    assert op is not None and op.kind is OpKind.MERGE and op.conflicts == 1


async def test_rebase_conflict_progress(repo):
    repo.commit({"f.txt": "base\n"})
    repo.branch("topic")
    repo.commit({"f.txt": "main\n"}, "main change")
    repo.checkout("topic")
    repo.commit({"g.txt": "1\n"}, "t1")
    repo.commit({"f.txt": "topic\n"}, "t2")
    repo.commit({"h.txt": "3\n"}, "t3")
    repo.git("rebase", "main", check=False)
    op = await detect(repo.g)
    assert op is not None and op.kind is OpKind.REBASE
    assert (op.step, op.total) == (2, 3)
    assert op.head_name == "topic" and op.conflicts == 1


async def test_cherry_pick_sequence(repo):
    repo.commit({"f.txt": "base\n"})
    repo.branch("src")
    repo.checkout("src")
    a = repo.commit({"a.txt": "a\n"}, "a")
    b = repo.commit({"f.txt": "src\n"}, "b")
    c = repo.commit({"c.txt": "c\n"}, "c")
    repo.checkout("main")
    repo.commit({"f.txt": "main\n"}, "main")
    repo.git("cherry-pick", a, b, c, check=False)
    op = await detect(repo.g)
    assert op is not None and op.kind is OpKind.CHERRY_PICK
    assert op.current == b and op.remaining == 2 and op.step is None
    # gitpanel records the sequence length when it starts one
    (repo.path / ".git" / "gitpanel").mkdir(exist_ok=True)
    (repo.path / ".git" / "gitpanel" / "sequence-total").write_text("3")
    op = await detect(repo.g)
    assert (op.step, op.total) == (2, 3)
    assert op.label == "Cherry-pick (2/3) in progress"


async def test_revert(repo):
    c1 = repo.commit({"f.txt": "1\n"})
    repo.commit({"f.txt": "2\n"})
    repo.git("revert", "--no-edit", c1, check=False)
    op = await detect(repo.g)
    assert op is not None and op.kind is OpKind.REVERT


async def test_bisect(repo):
    good = repo.head()
    for i in range(4):
        repo.commit({"n.txt": f"{i}\n"})
    repo.git("bisect", "start", "HEAD", good)
    op = await detect(repo.g)
    assert op is not None and op.kind is OpKind.BISECT
