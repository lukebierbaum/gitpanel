"""Async wrapper around the git CLI. All git access goes through `Git`."""

from __future__ import annotations

import asyncio
import os
import sys
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from pathlib import Path

BASE_CONFIG = (
    "-c",
    "color.ui=false",
    "-c",
    "core.quotepath=false",
    "-c",
    "advice.detachedHead=false",
)


def editor_command() -> str:
    """Command git runs as GIT_EDITOR / GIT_SEQUENCE_EDITOR (see `gitpanel.git._editor`)."""
    exe = sys.executable.replace("'", "'\\''")
    return f"'{exe}' -m gitpanel.git._editor"


def base_env() -> dict[str, str]:
    env = dict(os.environ)
    env.update(
        LC_ALL="C",
        LANG="C",
        GIT_PAGER="cat",
        PAGER="cat",
        GIT_EDITOR=editor_command(),
        GIT_SEQUENCE_EDITOR=editor_command(),
        GIT_MERGE_AUTOEDIT="no",
    )
    env.pop("GIT_DIR", None)
    env.pop("GIT_WORK_TREE", None)
    env.pop("GIT_INDEX_FILE", None)
    return env


@dataclass(frozen=True)
class GitResult:
    args: tuple[str, ...]
    returncode: int
    stdout: bytes
    stderr: str

    @property
    def text(self) -> str:
        return self.stdout.decode("utf-8", "surrogateescape")

    @property
    def ok(self) -> bool:
        return self.returncode == 0


class GitError(Exception):
    def __init__(self, result: GitResult) -> None:
        self.result = result
        lines = [ln for ln in result.stderr.splitlines() if ln.strip()]
        if not lines:
            lines = [ln for ln in result.text.splitlines() if ln.strip()]
        head = lines[0] if lines else f"git exited with {result.returncode}"
        super().__init__(head)

    @property
    def details(self) -> str:
        parts = [f"$ git {' '.join(self.result.args)}"]
        if self.result.text.strip():
            parts.append(self.result.text.rstrip())
        if self.result.stderr.strip():
            parts.append(self.result.stderr.rstrip())
        return "\n".join(parts)


class Git:
    """Runs git commands in a working directory."""

    def __init__(self, cwd: Path, *, git_exe: str = "git") -> None:
        self.cwd = Path(cwd)
        self.git_exe = git_exe

    def __repr__(self) -> str:
        return f"Git({str(self.cwd)!r})"

    def _env(self, env: Mapping[str, str] | None, read_only: bool) -> dict[str, str]:
        full = base_env()
        if read_only:
            full["GIT_OPTIONAL_LOCKS"] = "0"
        if env:
            full.update(env)
        return full

    async def run(
        self,
        *args: str,
        input: bytes | None = None,
        env: Mapping[str, str] | None = None,
        check: bool = True,
        read_only: bool = False,
    ) -> GitResult:
        proc = await asyncio.create_subprocess_exec(
            self.git_exe,
            *BASE_CONFIG,
            *args,
            cwd=self.cwd,
            env=self._env(env, read_only),
            stdin=asyncio.subprocess.PIPE if input is not None else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            out, err = await proc.communicate(input)
        except asyncio.CancelledError:
            if proc.returncode is None:
                proc.kill()
                await proc.wait()
            raise
        result = GitResult(tuple(args), proc.returncode or 0, out, err.decode("utf-8", "replace"))
        if check and result.returncode != 0:
            raise GitError(result)
        return result

    async def stream(
        self,
        *args: str,
        sep: bytes = b"\0",
        env: Mapping[str, str] | None = None,
    ) -> AsyncIterator[bytes]:
        """Yield `sep`-separated records from a long-running git command."""
        proc = await asyncio.create_subprocess_exec(
            self.git_exe,
            *BASE_CONFIG,
            *args,
            cwd=self.cwd,
            env=self._env(env, read_only=True),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=1 << 24,
        )
        assert proc.stdout is not None
        buf = b""
        try:
            while True:
                chunk = await proc.stdout.read(1 << 16)
                if not chunk:
                    break
                buf += chunk
                *records, buf = buf.split(sep)
                for rec in records:
                    yield rec
            if buf:
                yield buf
            await proc.wait()
            if proc.returncode:
                assert proc.stderr is not None
                err = (await proc.stderr.read()).decode("utf-8", "replace")
                raise GitError(GitResult(tuple(args), proc.returncode, b"", err))
        finally:
            if proc.returncode is None:
                proc.kill()
                await proc.wait()

    async def rev_parse(self, *args: str) -> str:
        res = await self.run("rev-parse", *args, read_only=True)
        return res.text.strip()

    async def try_rev_parse(self, *args: str) -> str | None:
        res = await self.run("rev-parse", "--verify", "--quiet", *args, check=False, read_only=True)
        return res.text.strip() or None if res.ok else None
