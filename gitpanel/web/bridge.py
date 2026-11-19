"""The `Ui` protocol over a WebSocket: dialogs become ask/answer round trips.

Messages server → browser:
  {"type": "ask", "id": n, "kind": "confirm"|"prompt"|"form"|"choose"|"output", ...}
  {"type": "output", "id": n, "line": str} / {"type": "output_done", "id": n, "code": int}
  {"type": "notify" | "error" | "post" | "open_screen", ...}
Browser → server: {"type": "answer", "id": n, "value": ...}
"""

from __future__ import annotations

import asyncio
import contextlib
import os
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from pathlib import Path
from typing import Any

from ..actions.ui_protocol import Field, Severity
from .serialize import jsonable

Send = Callable[[dict[str, Any]], None]


class Channel:
    """Outgoing message queue + pending questions for one browser tab."""

    def __init__(self) -> None:
        self.queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        self._pending: dict[int, asyncio.Future[Any]] = {}
        self._next = 1
        self.closed = False

    def send(self, msg: dict[str, Any]) -> None:
        if not self.closed:
            self.queue.put_nowait(msg)

    async def ask(self, kind: str, fallback: Any, /, **payload: Any) -> Any:
        """Send a question and wait for the browser's answer (`fallback` if the tab goes away)."""
        if self.closed:
            return fallback
        qid = self._next
        self._next += 1
        fut: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        self._pending[qid] = fut
        self.send({"type": "ask", "id": qid, "kind": kind, **jsonable(payload)})
        try:
            value = await fut
        finally:
            self._pending.pop(qid, None)
        return fallback if value is _GONE else value

    def new_id(self) -> int:
        qid = self._next
        self._next += 1
        return qid

    def expect(self, qid: int) -> asyncio.Future[Any]:
        fut: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        self._pending[qid] = fut
        return fut

    def answer(self, qid: int, value: Any) -> None:
        fut = self._pending.pop(qid, None)
        if fut is not None and not fut.done():
            fut.set_result(value)

    def close(self) -> None:
        self.closed = True
        for fut in self._pending.values():
            if not fut.done():
                fut.set_result(_GONE)
        self.queue.put_nowait(None)


_GONE = object()


class WebUi:
    """Implements `gitpanel.actions.ui_protocol.Ui` for one browser tab."""

    def __init__(self, channel: Channel) -> None:
        self.ch = channel

    async def confirm(self, title: str, body: str = "", *, danger: bool = False) -> bool:
        return bool(await self.ch.ask("confirm", False, title=title, body=body, danger=danger))

    async def prompt(
        self, title: str, *, default: str = "", placeholder: str = "", multiline: bool = False
    ) -> str | None:
        value = await self.ch.ask(
            "prompt",
            None,
            title=title,
            default=default,
            placeholder=placeholder,
            multiline=multiline,
        )
        return None if value is None else str(value)

    async def form(
        self,
        title: str,
        fields: Sequence[Field],
        *,
        body: str = "",
        ok_label: str = "OK",
        danger: bool = False,
    ) -> dict[str, Any] | None:
        value = await self.ch.ask(
            "form",
            None,
            title=title,
            fields=list(fields),
            body=body,
            ok_label=ok_label,
            danger=danger,
        )
        if not isinstance(value, dict):
            return None
        # browsers send strings/bools; keep only the declared names
        return {f.name: value.get(f.name, f.default) for f in fields}

    async def choose(self, title: str, options: Sequence[tuple[str, str]]) -> str | None:
        value = await self.ch.ask("choose", None, title=title, options=[list(o) for o in options])
        return None if value is None else str(value)

    def notify(self, message: str, *, severity: Severity = "information") -> None:
        self.ch.send({"type": "notify", "message": message, "severity": severity})

    def show_error(self, title: str, details: str) -> None:
        self.ch.send({"type": "error", "title": title, "details": details})

    async def show_output(self, title: str, stream: AsyncIterator[str]) -> int:
        return await self._stream(title, stream)

    async def _stream(
        self,
        title: str,
        stream: AsyncIterator[str],
        code_of: Callable[[], Awaitable[int]] | None = None,
    ) -> int:
        qid = self.ch.new_id()
        closed = self.ch.expect(qid)
        self.ch.send({"type": "ask", "id": qid, "kind": "output", "title": title})
        code = 0
        try:
            async for line in stream:
                self.ch.send({"type": "output", "id": qid, "line": line})
        except Exception as e:  # show, don't crash the action
            self.ch.send({"type": "output", "id": qid, "line": f"error: {e}"})
            code = 1
        if code_of is not None:
            code = await code_of()
        self.ch.send({"type": "output_done", "id": qid, "code": code})
        if not self.ch.closed:
            await closed
        return code

    async def run_in_terminal(self, argv: Sequence[str], cwd: Path) -> int:
        """No terminal to suspend to: run without stdin and stream the output into a dialog.

        Git is told not to prompt, so a credential request fails fast instead of hanging.
        """
        env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "never"}
        proc = await asyncio.create_subprocess_exec(
            *argv,
            cwd=cwd,
            env=env,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        assert proc.stdout is not None
        out = proc.stdout

        async def lines() -> AsyncIterator[str]:
            async for raw in out:
                yield raw.decode("utf-8", "replace").rstrip("\n")

        try:
            return await self._stream(f"$ {' '.join(argv)}", lines(), proc.wait)
        finally:
            if proc.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    proc.kill()

    def open_screen(self, name: str, **kwargs: Any) -> None:
        self.ch.send({"type": "open_screen", "name": name, "args": jsonable(kwargs)})

    def post(self, event: str, **data: Any) -> None:
        self.ch.send({"type": "post", "event": event, "data": jsonable(data)})
