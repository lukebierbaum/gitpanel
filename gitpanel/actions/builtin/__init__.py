"""Built-in actions, one module per area."""

from __future__ import annotations

from ..registry import Registry
from . import app, commit, log


def register_builtin(registry: Registry) -> Registry:
    for module in (app, commit, log):
        registry.register_all(module.actions())
    return registry
