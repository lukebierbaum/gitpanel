from __future__ import annotations

from textual.widgets import DataTable, OptionList

from gitpanel.ui.app import GitPanelApp
from gitpanel.ui.screens.branches import BranchesPopup
from gitpanel.ui.screens.worktrees import WorktreesView

from .test_commit_view import settle


async def test_branches_popup_search_and_checkout(repo):
    repo.branch("feature/search")
    repo.branch("bugfix")
    app = GitPanelApp(repo.path)
    async with app.run_test(size=(150, 45)) as pilot:
        await settle(pilot, app)
        await pilot.press("b")
        await settle(pilot, app)
        assert isinstance(app.screen, BranchesPopup)
        await pilot.press(*"fsea")
        await settle(pilot, app)
        ol = app.screen.query_one(OptionList)
        assert app.screen.selected_ref().short == "feature/search"
        assert ol.option_count == 2  # "Local" header + the match
        await pilot.press("enter")
        await settle(pilot, app)
        assert not isinstance(app.screen, BranchesPopup)
        assert repo.git("branch", "--show-current").strip() == "feature/search"


async def test_worktrees_window_new_and_switch(repo, tmp_path):
    app = GitPanelApp(repo.path)
    async with app.run_test(size=(150, 45)) as pilot:
        await settle(pilot, app)
        await pilot.press("alt+8")
        await settle(pilot, app)
        assert isinstance(app.view, WorktreesView)
        assert app.view.query_one(DataTable).row_count == 1
        await pilot.press("n")
        await settle(pilot, app)
        form = app.screen
        form.query_one("#field-name").value = "wt-feature"
        form.query_one("#field-path").value = str(tmp_path / "wtf")
        await pilot.press("ctrl+s")
        await settle(pilot, app)
        await settle(pilot, app)
        assert app.session.active.path.resolve() == (tmp_path / "wtf").resolve()
        header = str(app.query_one("#header").render())
        assert "worktree:" in header and "wt-feature" in header
