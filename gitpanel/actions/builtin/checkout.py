"""Checkout with PyCharm's fallback: Smart or Force Checkout when local changes block it."""

from __future__ import annotations

from ...git import refs as R
from ...git.journal import snapshot_worktree
from ...git.runner import GitError
from ..registry import ActionEnv, ActionError

CHOICES = [
    ("smart", "Smart Checkout — carry your local changes over (stash, switch, unstash)"),
    ("force", "Force Checkout — discard your local changes (undo with z)"),
]


async def force_checkout(env: ActionEnv, target: str, *, detach: bool = False) -> None:
    git = env.session.git
    if env.entry is not None and env.entry.backup is None:
        # back up the working tree so z restores the discarded changes
        env.entry.backup = await snapshot_worktree(git, f"force checkout {target}")
    await R.checkout(git, target, mode="force", detach=detach)


async def checkout_with_fallback(
    env: ActionEnv, target: str, label: str, *, detach: bool = False
) -> bool:
    """Check out `target`; if local changes block it, offer smart/force. True when switched."""
    git = env.session.git
    try:
        await R.checkout(git, target, detach=detach)
        return True
    except GitError as e:
        if not R.is_local_changes_error(e):
            raise
        files = [ln.strip() for ln in e.result.stderr.splitlines() if ln.startswith("\t")]
        shown = ", ".join(files[:4]) + (f" (+{len(files) - 4})" if len(files) > 4 else "")
        choice = await env.ui.choose(
            f"Local changes{' in ' + shown if shown else ''} would be overwritten by {label}",
            CHOICES,
        )
    if choice not in ("smart", "force"):
        return False
    if choice == "force":
        await force_checkout(env, target, detach=detach)
        return True
    res = await R.smart_checkout(git, target, detach=detach)
    if not res.ok:
        if "CONFLICT" in res.text + res.stderr:
            env.ui.notify(
                "Switched, but your changes conflict with it — resolve them; they're also kept "
                "in the stash",
                severity="warning",
            )
            return True
        raise ActionError(f"Switched, but restoring your changes failed: {res.stderr.strip()}")
    return True
