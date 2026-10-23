from __future__ import annotations

import json
import stat
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

FAKE = Path(__file__).with_name("fake_gh.py")


def _pr(number: int, title: str, author: str, head: str, sha: str, **extra) -> dict:
    d = {
        "number": number,
        "title": title,
        "author": {"login": author},
        "state": "OPEN",
        "isDraft": False,
        "headRefName": head,
        "baseRefName": "main",
        "headRefOid": sha,
        "additions": 10,
        "deletions": 2,
        "changedFiles": 2,
        "reviewDecision": "REVIEW_REQUIRED",
        "statusCheckRollup": [],
        "labels": [],
        "url": f"https://github.com/acme/widgets/pull/{number}",
        "updatedAt": "2026-10-20T10:00:00Z",
        "createdAt": "2026-10-19T10:00:00Z",
        "isCrossRepository": False,
        "headRepositoryOwner": {"login": "acme"},
        "body": f"Body of #{number}",
        "mergeable": "MERGEABLE",
        "mergeStateStatus": "CLEAN",
        "reviewRequests": [],
        "latestReviews": [],
        "comments": [],
        "assignees": [],
    }
    d.update(extra)
    return d


@dataclass
class GhRepo:
    repo: object  # tests.conftest.RepoBuilder
    bare: Path
    state_file: Path

    @property
    def state(self) -> dict:
        return json.loads(self.state_file.read_text())

    def calls(self) -> list[list[str]]:
        return self.state.get("calls", [])

    def update(self, fn) -> None:  # type: ignore[no-untyped-def]
        state = self.state
        fn(state)
        self.state_file.write_text(json.dumps(state, indent=1))


@pytest.fixture
def gh_repo(repo_factory, tmp_path, monkeypatch) -> GhRepo:
    """A repo whose `origin` looks like github.com/acme/widgets, with two open PRs."""
    bare = tmp_path / "github.com" / "acme" / "widgets.git"
    bare.parent.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(bare)], check=True)
    repo = repo_factory("widgets")
    repo.git("remote", "add", "origin", str(bare))
    base = "".join(f"line {i}\n" for i in range(30))
    repo.commit({"app.py": base}, "base")
    repo.git("push", "-q", "-u", "origin", "main")
    repo.branch("feature/fuzz")
    repo.checkout("feature/fuzz")
    repo.commit({"fuzz.py": "import random\n"}, "Add fuzz harness")
    repo.commit(
        {"app.py": base.replace("line 3\n", "LINE 3\n").replace("line 25\n", "LINE 25\n")},
        "Harden parser",
    )
    repo.git("push", "-q", "-u", "origin", "feature/fuzz")
    fuzz = repo.head()
    repo.checkout("main")
    repo.branch("feature/cli")
    repo.checkout("feature/cli")
    repo.commit({"cli.py": "import sys\n"}, "Add CLI flags")
    repo.git("push", "-q", "-u", "origin", "feature/cli")
    cli = repo.head()
    repo.checkout("main")
    for n, sha in ((1, fuzz), (2, cli)):
        subprocess.run(
            ["git", "--git-dir", str(bare), "update-ref", f"refs/pull/{n}/head", sha], check=True
        )
    checks = [
        {
            "__typename": "CheckRun",
            "name": "tests",
            "status": "COMPLETED",
            "conclusion": "SUCCESS",
            "detailsUrl": "https://github.com/acme/widgets/actions/runs/111/job/1",
            "workflowName": "CI",
            "startedAt": "2026-10-20T10:00:00Z",
            "completedAt": "2026-10-20T10:02:30Z",
        },
        {
            "__typename": "CheckRun",
            "name": "lint",
            "status": "COMPLETED",
            "conclusion": "FAILURE",
            "detailsUrl": "https://github.com/acme/widgets/actions/runs/222/job/2",
            "workflowName": "CI",
            "startedAt": "2026-10-20T10:00:00Z",
            "completedAt": "2026-10-20T10:00:40Z",
        },
    ]
    state = {
        "me": "me",
        "bare": str(bare),
        "repo": {
            "owner": {"login": "acme"},
            "name": "widgets",
            "defaultBranchRef": {"name": "main"},
            "url": "https://github.com/acme/widgets",
        },
        "prs": [
            _pr(
                1,
                "Harden parser with fuzzing",
                "lin",
                "feature/fuzz",
                fuzz,
                statusCheckRollup=checks,
                reviewRequests=[{"login": "me"}],
                labels=[{"name": "security"}],
            ),
            _pr(2, "Add CLI flags", "me", "feature/cli", cli, isDraft=True),
        ],
        "threads": {
            "1": [
                {
                    "id": "T1",
                    "isResolved": False,
                    "isOutdated": False,
                    "path": "app.py",
                    "line": 4,
                    "diffSide": "RIGHT",
                    "comments": {
                        "nodes": [
                            {
                                "id": "C1",
                                "databaseId": 1001,
                                "author": {"login": "lin"},
                                "body": "Why upper-case here?",
                            }
                        ]
                    },
                }
            ]
        },
        "calls": [],
    }
    state_file = tmp_path / "gh-state.json"
    state_file.write_text(json.dumps(state, indent=1))
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gh = bin_dir / "gh"
    gh.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{FAKE}" "$@"\n')
    gh.chmod(gh.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{bin_dir}:" + __import__("os").environ["PATH"])
    monkeypatch.setenv("FAKE_GH_STATE", str(state_file))
    return GhRepo(repo, bare, state_file)
