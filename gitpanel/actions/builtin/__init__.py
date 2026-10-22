"""Built-in actions, one module per area."""

from __future__ import annotations

from ..registry import Registry
from . import (
    app,
    branches,
    changelists,
    cherry_pick,
    commit,
    conflicts,
    files,
    log,
    patches,
    rebase,
    remote,
    shelf,
    worktrees,
)


def register_builtin(registry: Registry) -> Registry:
    for module in (
        app,
        commit,
        changelists,
        conflicts,
        files,
        shelf,
        log,
        cherry_pick,
        rebase,
        patches,
        branches,
        remote,
        worktrees,
    ):
        registry.register_all(module.actions())
    return registry
