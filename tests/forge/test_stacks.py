from __future__ import annotations

import json

from gitpanel.forge import github as GH
from gitpanel.forge.models import PRStatus, PullRequest, StackInfo
from gitpanel.forge.stacks import Layer, build_stacks
from gitpanel.git.session import RepoSession
from gitpanel.git.stack import LocalStack, local_chains, read_gh_stack


def pr(n: int, head: str, base: str = "main", *, cross: bool = False) -> PullRequest:
    return PullRequest(n, f"PR {n}", "me", "OPEN", False, head, base, cross_repo=cross)


def test_build_stacks_github_chain_and_local():
    prs = [
        pr(1, "a"), pr(2, "b", "a"), pr(3, "c", "b"),  # GitHub stack 7 (1, 2, 3)
        pr(10, "x"), pr(11, "y", "x"), pr(12, "z", "y"),  # chained, not a stack
        pr(20, "fork-top", "z", cross=True),  # a fork can sit on top of a chain
        pr(30, "lonely"),  # not stacked
        pr(40, "l1"),  # bottom of a local stack whose top has no PR
    ]  # fmt: skip
    statuses = {
        n: PRStatus("none", "", StackInfo(7, pos, 3, "main")) for n, pos in ((3, 3), (1, 1), (2, 2))
    }
    local = [
        LocalStack("main", ("l1", "l2"), ("", ""), "gh-stack"),
        LocalStack("main", ("x", "y"), ("", ""), "ancestry"),  # all have PRs: shown as chain
    ]
    stacks = build_stacks(prs, statuses, local)
    kinds = [(s.kind, s.label, [ly.pr or ly.branch for ly in s.layers]) for s in stacks]
    assert kinds == [
        ("github", "Stack #7", [1, 2, 3]),
        ("chain", "not a GitHub stack yet", [10, 11, 12, 20]),
        ("local", "local · not a GitHub stack yet", [40, "l2"]),
    ]
    assert stacks[1].layers[-1] == Layer("fork-top", 20, 3)
    assert stacks[0].index_of(pr=2) == 1 and stacks[2].index_of(branch="l2") == 1


async def test_gh_stack_record_and_local_chains(repo):
    repo.commit({"base.txt": "0\n"}, "base")
    for name, parent in (("s1", "main"), ("s2", "s1"), ("s3", "s2"), ("other", "main")):
        repo.git("switch", "-q", "-c", name, parent)
        repo.commit({f"{name}.txt": name}, name)
    repo.git("switch", "-q", "main")
    session = await RepoSession.open(repo.path)
    assert read_gh_stack(session) == []
    (session.common_dir / "gh-stack").write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "stacks": [
                    {
                        "trunk": {"branch": "main", "head": "abc"},
                        "branches": [
                            {"branch": "s1", "base": "abc"},
                            {"branch": "s2", "base": "def"},
                        ],
                    }
                ],
            }
        )
    )
    [recorded] = read_gh_stack(session)
    assert recorded.branches == ("s1", "s2") and recorded.bases == ("abc", "def")
    chains = await local_chains(session.git, "main")
    assert [c.branches for c in chains] == [("s1", "s2", "s3")]  # "other" stands alone
    assert await local_chains(session.git, "main", exclude={"s1", "s2"}) == []


async def test_github_stack_fields(gh_repo):
    gh_repo.update(lambda st: st.setdefault("stacks", []).append(
        {"number": 7, "base": "main", "prs": [1, 2]}
    ))  # fmt: skip
    session = await RepoSession.open(gh_repo.repo.path)
    provider = await GH.detect(session)
    assert provider is not None
    found = await provider.statuses([1, 2])
    assert found[1].stack == StackInfo(7, 1, 2, "main") and found[2].stack.position == 2
    assert await provider.stack_members(2) == [1, 2]
    assert [p.number for p in await provider.open_pr_index()] == [1, 2]
