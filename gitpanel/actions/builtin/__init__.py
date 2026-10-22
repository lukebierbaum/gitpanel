"""Built-in actions, one module per area."""

from __future__ import annotations

from ..registry import Registry
from . import app, branches, cherry_pick, commit, conflicts, log, rebase, remote, worktrees


def register_builtin(registry: Registry) -> Registry:
    for module in (app, commit, conflicts, log, cherry_pick, rebase, branches, remote, worktrees):
        registry.register_all(module.actions())
    return registry
