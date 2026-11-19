"""Browser UI server: auth, JSON calls, and actions driven over the WebSocket (real git repos)."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

pytest.importorskip("aiohttp")

from aiohttp import WSServerHandshakeError  # noqa: E402
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

from gitpanel.config import Config  # noqa: E402
from gitpanel.git.session import RepoSession  # noqa: E402
from gitpanel.web.api import norm_key  # noqa: E402
from gitpanel.web.serialize import jsonable  # noqa: E402
from gitpanel.web.server import WebApp  # noqa: E402

TOKEN = "test-token"


class Tab:
    """A fake browser tab: sends calls, records events, answers dialogs from `answers`."""

    def __init__(self, ws: Any) -> None:
        self.ws = ws
        self.events: list[dict[str, Any]] = []
        self.answers: dict[str, Any] = {}
        self._n = 0

    async def call(self, method: str, **params: Any) -> Any:
        self._n += 1
        cid = self._n
        await self.ws.send_json({"type": "call", "id": cid, "method": method, "params": params})
        while True:
            msg = await asyncio.wait_for(self.ws.receive_json(), 10)
            if msg["type"] == "result" and msg["id"] == cid:
                if "error" in msg:
                    raise AssertionError(msg["error"])
                return msg["result"]
            self.events.append(msg)
            if msg["type"] == "ask":
                value = self.answers.get(msg["kind"])
                await self.ws.send_json({"type": "answer", "id": msg["id"], "value": value})

    def asks(self, kind: str) -> list[dict[str, Any]]:
        return [e for e in self.events if e["type"] == "ask" and e["kind"] == kind]


@pytest.fixture
async def server(repo):
    webapp = WebApp(await RepoSession.open(repo.path), config=Config(), token=TOKEN)
    client = TestClient(TestServer(webapp.make_app()))
    await client.start_server()
    yield client
    await client.close()
    webapp.close()


async def open_tab(client: TestClient, *, origin: str | None = None) -> Tab:
    origin = origin or f"http://{client.host}:{client.port}"
    ws = await client.ws_connect(f"/ws?t={TOKEN}", headers={"Origin": origin})
    tab = Tab(ws)
    hello = await ws.receive_json()
    assert hello["type"] == "hello"
    return tab


async def test_page_and_socket_need_the_token(server):
    assert (await server.get("/")).status == 403
    ok = await server.get(f"/?t={TOKEN}")
    assert ok.status == 200
    assert "gitpanel_token" in ok.cookies
    with pytest.raises(WSServerHandshakeError):
        await server.ws_connect(
            "/ws?t=wrong", headers={"Origin": f"http://{server.host}:{server.port}"}
        )
    with pytest.raises(WSServerHandshakeError):  # another site's page can't drive the repo
        await open_tab(server, origin="http://evil.example")


async def test_log_page_has_graph_edges_and_markers(server, repo):
    base = repo.head()
    repo.branch("feature")
    repo.commit({"a.txt": "main\n"}, "on main")
    repo.checkout("feature")
    repo.commit({"b.txt": "feature\n"}, "on feature")
    repo.checkout("main")
    repo.merge("feature")
    tab = await open_tab(server)

    page = await tab.call("log.page", filter={}, offset=0, gen=1)

    rows = page["rows"]
    assert [r["subject"] for r in rows][-1] == "initial"
    tip = rows[0]
    assert tip["parents"] and len(tip["parents"]) == 2
    assert sorted(e[0] for e in tip["graph"]["edges"]) == ["out", "out"]
    assert any(m["kind"] == "head" and m["label"] == "main" for m in tip["markers"])
    assert rows[-1]["sha"] == base
    branches = await tab.call("branches.list")
    assert {b["short"] for b in branches["local"]} == {"main", "feature"}
    assert branches["current"] == "main"


async def test_prompt_dialog_round_trip_then_undo(server, repo):
    sha = repo.head()
    tab = await open_tab(server)
    await tab.call("log.page", filter={}, offset=0, gen=1)
    tab.answers["prompt"] = "topic"

    ctx = {"screen": "log", "focus": "commit", "commits": [sha]}
    await tab.call("actions.run", id="log.new_branch", ctx=ctx)

    assert tab.asks("prompt")[0]["title"].startswith("New branch from")
    assert repo.git("branch", "--show-current").strip() == "topic"
    state = await tab.call("repo.state")
    assert state["branch"] == "topic" and state["undo"] == "New branch topic"

    await tab.call("actions.run", id="app.undo", ctx={"screen": "log"})
    assert repo.git("branch", "--show-current").strip() == "main"
    assert "topic" not in repo.git("branch", "--list", "topic")


async def test_cancelled_prompt_changes_nothing(server, repo):
    tab = await open_tab(server)
    await tab.call("log.page", filter={}, offset=0, gen=1)
    tab.answers["prompt"] = None
    ctx = {"screen": "log", "focus": "commit", "commits": [repo.head()]}
    await tab.call("actions.run", id="log.new_branch", ctx=ctx)
    assert repo.git("branch", "--format=%(refname:short)").split() == ["main"]


async def test_stage_one_hunk_from_a_browser_selection(server, repo):
    lines = [f"line {i}\n" for i in range(40)]
    repo.commit({"f.txt": "".join(lines)}, "forty lines")
    lines[2] = "line 2 changed\n"
    lines[35] = "line 35 changed\n"
    repo.write("f.txt", "".join(lines))
    tab = await open_tab(server)

    status = await tab.call("changes.status")
    assert [f["path"] for f in status["unstaged"]] == ["f.txt"]
    diff = await tab.call("diff.file", source={"kind": "unstaged"}, path="f.txt")
    assert len(diff["hunks"]) == 2 and diff["partial"]
    ctx = {
        "screen": "commit",
        "focus": "hunk",
        "files": [{"group": "unstaged", "path": "f.txt"}],
        "patch": {"source": {"kind": "unstaged"}, "path": "f.txt", "hunks": [1], "lines": []},
    }
    await tab.call("actions.run", id="commit.stage", ctx=ctx)

    staged = repo.staged_diff()
    assert "+line 35 changed" in staged and "line 2 changed" not in staged
    assert "+line 2 changed" in repo.unstaged_diff()
    status = await tab.call("changes.status")
    assert [f["path"] for f in status["staged"]] == ["f.txt"]


async def test_commit_from_the_form_and_events(server, repo):
    repo.write("new.txt", "x\n")
    repo.git("add", "new.txt")
    tab = await open_tab(server)
    ctx = {"screen": "commit", "form": {"message": "Add new.txt"}}

    await tab.call("actions.run", id="commit.commit", ctx=ctx)

    assert repo.git("log", "-1", "--format=%s").strip() == "Add new.txt"
    kinds = [e for e in tab.events if e["type"] in ("post", "notify")]
    assert {"type": "post", "event": "commit.done", "data": {}} in kinds
    await asyncio.sleep(0)  # the "changed" broadcast is queued before the result
    assert any(e["type"] == "changed" and "HEAD" in e["kinds"] for e in tab.events)


async def test_key_dispatch_uses_the_registry(server, repo):
    repo.write("README.md", "changed\n")
    tab = await open_tab(server)
    await tab.call("changes.status")
    ctx = {
        "screen": "commit",
        "focus": "file",
        "files": [{"group": "unstaged", "path": "README.md"}],
    }

    assert await tab.call("actions.key", key="space", ctx=ctx) is True  # space = stage here
    assert "README.md" in repo.staged_diff()
    assert await tab.call("actions.key", key="ctrl+alt+shift+q", ctx=ctx) is False


def test_norm_key_and_jsonable():
    assert norm_key("ctrl+alt+k") == norm_key("alt+ctrl+k") == "alt+ctrl+k"
    assert norm_key("enter") == "enter"
    from pathlib import Path

    assert jsonable({"p": Path("/x"), "t": (1, 2)}) == {"p": "/x", "t": [1, 2]}
