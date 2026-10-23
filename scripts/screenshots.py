"""Regenerate the README screenshots: `pixi run python scripts/screenshots.py`."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "screenshots"
SIZE = (140, 38)

sys.path.insert(0, str(ROOT))
from gitpanel.config import Config  # noqa: E402
from gitpanel.ui.app import GitPanelApp  # noqa: E402

ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "Ada Lovelace",
    "GIT_AUTHOR_EMAIL": "ada@example.com",
    "GIT_COMMITTER_NAME": "Ada Lovelace",
    "GIT_COMMITTER_EMAIL": "ada@example.com",
    "GIT_CONFIG_NOSYSTEM": "1",
}


def git(repo: Path, *args: str, check: bool = True) -> None:
    subprocess.run(
        ["git", "-c", "commit.gpgsign=false", *args],
        cwd=repo,
        env=ENV,
        check=check,
        capture_output=True,
    )


def commit(repo: Path, files: dict[str, str], msg: str) -> None:
    for name, content in files.items():
        p = repo / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", msg)


def pick_repo(base: Path) -> Path:
    repo = base / "parser"
    origin = base / "origin.git"
    git(base, "init", "-q", "--bare", str(origin))
    git(base, "init", "-q", "-b", "main", str(repo))
    git(repo, "remote", "add", "origin", str(origin))
    commit(repo, {"parser.py": "def parse(s):\n    return s.split()\n"}, "Initial parser")
    git(repo, "branch", "release/1.x")
    git(repo, "switch", "-q", "-c", "feature/tokens")
    commit(repo, {"tokens.py": "TOKENS = []\n"}, "Add token table")
    commit(
        repo,
        {"parser.py": "def parse(s):\n    return s.strip().split()\n"},
        "Fix whitespace bug in parse()",
    )
    commit(repo, {"cli.py": "import sys\n"}, "Add --tokens CLI flag")
    commit(repo, {"README.md": "# parser\n"}, "Document tokens")
    git(repo, "switch", "-q", "main")
    git(repo, "cherry-pick", "-x", "feature/tokens~2")
    commit(repo, {"VERSION": "1.1\n"}, "Bump version")
    git(repo, "push", "-q", "-u", "origin", "main", "feature/tokens", "release/1.x")
    for name in ("feature/ui/dark-mode", "bugfix/unicode"):
        git(repo, "branch", name, "main")
    # a teammate's commit we've fetched but not pulled, and local work not yet pushed
    other = base / "teammate"
    git(base, "clone", "-q", "-b", "main", str(origin), str(other))
    commit(other, {"CHANGELOG.md": "## 1.1\n"}, "Add changelog")
    git(other, "push", "-q", "origin", "main")
    git(repo, "fetch", "-q")
    commit(repo, {"bench.py": "import timeit\n"}, "Benchmark the tokenizer")
    git(repo, "worktree", "add", "-q", str(base / "parser-release"), "release/1.x")
    (repo / "parser.py").write_text(
        "def parse(s):\n    # split on whitespace\n    return s.strip().split()\n\n\n"
        "def parse_all(lines):\n    return [parse(ln) for ln in lines]\n"
    )
    (repo / "notes.txt").write_text("todo\n")
    git(repo, "add", "notes.txt")
    (repo / "scratch.py").write_text("print('hi')\n")
    return repo


def conflict_repo(base: Path) -> Path:
    repo = base / "service"
    git(base, "init", "-q", "-b", "main", str(repo))
    src = (
        "def handler(req):\n    validate(req)\n    log('start')\n    data = load(req.id)\n"
        "    data = transform(data)\n    log('done')\n    return render(data)\n"
    )
    commit(repo, {"app.py": src}, "Handler")
    git(repo, "branch", "feature/v2")
    commit(
        repo,
        {
            "app.py": src.replace("log('start')", "log.info('start', id=req.id)").replace(
                "render(data)", "render(data, fmt='json')"
            )
        },
        "Structured logging",
    )
    git(repo, "switch", "-q", "feature/v2")
    commit(
        repo,
        {
            "app.py": src.replace("log('start')", "logger.debug('start')").replace(
                "render(data)", "render_v2(data)"
            )
        },
        "Use render_v2",
    )
    git(repo, "switch", "-q", "main")
    git(repo, "merge", "feature/v2", check=False)
    return repo


def pr_repo(base: Path) -> Path:
    """A repo whose origin looks like github.com/acme/parser, answered by the tests' fake gh."""
    bare = base / "github.com" / "acme" / "parser.git"
    bare.parent.mkdir(parents=True)
    git(base, "init", "-q", "--bare", "-b", "main", str(bare))
    repo = base / "parser-prs"
    git(base, "init", "-q", "-b", "main", str(repo))
    git(repo, "remote", "add", "origin", str(bare))
    src = "def parse(s):\n    return s.split()\n"
    commit(repo, {"parser.py": src}, "Initial parser")
    git(repo, "push", "-q", "-u", "origin", "main")
    now = datetime.now(UTC)
    ago = lambda **kw: (now - timedelta(**kw)).strftime("%Y-%m-%dT%H:%M:%SZ")  # noqa: E731

    def check(name: str, conclusion: str, secs: int) -> dict:
        return {
            "__typename": "CheckRun", "name": name, "workflowName": "CI",
            "status": "COMPLETED" if conclusion != "PENDING" else "IN_PROGRESS",
            "conclusion": conclusion if conclusion != "PENDING" else "",
            "detailsUrl": "https://github.com/acme/parser/actions/runs/1/job/1",
            "startedAt": ago(minutes=30), "completedAt": ago(minutes=30, seconds=-secs),
        }  # fmt: skip

    specs = [
        # number, branch, title, author, ci, review, labels, draft, hours ago
        (48, "feature/streaming", "Stream tokens instead of building a list", "ada",
         "FAILURE", "REVIEW_REQUIRED", ["performance"], False, 2),
        (47, "fix/unicode-ws", "Treat unicode whitespace as separators", "grace",
         "SUCCESS", "APPROVED", ["bug"], False, 5),
        (45, "feature/cli-json", "Add --json output to the CLI", "linus",
         "PENDING", "REVIEW_REQUIRED", [], False, 26),
        (44, "docs/tokens", "Document the token table", "margaret",
         "SUCCESS", "CHANGES_REQUESTED", ["docs"], False, 50),
        (41, "spike/rust-core", "Rust tokenizer core (spike)", "ada",
         "SUCCESS", "", [], True, 120),
    ]  # fmt: skip
    prs = []
    for number, branch, title, author, ci, review, labels, draft, hours in specs:
        git(repo, "switch", "-q", "-c", branch, "main")
        commit(repo, {"parser.py": src.replace("s.split()", "s.strip().split()")}, title)
        commit(repo, {f"tests/test_{number}.py": "def test():\n    assert True\n"}, "Add tests")
        git(repo, "push", "-q", "-u", "origin", branch)
        sha = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True
        ).stdout.strip()
        git(repo, "--git-dir", str(bare), "update-ref", f"refs/pull/{number}/head", sha)
        checks = [check("tests", "SUCCESS" if ci != "PENDING" else "PENDING", 154),
                  check("lint", ci if ci == "FAILURE" else "SUCCESS", 38),
                  check("typecheck", "SUCCESS", 71)]  # fmt: skip
        prs.append(
            {
                "number": number, "title": title, "author": {"login": author}, "state": "OPEN",
                "isDraft": draft, "headRefName": branch, "baseRefName": "main",
                "headRefOid": sha, "additions": 42, "deletions": 7, "changedFiles": 2,
                "reviewDecision": review, "statusCheckRollup": checks,
                "labels": [{"name": n} for n in labels],
                "url": f"https://github.com/acme/parser/pull/{number}",
                "updatedAt": ago(hours=hours), "createdAt": ago(hours=hours + 30),
                "isCrossRepository": False, "headRepositoryOwner": {"login": "acme"},
                "body": "",
                "mergeable": "MERGEABLE", "mergeStateStatus": "BLOCKED",
                "reviewRequests": [{"login": "you"}], "latestReviews": [], "comments": [],
                "assignees": [],
            }
        )  # fmt: skip
    git(repo, "switch", "-q", "main")
    by_number = {p["number"]: p for p in prs}
    by_number[47]["baseRefName"] = "feature/streaming"
    by_number[44]["baseRefName"] = "feature/cli-json"
    prs[0]["body"] = (
        "Builds tokens lazily so `parse_all()` can handle files that don't fit in memory.\n\n"
        "- `tokenize()` is now a generator\n- `parse_all()` streams line by line\n\n"
        "Benchmarks: **3.1× less memory** on the 2 GB corpus, same speed."
    )
    prs[0]["latestReviews"] = [
        {
            "author": {"login": "grace"},
            "state": "COMMENTED",
            "body": "Looks good — one nit on lint.",
            "submittedAt": ago(hours=1),
        }
    ]
    prs[0]["comments"] = [
        {"author": {"login": "grace"}, "createdAt": ago(hours=1),
         "body": "Nice! Lint is failing on an unused import in tokens.py."},
        {"author": {"login": "ada"}, "createdAt": ago(minutes=40),
         "body": "Good catch — fixing."},
    ]  # fmt: skip
    state = base / "gh-state.json"
    state.write_text(
        json.dumps(
            {
                "me": "you",
                "bare": str(bare),
                "repo": {
                    "owner": {"login": "acme"},
                    "name": "parser",
                    "defaultBranchRef": {"name": "main"},
                    "url": "https://github.com/acme/parser",
                },  # fmt: skip
                "prs": prs,
                "threads": {},
                # #45 → #44 is a GitHub stack; #48 → #47 chain but aren't linked yet
                "stacks": [{"number": 3, "base": "main", "prs": [45, 44]}],
                "calls": [],
            }
        )
    )
    bin_dir = base / "bin"
    bin_dir.mkdir()
    shim = bin_dir / "gh"
    shim.write_text(
        f'#!/bin/sh\nexec "{sys.executable}" "{ROOT / "tests" / "forge" / "fake_gh.py"}" "$@"\n'
    )
    shim.chmod(0o755)
    os.environ["PATH"] = f"{bin_dir}:{os.environ['PATH']}"
    os.environ["FAKE_GH_STATE"] = str(state)
    return repo


