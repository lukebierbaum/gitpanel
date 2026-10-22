from __future__ import annotations

from gitpanel.ui.app import GitPanelApp
from gitpanel.ui.keys import alt_equivalent
from gitpanel.ui.screens.journal import JournalScreen

from .test_commit_view import settle


def test_mac_option_characters_map_back_to_alt():
    assert alt_equivalent("∞") == "alt+5"
    assert alt_equivalent("˙") == "alt+h"
    assert alt_equivalent("Ω") == "alt+z"
    assert alt_equivalent("a") is None and alt_equivalent(None) is None


async def test_option_layout_characters_trigger_alt_actions(repo):
    app = GitPanelApp(repo.path)
    async with app.run_test(size=(150, 40)) as pilot:
        await settle(pilot, app)
        await pilot.press("infinity")  # what Alt+5 sends on a Mac-style layout
        await settle(pilot, app)
        assert app.view.id == "shelf-view"
        await pilot.press("1")
        await settle(pilot, app)
        await pilot.press("Ω")  # Alt+z → operation history
        await settle(pilot, app)
        assert isinstance(app.screen, JournalScreen)
        await pilot.press("escape")
        await settle(pilot, app)
        # typed into the commit message, the same character stays text
        app.query_one("#commit-msg").focus()
        await pilot.press("infinity")
        await settle(pilot, app)
        assert app.query_one("#commit-msg").text == "∞" and app.view.id == "commit-view"
