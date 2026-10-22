"""Built-in actions, one module per area."""

from __future__ import annotations

from ..registry import Registry
from . import app, branches, commit, log, remote, worktrees


def register_builtin(registry: Registry) -> Registry:
    for module in (app, commit, log, branches, remote, worktrees):
        registry.register_all(module.actions())
    return registry
