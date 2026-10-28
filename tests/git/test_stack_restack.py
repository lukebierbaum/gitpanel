from __future__ import annotations

import subprocess

import pytest

from gitpanel.git import remote as RM
from gitpanel.git import scripted_rebase as SR
from gitpanel.git.runner import GitError
from gitpanel.git.session import RepoSession
from gitpanel.git.stack import RestackLayer, nearest_base, restack

BASE = "".join(f"line {i}\n" for i in range(1, 21))


def stack_repo(repo):  # type: ignore[no-untyped-def]
    """main ← s1 ← s2 ← s3, one commit per layer."""
    repo.commit({"app.txt": BASE}, "base")
    for name, parent, files in (
        ("s1", "main", {"one.txt": "1\n"}),
        ("s2", "s1", {"two.txt": "2\n"}),
        ("s3", "s2", {"three.txt": "3\n"}),
    ):
        repo.git("switch", "-q", "-c", name, parent)
        repo.commit(files, f"layer {name}")
    repo.git("switch", "-q", "main")
    return {b: repo.git("rev-parse", b).strip() for b in ("main", "s1", "s2", "s3")}


def squash_merge(repo, branch: str) -> None:  # type: ignore[no-untyped-def]
    """What GitHub's squash merge does: one new commit on main with the branch's tree."""
    repo.git("switch", "-q", "main")
    repo.git("merge", "-q", "--squash", branch)
    repo.git("commit", "-q", "-m", f"{branch} (squashed)")


def subjects(repo, rng: str) -> list[str]:  # type: ignore[no-untyped-def]
    return repo.git("log", "--format=%s", rng).splitlines()


def contains(repo, ancestor: str, rev: str) -> bool:  # type: ignore[no-untyped-def]
    res = subprocess.run(
        ["git", "merge-base", "--is-ancestor", ancestor, rev], cwd=repo.path, check=False
    )
    return res.returncode == 0


async def test_restack_after_a_squash_merge(repo):
    heads = stack_repo(repo)
    squash_merge(repo, "s1")
    session = await RepoSession.open(repo.path)
    # s2's own commits start after s1's old head — patch-ids can't see that after a squash
    layers = [
        RestackLayer("s2", heads["s2"], heads["s1"]),
        RestackLayer("s3", heads["s3"], heads["s2"]),
    ]
    res = await restack(session, "main", layers)
    assert res.status == "done"
    assert subjects(repo, "main..s2") == ["layer s2"]  # s1's commit isn't replayed
    assert subjects(repo, "s2..s3") == ["layer s3"]
    assert contains(repo, "main", "s2") and contains(repo, "s2", "s3")
    assert (repo.path / "one.txt").exists()  # s1's change is there, via the squash


async def test_restack_conflict_mid_stack_then_continue(repo):
    heads = stack_repo(repo)
    # s3 and main both change line 5
    repo.git("switch", "-q", "s3")
    repo.commit({"app.txt": BASE.replace("line 5\n", "s3's line 5\n")}, "s3 edits line 5")
    heads["s3"] = repo.head()
    repo.git("switch", "-q", "main")
    repo.commit({"app.txt": BASE.replace("line 5\n", "main's line 5\n")}, "main edits line 5")
    session = await RepoSession.open(repo.path)
    layers = [
        RestackLayer("s1", heads["s1"], heads["main"]),
        RestackLayer("s2", heads["s2"], heads["s1"]),
        RestackLayer("s3", heads["s3"], heads["s2"]),
    ]
    res = await restack(session, "main", layers)
    assert res.status == "conflict"
    (repo.path / "app.txt").write_text(BASE.replace("line 5\n", "both\n"))
    repo.git("add", "app.txt")
    res = await SR.resume(session)
    assert res.status == "done"
    main = repo.git("rev-parse", "main").strip()
    for b, parent in (("s1", main), ("s2", "s1"), ("s3", "s2")):
        assert contains(repo, parent, b)
    assert subjects(repo, "main..s3") == ["s3 edits line 5", "layer s3", "layer s2", "layer s1"]
    assert (repo.path / "app.txt").read_text().count("both") == 1
    assert repo.git("rev-parse", "--abbrev-ref", "HEAD").strip() == "s3"


async def test_nearest_base(repo):
    stack_repo(repo)
    session = await RepoSession.open(repo.path)
    assert await nearest_base(session.git, "s3", ["main", "s1", "s2", "s3"]) == "s2"
    assert await nearest_base(session.git, "s1", ["s2", "s3"]) is None


async def test_push_many_is_atomic_and_leased(repo, tmp_path):
    heads = stack_repo(repo)
    bare = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
    repo.git("remote", "add", "origin", str(bare))
    repo.git("push", "-q", "origin", "s1", "s2")
    session = await RepoSession.open(repo.path)
    repo.git("switch", "-q", "s1")
    repo.git("commit", "-q", "--amend", "-m", "layer s1, reworded")
    repo.git("switch", "-q", "main")
    leases = {"s1": heads["s1"], "s2": heads["s2"]}
    await RM.push_many(session.git, "origin", ["s1", "s2"], leases)  # non-ff, but leased
    assert subprocess.run(
        ["git", "--git-dir", str(bare), "log", "-1", "--format=%s", "s1"],
        capture_output=True, text=True, check=True,
    ).stdout.strip() == "layer s1, reworded"  # fmt: skip
    # someone else pushed s2 meanwhile: the stale lease refuses, and atomically nothing moves
    repo.git("push", "-q", "origin", "s3:s2", "--force")
    repo.git("switch", "-q", "s1")
    repo.git("commit", "-q", "--amend", "-m", "again")
    repo.git("switch", "-q", "main")
    s1_remote = repo.git("--git-dir", str(bare), "rev-parse", "s1").strip()
    with pytest.raises(GitError):
        await RM.push_many(
            session.git, "origin", ["s1", "s2"], {"s1": s1_remote, "s2": heads["s2"]}
        )
    assert repo.git("--git-dir", str(bare), "rev-parse", "s1").strip() == s1_remote
