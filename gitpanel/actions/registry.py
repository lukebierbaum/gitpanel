"""Action registry and dispatcher. See docs/architecture.md §6."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from ..git.journal import JournalEntry, ResetMode, UndoRefused
from ..git.runner import GitError
from ..git.session import RepoSession
from .context import Scope, SelectionContext
from .ui_protocol import Ui

log = logging.getLogger(__name__)


class ActionError(Exception):
    """A user-facing failure raised by an action (shown as an error notification)."""


@dataclass
class ActionEnv:
    ctx: SelectionContext
    ui: Ui
    session: RepoSession
    entry: JournalEntry | None = None  # set for mutating actions; `entry.op` may be refined
    config: Any = None  # gitpanel.config.Config (None in tests → defaults)

    def setting(self, name: str, default: Any) -> Any:
        return getattr(self.config, name, default) if self.config is not None else default


def _always(_ctx: SelectionContext) -> bool:
    return True


@dataclass(frozen=True)
class Action:
    id: str
    label: str
    run: Callable[[ActionEnv], Awaitable[Any]]
    scopes: frozenset[Scope] = frozenset({Scope.GLOBAL})
    when: Callable[[SelectionContext], bool] = _always
    key: str | None = None
    group: str = ""
    screens: frozenset[str] | None = None  # None = any screen
    mutating: bool = False
    destructive: bool = False
    reset_mode: ResetMode = "keep"
    confirm: str | None = None  # may use {placeholders}
    silent_backup: bool = False  # destructive (backed up) but safe enough not to ask
    confirm_title: str | None = None
    paths: Callable[[SelectionContext], list[str] | None] | None = None
    show_in_palette: bool = True
    description: str = ""
    label_fn: Callable[[SelectionContext], str] | None = None  # dynamic label (e.g. undo target)

    def label_for(self, ctx: SelectionContext) -> str:
        if self.label_fn is None:
            return self.label
        try:
            return self.label_fn(ctx)
        except Exception:
            return self.label

    def applies(self, ctx: SelectionContext) -> bool:
        if self.screens is not None and ctx.screen not in self.screens:
            return False
        if Scope.GLOBAL not in self.scopes and ctx.focus not in self.scopes:
            return False
        try:
            return bool(self.when(ctx))
        except Exception:  # a predicate must never break the UI
            log.exception("when() of %s failed", self.id)
            return False


@dataclass
class Registry:
    _actions: dict[str, Action] = field(default_factory=dict)
    _key_overrides: dict[str, str | None] = field(default_factory=dict)

    def register(self, action: Action) -> Action:
        if action.id in self._actions:
            raise ValueError(f"duplicate action id {action.id!r}")
        self._actions[action.id] = action
        return action

    def register_all(self, actions: Iterable[Action]) -> None:
        for a in actions:
            self.register(a)

    def get(self, action_id: str) -> Action:
        return self._actions[action_id]

    def all(self) -> list[Action]:
        return list(self._actions.values())

    def key_for(self, action: Action) -> str | None:
        if action.id in self._key_overrides:
            return self._key_overrides[action.id]
        return action.key

    def rebind(self, overrides: Mapping[str, str | None]) -> None:
        unknown = set(overrides) - set(self._actions)
        if unknown:
            raise KeyError(f"unknown action ids: {', '.join(sorted(unknown))}")
        self._key_overrides.update(overrides)

    def available(self, ctx: SelectionContext) -> list[Action]:
        return [a for a in self._actions.values() if a.applies(ctx)]

    def for_key(self, ctx: SelectionContext, key: str) -> Action | None:
        matches = [a for a in self.available(ctx) if self.key_for(a) == key]
        if not matches:
            return None
        # prefer the most specific: screen-restricted, then non-global scopes
        matches.sort(key=lambda a: (a.screens is None, Scope.GLOBAL in a.scopes))
        return matches[0]


class Dispatcher:
    def __init__(self, registry: Registry, ui: Ui, config: Any = None) -> None:
        self.registry = registry
        self.ui = ui
        self.config = config

    async def dispatch_id(self, action_id: str, ctx: SelectionContext) -> Any:
        return await self.dispatch(self.registry.get(action_id), ctx)

    async def dispatch(self, action: Action, ctx: SelectionContext) -> Any:
        if not action.applies(ctx):
            return None
        ask_destructive = getattr(self.config, "confirm_destructive", True)
        if action.confirm or (action.destructive and not action.silent_backup and ask_destructive):
            body = (action.confirm or "This cannot be undone outside gitpanel.").format_map(
                _SafeDict(ctx.placeholders())
            )
            ok = await self.ui.confirm(
                action.confirm_title or action.label, body, danger=action.destructive
            )
            if not ok:
                return None
        env = ActionEnv(ctx, self.ui, ctx.session, config=self.config)
        try:
            if action.mutating:
                paths = action.paths(ctx) if action.paths else None
                async with ctx.session.journal.mutation(
                    action.label,
                    destructive=action.destructive,
                    paths=paths,
                    reset_mode=action.reset_mode,
                ) as entry:
                    env.entry = entry
                    return await action.run(env)
            return await action.run(env)
        except GitError as e:
            self.ui.notify(str(e), severity="error")
            self.ui.show_error(action.label, e.details)
        except UndoRefused as e:
            self.ui.notify(e.reason, severity="warning")
        except ActionError as e:
            self.ui.notify(str(e), severity="error")
        return None


class _SafeDict(dict[str, str]):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"
