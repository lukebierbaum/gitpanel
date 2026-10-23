from __future__ import annotations

from gitpanel.config import Config
from gitpanel.ui.app import GitPanelApp
from gitpanel.ui.colors import DARK, LIGHT, C

from .test_commit_view import settle


async def test_palette_follows_the_theme(repo):
    app = GitPanelApp(repo.path, config=Config(theme="hulk-light"))
    async with app.run_test(size=(120, 30)) as pilot:
        await settle(pilot, app)
        assert C.green == LIGHT.green and C.unpushed == LIGHT.amber
        app.theme = "hulk"
        await settle(pilot, app)
        assert C.green == DARK.green
        assert C["incoming"] == DARK.purple and C["bold"] == "bold"  # names pass through
