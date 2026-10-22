"""Shared helper: run a network git operation, offering a terminal retry on auth errors."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from typing import TypeVar

from ...git.remote import is_auth_error
from ...git.runner import GitError
from ..registry import ActionEnv

T = TypeVar("T")


async def with_auth_retry(
    env: ActionEnv, call: Callable[[], Awaitable[T]], argv: Sequence[str]
) -> T | None:
    """Run `call`; if git needs credentials, offer to rerun `git argv` in the real terminal."""
    try:
        return await call()
    except GitError as e:
        if not is_auth_error(e):
            raise
        ok = await env.ui.confirm(
            "Authentication required",
            f"{e}\n\nRun `git {' '.join(argv)}` in the terminal so you can enter credentials?",
        )
        if ok:
            code = await env.ui.run_in_terminal(["git", *argv], env.session.active.path)
            if code != 0:
                env.ui.notify(f"git {argv[0]} exited with {code}", severity="error")
        return None
