"""aiohttp server for the browser UI: one RepoSession shared by every open tab.

Security: the server listens on localhost only, and a WebSocket can be opened from any web
page, so every request must carry a random per-run token (`/?t=…` sets a SameSite cookie)
and WebSocket upgrades must come from this server's own origin.
"""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import json
import logging
import secrets
import webbrowser
from datetime import timedelta
from pathlib import Path
from typing import Any

from aiohttp import WSMsgType, web

from ..actions.builtin import register_builtin
from ..actions.custom import register_custom
from ..actions.registry import Registry
from ..config import Config
from ..config import load as load_config
from ..git.runner import GitError
from ..git.session import ChangeKind, RepoSession
from .api import Connection, RpcError
from .bridge import Channel

log = logging.getLogger(__name__)
STATIC = Path(__file__).parent / "static"
COOKIE = "gitpanel_token"


class WebApp:
    """Owns the session, registry and config; hands each tab a `Connection`."""

    def __init__(
        self,
        session: RepoSession,
        *,
        config: Config | None = None,
        registry: Registry | None = None,
        token: str | None = None,
    ) -> None:
        self.session = session
        self.config = config or Config()
        self.registry = registry or register_builtin(Registry())
        self.token = token or secrets.token_urlsafe(24)
        self.connections: set[Connection] = set()
        self._unsubscribe = session.subscribe(self._on_change)

    @classmethod
    async def open(
        cls,
        path: Path | str,
        *,
        config: Config | None = None,
        config_path: Path | None = None,
        trust_repo_config: bool = False,
        token: str | None = None,
    ) -> WebApp:
        session = await RepoSession.open(path)
        registry = register_builtin(Registry())
        if config is None:
            config = load_config(config_path, session.active.path, trust_repo_config)
            config.errors += [f"commands: {e}" for e in register_custom(registry, config.commands)]
            try:
                registry.rebind(config.keys)
            except KeyError as e:
                ids = {a.id for a in registry.all()}
                registry.rebind({k: v for k, v in config.keys.items() if k in ids})
                config.errors.append(f"[keys]: {e.args[0]}")
        return cls(session, config=config, registry=registry, token=token)

    def _on_change(self, kinds: ChangeKind) -> None:
        names = [k.name for k in ChangeKind if k in kinds]  # single flags only
        for conn in list(self.connections):
            conn.ch.send({"type": "changed", "kinds": names})

    # -- HTTP --------------------------------------------------------------------------
    def authorized(self, request: web.Request) -> bool:
        given = request.query.get("t") or request.cookies.get(COOKIE) or ""
        return hmac.compare_digest(given.encode(), self.token.encode())

    async def index(self, request: web.Request) -> web.StreamResponse:
        if not self.authorized(request):
            return web.Response(
                status=403, text="gitpanel: open the URL printed in the terminal (it has a token)."
            )
        resp = web.FileResponse(STATIC / "index.html")
        resp.headers["Cache-Control"] = "no-store"
        resp.set_cookie(COOKIE, self.token, httponly=True, samesite="Strict")
        return resp

    async def websocket(self, request: web.Request) -> web.StreamResponse:
        origin = request.headers.get("Origin", "")
        if not self.authorized(request) or origin != f"{request.scheme}://{request.host}":
            return web.Response(status=403, text="forbidden")
        ws = web.WebSocketResponse(heartbeat=30, max_msg_size=16 * 1024 * 1024)
        await ws.prepare(request)
        ch = Channel()
        conn = Connection(self, ch)
        self.connections.add(conn)
        writer = asyncio.create_task(self._write(ws, ch))
        tasks: set[asyncio.Task[None]] = set()
        ch.send({"type": "hello", "errors": list(self.config.errors)})
        try:
            async for msg in ws:
                if msg.type != WSMsgType.TEXT:
                    continue
                try:
                    data = json.loads(msg.data)
                except ValueError:
                    continue
                if data.get("type") == "answer":
                    ch.answer(int(data["id"]), data.get("value"))
                elif data.get("type") == "call":
                    # each call runs on its own so an action waiting on a dialog doesn't
                    # block the answer that unblocks it
                    t = asyncio.create_task(self._call(conn, data))
                    tasks.add(t)
                    t.add_done_callback(tasks.discard)
        finally:
            self.connections.discard(conn)
            ch.close()
            await writer
        return ws

    async def _call(self, conn: Connection, data: dict[str, Any]) -> None:
        cid = data.get("id")
        try:
            result = await conn.call(str(data.get("method")), data.get("params") or {})
            conn.ch.send({"type": "result", "id": cid, "result": result})
        except (RpcError, GitError) as e:
            conn.ch.send({"type": "result", "id": cid, "error": str(e)})
        except Exception as e:  # noqa: BLE001 - report, keep serving
            log.exception("web call %s failed", data.get("method"))
            conn.ch.send({"type": "result", "id": cid, "error": f"{type(e).__name__}: {e}"})

    @staticmethod
    async def _write(ws: web.WebSocketResponse, ch: Channel) -> None:
        while True:
            msg = await ch.queue.get()
            if msg is None or ws.closed:
                return
            with contextlib.suppress(ConnectionResetError):
                await ws.send_str(json.dumps(msg, default=str))

    def make_app(self) -> web.Application:
        app = web.Application()
        app.router.add_get("/", self.index)
        app.router.add_get("/ws", self.websocket)
        app.router.add_static("/static/", STATIC)
        app.on_response_prepare.append(_revalidate)
        return app

    def close(self) -> None:
        self._unsubscribe()
        self.session.stop_watching()


async def _revalidate(request: web.Request, response: web.StreamResponse) -> None:
    """Static files change with gitpanel upgrades: always revalidate (cheap: 304s)."""
    if request.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache"


async def serve(
    path: Path | str,
    *,
    host: str = "127.0.0.1",
    port: int = 0,
    open_browser: bool = True,
    config_path: Path | None = None,
    trust_repo_config: bool = False,
) -> None:
    webapp = await WebApp.open(path, config_path=config_path, trust_repo_config=trust_repo_config)
    webapp.session.start_watching()
    cfg = webapp.config
    asyncio.get_running_loop().create_task(
        webapp.session.journal.prune(cfg.journal_keep, timedelta(days=cfg.journal_max_age_days))
    )
    runner = web.AppRunner(webapp.make_app(), access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    bound = runner.addresses[0][1] if runner.addresses else port
    url = f"http://{host}:{bound}/?t={webapp.token}"
    print(
        f"gitpanel web UI for {webapp.session.active.path}\n  {url}\n(ctrl+c to stop)", flush=True
    )
    if open_browser:
        webbrowser.open(url)
    try:
        await asyncio.Event().wait()
    finally:
        webapp.close()
        await runner.cleanup()


def run(path: Path | str, **kwargs: Any) -> None:
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(serve(path, **kwargs))
