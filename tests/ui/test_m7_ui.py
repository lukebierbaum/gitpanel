from __future__ import annotations

from textual.widgets import OptionList

from gitpanel.ui.app import GitPanelApp
from gitpanel.ui.screens.annotate import AnnotateScreen, BlameView
from gitpanel.ui.screens.history import HistoryScreen
from gitpanel.ui.screens.shelf import ShelfView
from gitpanel.ui.widgets.commit_table import CommitTable

from .test_commit_view import move_to, settle


async def test_history_and_annotate_from_commit_window(repo):
    repo.commit({"f.txt": "one\n"}, "first f")
    repo.commit({"f.txt": "one\ntwo\n"}, "second f")
    repo.write("f.txt", "one\ntwo\nthree\n")
    app = GitPanelApp(repo.path)
    async with app.run_test(size=(150, 45)) as pilot:
        await settle(pilot, app)
        move_to(app, "unstaged", "f.txt")
        app.query_one("#changes").focus()
        await settle(pilot, app)
        await pilot.press("alt+h")
        await settle(pilot, app)
        assert isinstance(app.screen, HistoryScreen)
        rows = app.screen.query_one(CommitTable).rows
        assert [r.commit.subject for r in rows] == ["second f", "first f"]
        await pilot.press("escape")
        await settle(pilot, app)
        await pilot.press("alt+a")
        await settle(pilot, app)
        assert isinstance(app.screen, AnnotateScreen)
        lines = app.screen.query_one(BlameView).lines
        assert [ln.summary for ln in lines[:2]] == ["first f", "second f"]
        assert lines[2].uncommitted


async def test_shelve_from_commit_window_and_unshelve(repo):
    repo.write("README.md", "changed\n")
    app = GitPanelApp(repo.path)
    async with app.run_test(size=(150, 45)) as pilot:
        await settle(pilot, app)
        move_to(app, "unstaged", "README.md")
        app.query_one("#changes").focus()
        await settle(pilot, app)
        await pilot.press("alt+s")
        await settle(pilot, app)
        await pilot.press(*"shelfie", "enter")
        await settle(pilot, app)
        assert repo.read("README.md") == "hello\n"
        await pilot.press("alt+5")
        await settle(pilot, app)
        assert isinstance(app.view, ShelfView)
        ol = app.view.query_one("#shelf-list", OptionList)
        assert ol.highlighted_option is not None and "shelfie" in str(ol.highlighted_option.prompt)
        await pilot.press("enter")
        await settle(pilot, app)
        assert repo.read("README.md") == "changed\n"
