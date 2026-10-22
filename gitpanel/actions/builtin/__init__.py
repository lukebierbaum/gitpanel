"""Built-in actions, one module per area."""

from __future__ import annotations

from ..registry import Registry
from . import app, branches, cherry_pick, commit, log, remote, worktrees


def register_builtin(registry: Registry) -> Registry:
    for module in (app, commit, log, cherry_pick, branches, remote, worktrees):
        registry.register_all(module.actions())
    return registry
