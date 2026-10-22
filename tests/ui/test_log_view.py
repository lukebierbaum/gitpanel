from __future__ import annotations

from gitpanel.ui.app import GitPanelApp
from gitpanel.ui.screens.log import LogView
from gitpanel.ui.widgets.commit_table import CommitTable

from .test_commit_view import settle


async def test_log_view_details_and_reword(repo):
    repo.commit({"a.txt": "a\n"}, "add a")
    repo.commit({"b.txt": "b\n"}, "add b")
    app = GitPanelApp(repo.path)
    async with app.run_test(size=(150, 45)) as pilot:
        await settle(pilot, app)
        await pilot.press("alt+9")
        await settle(pilot, app)
        assert isinstance(app.view, LogView)
        table = app.query_one(CommitTable)
        assert [r.commit.subject for r in table.rows] == ["add b", "add a", "initial"]
        assert any(m.label == "main" for m in table.rows[0].markers)
        await pilot.press("down")
        await settle(pilot, app)
        details = str(app.query_one("#log-details-text").render())
        assert "add a" in details
        files = app.query_one("#log-files")
        assert files.get_option_at_index(0).id == "a.txt"

        await pilot.press("R")
        await settle(pilot, app)
        area = app.screen.query_one("TextArea")
        area.text = "add a, reworded"
        await pilot.press("ctrl+s")
        await settle(pilot, app)
        assert repo.git("log", "-1", "--format=%s", "HEAD~1").strip() == "add a, reworded"
        await settle(pilot, app)
        assert [r.commit.subject for r in table.rows][1] == "add a, reworded"


async def test_log_filter(repo):
    repo.commit({"a.txt": "a\n"}, "feat: thing")
    repo.commit({"b.txt": "b\n"}, "fix: other")
    app = GitPanelApp(repo.path)
    async with app.run_test(size=(150, 45)) as pilot:
        await settle(pilot, app)
        await pilot.press("alt+9")
        await settle(pilot, app)
        await pilot.press("slash")
        await pilot.press(*"^fix", "enter")
        await settle(pilot, app)
        table = app.query_one(CommitTable)
        assert [r.commit.subject for r in table.rows] == ["fix: other"]


async def test_log_shows_pushed_state(repo):
    repo.bare_remote()
    repo.git("push", "-q", "-u", "origin", "main")
    repo.commit({"x.txt": "x\n"}, "not pushed yet")
    app = GitPanelApp(repo.path)
    async with app.run_test(size=(170, 45)) as pilot:
        await settle(pilot, app)
        await pilot.press("2")
        await settle(pilot, app)
        table = app.query_one("#log-table", CommitTable)
        top = table.rows[0]
        assert top.commit.subject == "not pushed yet"
        assert any(m.kind == "unpushed" and m.node_color and not m.label for m in top.markers)
        assert app.query_one("#log-legend").has_class("-shown")
        assert not any(m.kind == "unpushed" for m in table.rows[1].markers)
        details = str(app.query_one("#log-details-text").render())
        assert "not pushed to any remote" in details
        table.cursor = 1
        await settle(pilot, app)
        details = str(app.query_one("#log-details-text").render())
        assert "pushed: origin/main" in details
