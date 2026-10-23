"""Runs the GitHub CLI (`gh`). Every call is recorded in the Console like git calls."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..git.console import CONSOLE

ENV = {
    "GH_PROMPT_DISABLED": "1",
    "GH_NO_UPDATE_NOTIFIER": "1",
    "GH_PAGER": "cat",
    "PAGER": "cat",
    "NO_COLOR": "1",
    "CLICOLOR": "0",
}


@dataclass(frozen=True)
class GhResult:
    args: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


class ForgeError(Exception):
    """A `gh` failure with a human explanation and, when known, how to fix it."""

    def __init__(self, message: str, hint: str = "", result: GhResult | None = None) -> None:
        super().__init__(message)
        self.hint = hint
        self.result = result

    @property
    def details(self) -> str:
        parts = [str(self)]
        if self.hint:
            parts.append(self.hint)
        if self.result is not None:
            parts.append(f"$ gh {' '.join(self.result.args)}\n{self.result.stderr.strip()}")
        return "\n\n".join(parts)


def _explain(result: GhResult) -> ForgeError:
    err = result.stderr.strip()
    low = err.lower()
    if "gh auth login" in low or "not logged in" in low or "authentication" in low:
        return ForgeError("Not logged in to GitHub", "Run `gh auth login` in a terminal.", result)
    if "set-default" in low or "multiple remotes" in low:
        return ForgeError(
            "gh doesn't know which remote is the GitHub repository",
            "Run `gh repo set-default` in this repository.",
            result,
        )
    if "rate limit" in low:
        return ForgeError("GitHub API rate limit reached", "Wait a few minutes.", result)
    if "no git remotes" in low or "could not determine" in low or "none of the git remotes" in low:
        return ForgeError("This repository has no GitHub remote", "", result)
    if "could not resolve to a pullrequest" in low or "no pull requests found" in low:
        return ForgeError("Pull request not found", "", result)
    first = err.splitlines()[0] if err else f"gh exited with {result.returncode}"
    return ForgeError(first, "", result)


class Gh:
    def __init__(self, cwd: Path, exe: str = "gh") -> None:
        self.cwd = Path(cwd)
        self.exe = exe

    @staticmethod
    def available(exe: str = "gh") -> bool:
        return shutil.which(exe) is not None

    async def run(
        self, *args: str, input: str | None = None, check: bool = True, read_only: bool = True
    ) -> GhResult:
        data = input.encode() if input is not None else None
        entry = CONSOLE.start(args, self.cwd, read_only=read_only, stdin=data, kind="gh")
        try:
            proc = await asyncio.create_subprocess_exec(
                self.exe,
                *args,
                cwd=self.cwd,
                env={**os.environ, **ENV},
                stdin=asyncio.subprocess.PIPE if data is not None else asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as e:
            CONSOLE.finish(entry, None, stderr=str(e))
            raise ForgeError(
                "The GitHub CLI (gh) isn't installed", "Install it from https://cli.github.com"
            ) from e
        except asyncio.CancelledError:
            CONSOLE.finish(entry, None, cancelled=True)
            raise
        try:
            out, err = await proc.communicate(data)
        except asyncio.CancelledError:
            if proc.returncode is None:
                proc.kill()
                await proc.wait()
            CONSOLE.finish(entry, None, cancelled=True)
            raise
        result = GhResult(
            tuple(args),
            proc.returncode or 0,
            out.decode("utf-8", "replace"),
            err.decode("utf-8", "replace"),
        )
        CONSOLE.finish(entry, result.returncode, result.stdout, result.stderr)
        if check and not result.ok:
            raise _explain(result)
        return result

    async def json(self, *args: str, input: str | None = None) -> Any:
        res = await self.run(*args, input=input)
        text = res.stdout.strip()
        return json.loads(text) if text else None
