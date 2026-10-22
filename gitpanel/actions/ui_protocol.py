"""The only way actions talk to the user. Implemented by the Textual app and by FakeUi in tests."""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from pathlib import Path
from typing import Any, Literal, Protocol

Severity = Literal["information", "warning", "error"]


class Ui(Protocol):
    async def confirm(self, title: str, body: str = "", *, danger: bool = False) -> bool: ...

    async def prompt(
        self, title: str, *, default: str = "", placeholder: str = ""
    ) -> str | None: ...

    async def choose(self, title: str, options: Sequence[tuple[str, str]]) -> str | None:
        """`options` are (value, label) pairs; returns the chosen value or None."""
        ...

    def notify(self, message: str, *, severity: Severity = "information") -> None: ...

    def show_error(self, title: str, details: str) -> None: ...

    async def show_output(self, title: str, stream: AsyncIterator[str]) -> int: ...

    async def run_in_terminal(self, argv: Sequence[str], cwd: Path) -> int:
        """Suspend the UI and run a command in the real terminal (credential prompts, shells)."""
        ...

    def open_screen(self, name: str, **kwargs: Any) -> None: ...

    def post(self, event: str, **data: Any) -> None:
        """Tell the UI something happened (e.g. "commit.done") so screens can react."""
        ...
