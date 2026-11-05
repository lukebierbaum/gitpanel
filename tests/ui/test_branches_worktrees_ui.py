from __future__ import annotations

from textual import events
from textual.widgets import DataTable, OptionList

from gitpanel.git.session import ChangeKind
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


async def test_worktrees_filter(repo, tmp_path):
    repo.git("worktree", "add", "-q", "-b", "feature/alpha", str(tmp_path / "alpha"))
    repo.git("worktree", "add", "-q", "-b", "bugfix/beta", str(tmp_path / "beta"))
    app = GitPanelApp(repo.path)
    async with app.run_test(size=(150, 45)) as pilot:
        await settle(pilot, app)
        await pilot.press("3")
        await settle(pilot, app)
        view = app.view
        assert isinstance(view, WorktreesView)
        table = view.query_one(DataTable)
        assert table.row_count == 3
        await pilot.press("slash", *"bugfix")
        await settle(pilot, app)
        assert table.row_count == 1 and view.selected().branch_short == "bugfix/beta"
        await pilot.press("ctrl+u", *"main")  # state words match too
        await settle(pilot, app)
        assert table.row_count == 1 and view.selected().is_main
        await pilot.press("escape")
        await settle(pilot, app)
        assert table.row_count == 3 and app.focused is table


async def test_worktrees_window_shows_a_broken_worktree(repo, tmp_path):
    wt_path = tmp_path / "linked"
    repo.git("worktree", "add", "-q", "-b", "linked", str(wt_path))
    moved = tmp_path / "moved"
    repo.path.rename(moved)  # the linked worktree's .git file still points at the old place
    app = GitPanelApp(moved)
    async with app.run_test(size=(150, 45)) as pilot:
        await settle(pilot, app)
        await pilot.press("3")
        await settle(pilot, app)
        view = app.view
        assert isinstance(view, WorktreesView)
        table = view.query_one(DataTable)
        assert table.row_count == 2
        cells = [str(c) for i in range(table.row_count) for c in table.get_row_at(i)]
        assert "broken" in cells
        await pilot.press("slash", *"broken")
        await settle(pilot, app)
        assert table.row_count == 1 and view.selected().path.resolve() == wt_path.resolve()
        details = str(view.query_one("#wt-details").render())
        assert "Broken: fatal: gitfile" in details and "Repair Worktrees" in details

        await app.dispatcher.dispatch_id("worktree.repair", app.context())
        await settle(pilot, app)
        cells = [str(c) for i in range(table.row_count) for c in table.get_row_at(i)]
        assert "broken" not in cells


async def test_active_worktree_deleted_switches_to_another(repo):
    import shutil

    wt_path = repo.worktree("doomed", "doomed")
    app = GitPanelApp(wt_path)
    async with app.run_test(size=(150, 45)) as pilot:
        await settle(pilot, app)
        shutil.rmtree(wt_path)
        app.session.emit(ChangeKind.ALL)  # what the watcher / app focus do
        await settle(pilot, app)
        assert app.session.active.is_main
        assert app.session.active.path.resolve() == repo.path.resolve()
        assert any("switched to worktree" in n.message for n in app._notifications)
        header = str(app.query_one("#header").render())
        assert "worktree:" not in header and "main" in header


async def test_repository_moved_while_open_reports_once(repo, tmp_path):
    from gitpanel.ui.dialogs import ErrorDialog

    app = GitPanelApp(repo.path)
    async with app.run_test(size=(150, 45)) as pilot:
        await settle(pilot, app)
        repo.path.rename(tmp_path / "moved")
        app.session.emit(ChangeKind.ALL)
        await settle(pilot, app)
        assert isinstance(app.screen, ErrorDialog)
        assert "no longer exists" in app.screen.details
        assert "no longer available" in str(app.query_one("#header").render())
        await pilot.press("escape")
        await settle(pilot, app)
        app.post_message(events.AppFocus())  # refreshes again: no second dialog, no crash
        await settle(pilot, app)
        assert not isinstance(app.screen, ErrorDialog)
        assert not repo.path.exists()  # nothing re-created at the old place
        app.session.start_watching(interval=0.01)
        await pilot.pause(0.05)
        await settle(pilot, app)
        assert app.is_running