async def shot(
    repo: Path,
    name: str,
    keys: list[str],
    theme: str = "hulk",
    message: str | None = None,
    **config: object,
) -> None:
    app = GitPanelApp(repo, config=Config(theme=theme, **config))  # type: ignore[arg-type]
    async with app.run_test(size=SIZE) as pilot:
        for _ in range(40):
            await pilot.pause(0.02)
        for k in keys:
            if k == "<message>" and message is not None:
                app.query_one("#commit-msg").text = message  # type: ignore[attr-defined]
                continue
            await pilot.press(k)
            for _ in range(15):
                await pilot.pause(0.02)
        for _ in range(100):  # let background work finish so nothing shows as "running"
            await pilot.pause(0.02)
            if all(w.is_finished for w in app.workers):
                break
        app.save_screenshot(str(OUT / f"{name}.svg"))
    print("wrote", OUT / f"{name}.svg")


async def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    base = Path(tempfile.mkdtemp(prefix="gitpanel-shots-"))
    os.environ["XDG_STATE_HOME"] = str(base / "state")  # default pane sizes, not yours
    try:
        repo = pick_repo(base)
        await shot(repo, "commit", ["down", "down", "down", "tab"])
        await shot(repo, "log", ["2", "down", "down", "down"])
        await shot(repo, "branches", ["b"])
        await shot(repo, "compare", ["b", *"feat", "ctrl+e"])
        await shot(repo, "worktrees", ["3"])
        await shot(conflict_repo(base), "resolver", ["down", "down", "enter"])
        await shot(pr_repo(base), "prs", ["7"], pr_default_query="open")
        # last: these steps change the repo (stage, commit) so the Console has something to show
        await shot(
            repo,
            "console",
            ["a", "<message>", "ctrl+k", "6"],
            message="Parse multiple lines\n\nAdds parse_all().",
        )
    finally:
        shutil.rmtree(base, ignore_errors=True)


if __name__ == "__main__":
    asyncio.run(main())
