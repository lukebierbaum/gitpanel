from __future__ import annotations

import os
import subprocess
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from gitpanel.git.runner import Git

DETERMINISTIC_ENV = {
    "GIT_AUTHOR_NAME": "Test Author",
    "GIT_AUTHOR_EMAIL": "author@example.com",
    "GIT_COMMITTER_NAME": "Test Committer",
    "GIT_COMMITTER_EMAIL": "committer@example.com",
    "GIT_CONFIG_NOSYSTEM": "1",
}


@pytest.fixture(autouse=True)
def _isolated_git_env(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch):
    home = tmp_path_factory.mktemp("home")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(home / ".local" / "state"))  # saved pane sizes
    monkeypatch.delenv("GIT_DIR", raising=False)
    monkeypatch.delenv("GIT_WORK_TREE", raising=False)
    for k, v in DETERMINISTIC_ENV.items():
        monkeypatch.setenv(k, v)
    (home / ".gitconfig").write_text(
        "[init]\n\tdefaultBranch = main\n[user]\n\tname = Test User\n\temail = test@example.com\n"
        "[commit]\n\tgpgsign = false\n[tag]\n\tgpgsign = false\n"
    )


class RepoBuilder:
    """Builds throwaway repositories with deterministic commits."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._tick = 0
        path.mkdir(parents=True, exist_ok=True)
        self.git("init", "-q", "-b", "main")

    # -- raw helpers --------------------------------------------------------
    def git(self, *args: str, check: bool = True, input: str | None = None) -> str:
        self._tick += 1
        date = f"2026-01-01T00:{self._tick // 60:02d}:{self._tick % 60:02d}+00:00"
        env = {**os.environ, "GIT_AUTHOR_DATE": date, "GIT_COMMITTER_DATE": date}
        res = subprocess.run(
            ["git", *args],
            cwd=self.path,
            env=env,
            input=input,
            capture_output=True,
            text=True,
        )
        if check and res.returncode != 0:
            raise AssertionError(f"git {' '.join(args)} failed:\n{res.stdout}\n{res.stderr}")
        return res.stdout

    @property
    def g(self) -> Git:
        return Git(self.path)

    def write(self, rel: str, content: str | bytes | None) -> None:
        p = self.path / rel
        if content is None:
            p.unlink()
            return
        p.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            p.write_bytes(content)
        else:
            p.write_bytes(content.encode())

    def read(self, rel: str) -> str:
        return (self.path / rel).read_bytes().decode()

    # -- high level ---------------------------------------------------------
    def commit(
        self, files: dict[str, str | bytes | None] | None = None, msg: str = "commit"
    ) -> str:
        for rel, content in (files or {}).items():
            self.write(rel, content)
        self.git("add", "-A")
        self.git("commit", "-q", "--allow-empty", "-m", msg)
        return self.head()

    def head(self) -> str:
        return self.git("rev-parse", "HEAD").strip()

    def branch(self, name: str, at: str | None = None) -> None:
        self.git("branch", name, *([at] if at else []))

    def checkout(self, name: str) -> None:
        self.git("checkout", "-q", name)

    def merge(self, name: str, *, check: bool = True) -> None:
        self.git("merge", "--no-edit", "-q", name, check=check)

    def index_content(self, rel: str) -> str:
        return self.git("show", f":{rel}")

    def staged_diff(self) -> str:
        return self.git("diff", "--cached")

    def unstaged_diff(self) -> str:
        return self.git("diff")

    def conflict(self, path: str = "conflict.txt") -> None:
        """Leave the repo mid-merge with a conflict in `path`."""
        self.commit({path: "base\n"}, "base for conflict")
        self.branch("conflict-other")
        self.commit({path: "ours\n"}, "ours")
        self.checkout("conflict-other")
        self.commit({path: "theirs\n"}, "theirs")
        self.checkout("main")
        self.merge("conflict-other", check=False)

    def bare_remote(self, name: str = "origin") -> Path:
        remote = self.path.parent / f"{self.path.name}-{name}.git"
        subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
        self.git("remote", "add", name, str(remote))
        return remote

    def worktree(self, name: str, branch: str, *, new: bool = True) -> Path:
        dest = self.path.parent / f"{self.path.name}-wt-{name}"
        if new:
            self.git("worktree", "add", "-q", "-b", branch, str(dest))
        else:
            self.git("worktree", "add", "-q", str(dest), branch)
        return dest


@pytest.fixture
def repo_factory(tmp_path: Path) -> Iterator[Callable[..., RepoBuilder]]:
    count = 0

    def make(name: str | None = None) -> RepoBuilder:
        nonlocal count
        count += 1
        return RepoBuilder(tmp_path / (name or f"repo{count}"))

    yield make


@pytest.fixture
def repo(repo_factory: Callable[..., RepoBuilder]) -> RepoBuilder:
    r = repo_factory("repo")
    r.commit({"README.md": "hello\n"}, "initial")
    return r
