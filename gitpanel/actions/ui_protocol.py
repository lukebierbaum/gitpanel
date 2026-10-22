"""The only way actions talk to the user. Implemented by the Textual app and by FakeUi in tests."""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol

Severity = Literal["information", "warning", "error"]


@dataclass(frozen=True)
class Field:
    """One input in a `Ui.form` dialog."""

    name: str
    label: str
    kind: Literal["text", "bool", "choice"] = "text"
    default: Any = ""
    options: Sequence[tuple[str, str]] = ()  # (value, label) for "choice"
    placeholder: str = ""


class Ui(Protocol):
    async def confirm(self, title: str, body: str = "", *, danger: bool = False) -> bool: ...

    async def prompt(
        self, title: str, *, default: str = "", placeholder: str = "", multiline: bool = False
    ) -> str | None: ...

    async def form(
        self,
        title: str,
        fields: Sequence[Field],
        *,
        body: str = "",
        ok_label: str = "OK",
        danger: bool = False,
    ) -> dict[str, Any] | None:
        """Dialog with text / checkbox / choice fields; None when cancelled."""
        ...

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
