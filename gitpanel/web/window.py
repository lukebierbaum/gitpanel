"""`gitpanel --app`: the web UI in its own desktop window (pywebview), not a browser tab.

GUI toolkits own the main thread, so the server runs on an event loop in a background
thread; closing the window stops it.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import contextlib
import importlib.util
import sys
import threading
from pathlib import Path
from typing import Any

from .server import Server


class _Background:
    """An event loop on a daemon thread, with the started `Server` once it is up."""

    def __init__(self) -> None:
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(
            target=self.loop.run_forever, name="gitpanel-web", daemon=True
        )
        self.thread.start()

    def call(self, coro: Any, timeout: float | None = None) -> Any:
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(timeout)

    def close(self) -> None:
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(5)


def _gui() -> str | None:
    """Skip pywebview's GTK attempt (and its traceback) when only Qt is installed."""
    if sys.platform.startswith("linux") and importlib.util.find_spec("gi") is None:
        return "qt"
    return None


def run(path: Path | str, **kwargs: Any) -> None:
    import webview  # the `app` extra; imported here so --web works without it

    bg = _Background()
    try:
        server: Server = bg.call(Server.start(path, **kwargs))
    except BaseException:
        bg.close()
        raise
    repo = server.webapp.session.active.path
    webview.create_window(
        f"gitpanel — {repo.name}",
        server.url,
        width=1440,
        height=900,
        min_size=(720, 480),
    )
    try:
        webview.start(gui=_gui())  # blocks until the last window closes
    finally:
        with contextlib.suppress(concurrent.futures.TimeoutError, RuntimeError):
            bg.call(server.stop(), timeout=5)
        bg.close()
