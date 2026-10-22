"""Record of every command gitpanel runs (PyCharm's Git "Console" tab).

`Git.run()/stream()` and custom commands report here; the UI subscribes.
Output is kept truncated so the buffer stays small.
"""

from __future__ import annotations

import contextlib
import shlex
import time
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Literal

MAX_OUTPUT = 64 * 1024  # characters kept per stream
MAX_ENTRIES = 2000

Event = Literal["start", "finish", "clear"]


@dataclass
class ConsoleEntry:
    id: int
    started: datetime
    args: tuple[str, ...]
    cwd: Path
    read_only: bool
    kind: Literal["git", "shell"] = "git"
    stdin_preview: str | None = None
    returncode: int | None = None  # None while running (or cancelled)
    duration: float | None = None
    stdout: str = ""
    stderr: str = ""
    stdout_truncated: bool = False
    cancelled: bool = False
    _t0: float = field(default=0.0, repr=False)

    @property
    def running(self) -> bool:
        return self.duration is None

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    @property
    def command(self) -> str:
        if self.kind == "shell":
            return self.args[-1] if self.args else ""
        return "git " + shlex.join(self.args)


def _clip(text: str) -> tuple[str, bool]:
    if len(text) <= MAX_OUTPUT:
        return text, False
    return text[:MAX_OUTPUT], True


def _preview(stdin: bytes | None) -> str | None:
    if stdin is None:
        return None
    text = stdin.decode("utf-8", "replace")
    if "\0" in text or b"diff --git" in stdin[:20]:
        return f"<{len(stdin)} bytes>"
    first = text.strip().splitlines()[0] if text.strip() else ""
    more = len(text.strip().splitlines()) > 1
    return (first[:120] + (" …" if more or len(first) > 120 else "")) or f"<{len(stdin)} bytes>"


class Console:
    def __init__(self, limit: int = MAX_ENTRIES) -> None:
        self.entries: deque[ConsoleEntry] = deque(maxlen=limit)
        self._subscribers: list[Callable[[Event, ConsoleEntry | None], None]] = []
        self._next = 1

    def subscribe(self, cb: Callable[[Event, ConsoleEntry | None], None]) -> Callable[[], None]:
        self._subscribers.append(cb)

        def unsubscribe() -> None:
            with contextlib.suppress(ValueError):
                self._subscribers.remove(cb)

        return unsubscribe

    def _emit(self, event: Event, entry: ConsoleEntry | None) -> None:
        for cb in list(self._subscribers):
            with contextlib.suppress(Exception):  # a broken listener must never break git
                cb(event, entry)

    def start(
        self,
        args: Sequence[str],
        cwd: Path,
        *,
        read_only: bool,
        stdin: bytes | None = None,
        kind: Literal["git", "shell"] = "git",
    ) -> ConsoleEntry:
        entry = ConsoleEntry(
            id=self._next,
            started=datetime.now(),
            args=tuple(args),
            cwd=Path(cwd),
            read_only=read_only,
            kind=kind,
            stdin_preview=_preview(stdin),
            _t0=time.monotonic(),
        )
        self._next += 1
        self.entries.append(entry)
        self._emit("start", entry)
        return entry

    def finish(
        self,
        entry: ConsoleEntry,
        returncode: int | None,
        stdout: str = "",
        stderr: str = "",
        *,
        cancelled: bool = False,
    ) -> None:
        entry.returncode = returncode
        entry.duration = time.monotonic() - entry._t0
        entry.stdout, entry.stdout_truncated = _clip(stdout)
        entry.stderr, _ = _clip(stderr)
        entry.cancelled = cancelled
        self._emit("finish", entry)

    def clear(self) -> None:
        self.entries.clear()
        self._emit("clear", None)


CONSOLE = Console()
