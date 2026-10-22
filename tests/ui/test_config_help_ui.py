from __future__ import annotations

from textual.widgets import OptionList

from gitpanel.config import Config
from gitpanel.ui.app import GitPanelApp
from gitpanel.ui.screens.help import HelpScreen

from .test_commit_view import settle


async def test_rebind_theme_and_split_from_config(repo):
    cfg = Config(
        theme="hulk-light", side_by_side=True, keys={"app.quit": "ctrl+q", "nope.action": "x"}
    )
    app = GitPanelApp(repo.path, config=cfg)
    async with app.run_test(size=(150, 45)) as pilot:
        await settle(pilot, app)
        assert app.theme == "hulk-light"
        assert app.registry.key_for(app.registry.get("app.quit")) == "ctrl+q"
        assert app.query_one("#diff").split is True
        assert any("nope.action" in e for e in cfg.errors)


async def test_help_screen_lists_actions(repo):
    app = GitPanelApp(repo.path, config=Config())
    async with app.run_test(size=(150, 45)) as pilot:
        await settle(pilot, app)
        await pilot.press("question_mark")
        await settle(pilot, app)
        assert isinstance(app.screen, HelpScreen)
        await pilot.press(*"cherry")
        await settle(pilot, app)
        ids = [o.id for o in app.screen.query_one(OptionList)._options if o.id]
        assert "log.cherry_pick" in ids and "commit.commit" not in ids
        await pilot.press("escape")
        await settle(pilot, app)
        assert not isinstance(app.screen, HelpScreen)
