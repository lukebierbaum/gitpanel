"""PyCharm changelists: named groups of changed files, per worktree."""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT = "Changes"


@dataclass
class Changelists:
    path: Path
    lists: list[str] = field(default_factory=lambda: [DEFAULT])
    active: str = DEFAULT
    files: dict[str, str] = field(default_factory=dict)  # path → list name

    @classmethod
    def load(cls, path: Path) -> Changelists:
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            return cls(path)
        lists = [n for n in data.get("lists", []) if isinstance(n, str)] or [DEFAULT]
        if DEFAULT not in lists:
            lists.insert(0, DEFAULT)
        active = data.get("active", DEFAULT)
        return cls(
            path,
            lists,
            active if active in lists else DEFAULT,
            {k: v for k, v in data.get("files", {}).items() if v in lists},
        )

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps({"lists": self.lists, "active": self.active, "files": self.files}, indent=1)
        )

    def list_for(self, path: str) -> str:
        return self.files.get(path, self.active)

    def group(self, paths: Iterable[str]) -> dict[str, list[str]]:
        """Changed paths grouped by changelist (every list present, possibly empty)."""
        out: dict[str, list[str]] = {n: [] for n in self.lists}
        for p in paths:
            out.setdefault(self.list_for(p), []).append(p)
        return out

    def add(self, name: str) -> None:
        name = name.strip()
        if not name or name in self.lists:
            raise ValueError(f"Changelist {name!r} already exists or is empty")
        self.lists.append(name)

    def assign(self, paths: Sequence[str], name: str) -> None:
        if name not in self.lists:
            self.add(name)
        for p in paths:
            self.files[p] = name

    def rename(self, old: str, new: str) -> None:
        if old == DEFAULT:
            raise ValueError("The default changelist can't be renamed")
        if new in self.lists:
            raise ValueError(f"Changelist {new!r} already exists")
        self.lists[self.lists.index(old)] = new
        self.files = {p: (new if n == old else n) for p, n in self.files.items()}
        if self.active == old:
            self.active = new

    def remove(self, name: str) -> None:
        if name == DEFAULT:
            raise ValueError("The default changelist can't be removed")
        self.lists.remove(name)
        self.files = {p: n for p, n in self.files.items() if n != name}
        if self.active == name:
            self.active = DEFAULT

    def set_active(self, name: str, changed: Iterable[str] = ()) -> None:
        """New changes go to `name`; files already changed stay where they are."""
        if name not in self.lists:
            raise ValueError(f"No changelist {name!r}")
        for p in changed:
            self.files.setdefault(p, self.active)
        self.active = name

    def prune(self, changed: Iterable[str]) -> None:
        """Forget assignments for files that no longer have changes."""
        keep = set(changed)
        self.files = {p: n for p, n in self.files.items() if p in keep}
