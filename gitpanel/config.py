"""User configuration: ~/.config/gitpanel/config.toml (and opt-in per-repo .gitpanel.toml)."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

EXAMPLE = """\
# gitpanel configuration — every key is optional.
theme = "hulk"               # hulk, hulk-light, or any Textual theme (textual-dark, nord, …)

[diff]
side_by_side = false         # start diffs side-by-side
context = 3                  # context lines around changes

[worktrees]
path_template = "../{repo}.worktrees/{branch}"   # relative to the main worktree

[journal]
keep = 200                   # operations kept for undo
max_age_days = 30

[confirm]
destructive = true           # ask before actions that discard work (they're always backed up)

[keys]                       # action id = key, or "key1,key2" for alternatives ("" unbinds); ids are in `?`
# "commit.commit" = "ctrl+s"
# "app.quit" = "ctrl+q"

# [[commands]]               # custom commands (see README)
# id = "open-pr"
# label = "Open pull request"
# key = "ctrl+o"
# context = "branch"         # global | commit | branch | file | worktree
# run = "gh pr view --web {branch}"
# output = "none"            # none | popup | terminal
"""


@dataclass
class Config:
    theme: str = "hulk"
    side_by_side: bool = False
    diff_context: int = 3
    worktree_template: str = "../{repo}.worktrees/{branch}"
    journal_keep: int = 200
    journal_max_age_days: int = 30
    confirm_destructive: bool = True
    keys: dict[str, str | None] = field(default_factory=dict)
    commands: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    source: Path | None = None


def config_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "gitpanel" / "config.toml"


def _merge(cfg: Config, data: dict[str, Any], origin: str) -> None:
    def get(section: str, key: str, kind: type, attr: str) -> None:
        sec = data.get(section, {}) if section else data
        if not isinstance(sec, dict) or key not in sec:
            return
        value = sec[key]
        if kind is int and isinstance(value, bool):
            cfg.errors.append(f"{origin}: {section}.{key} must be a number")
            return
        if not isinstance(value, kind):
            cfg.errors.append(
                f"{origin}: {(section + '.') if section else ''}{key} must be {kind.__name__}"
            )
            return
        setattr(cfg, attr, value)

    get("", "theme", str, "theme")
    get("diff", "side_by_side", bool, "side_by_side")
    get("diff", "context", int, "diff_context")
    get("worktrees", "path_template", str, "worktree_template")
    get("journal", "keep", int, "journal_keep")
    get("journal", "max_age_days", int, "journal_max_age_days")
    get("confirm", "destructive", bool, "confirm_destructive")
    keys = data.get("keys", {})
    if isinstance(keys, dict):
        for action_id, key in keys.items():
            if isinstance(key, str):
                cfg.keys[action_id] = key or None
            else:
                cfg.errors.append(f"{origin}: keys.{action_id} must be a string")
    commands = data.get("commands", [])
    if isinstance(commands, list):
        cfg.commands += [c for c in commands if isinstance(c, dict)]


def load(
    path: Path | None = None, repo_root: Path | None = None, trust_repo: bool = False
) -> Config:
    cfg = Config()
    path = path or config_path()
    if path.exists():
        cfg.source = path
        try:
            _merge(cfg, tomllib.loads(path.read_text()), str(path))
        except tomllib.TOMLDecodeError as e:
            cfg.errors.append(f"{path}: {e}")
    if repo_root is not None:
        repo_file = repo_root / ".gitpanel.toml"
        if repo_file.exists():
            if trust_repo:
                try:
                    _merge(cfg, tomllib.loads(repo_file.read_text()), str(repo_file))
                except tomllib.TOMLDecodeError as e:
                    cfg.errors.append(f"{repo_file}: {e}")
            else:
                cfg.errors.append(
                    f"{repo_file} ignored — run with --trust-repo-config to load it "
                    "(it can define commands)"
                )
    return cfg


def write_example(path: Path | None = None) -> Path:
    path = path or config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(EXAMPLE)
    return path
