from __future__ import annotations

from gitpanel.ui.app import GitPanelApp
from gitpanel.ui.widgets.splitter import Splitter, load_sizes

from .test_commit_view import settle


async def test_drag_splitter_resizes_and_remembers(repo):
    app = GitPanelApp(repo.path)
    async with app.run_test(size=(150, 40)) as pilot:
        await settle(pilot, app)
        left = app.query_one("#commit-left")
        before = left.outer_size.width
        splitter = app.query_one("#commit-body > Splitter", Splitter)
        x, y = splitter.region.x, splitter.region.y + 5  # screen coordinates
        await pilot.mouse_down(None, offset=(x, y))
        await pilot.hover(None, offset=(x + 15, y))
        await pilot.mouse_up(None, offset=(x + 15, y))
        await settle(pilot, app)
        assert left.outer_size.width == before + 15
        assert load_sizes()["commit.changes"] == before + 15

    # a new session starts with the remembered width
    app = GitPanelApp(repo.path)
    async with app.run_test(size=(150, 40)) as pilot:
        await settle(pilot, app)
        assert app.query_one("#commit-left").outer_size.width == before + 15
        splitter = app.query_one("#commit-body > Splitter", Splitter)
        await pilot.click(splitter, offset=(0, 5), times=2)  # double-click resets
        await settle(pilot, app)
        assert app.query_one("#commit-left").outer_size.width == before
        assert "commit.changes" not in load_sizes()


async def test_drag_up_makes_the_commit_message_taller(repo):
    app = GitPanelApp(repo.path)
    async with app.run_test(size=(150, 40)) as pilot:
        await settle(pilot, app)
        msg = app.query_one("#commit-msg")
        before = msg.outer_size.height
        assert before >= 9  # roomier by default
        splitter = app.query_one("#commit-left > Splitter", Splitter)
        x, y = splitter.region.x + 5, splitter.region.y
        await pilot.mouse_down(None, offset=(x, y))
        await pilot.hover(None, offset=(x, y - 6))
        await pilot.mouse_up(None, offset=(x, y - 6))
        await settle(pilot, app)
        assert msg.outer_size.height == before + 6
        assert "commit.message" in load_sizes()
