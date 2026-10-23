from __future__ import annotations

from textual.widgets import DataTable

from gitpanel.ui.app import GitPanelApp
from gitpanel.ui.screens.journal import JournalScreen

from .test_commit_view import settle


async def test_journal_undo_to_here_and_dynamic_label(repo):
    base = repo.head()
    app = GitPanelApp(repo.path)
    async with app.run_test(size=(150, 45)) as pilot:
        await settle(pilot, app)
        for i in range(2):
            repo.write(f"n{i}.txt", f"{i}\n")
            app.query_one("#commit-msg").text = f"commit {i}"
            await pilot.press("a")
            await settle(pilot, app)
            await pilot.press("ctrl+k")
            await settle(pilot, app)
        hints = app.query_one("#hints").text
        assert "Undo “Commit: commit 1”" in hints
        await pilot.press("alt+z")
        await settle(pilot, app)
        assert isinstance(app.screen, JournalScreen)
        table = app.screen.query_one(DataTable)
        assert table.row_count == 2
        table.move_cursor(row=1)  # the older commit
        await settle(pilot, app)
        await pilot.press("enter")
        await settle(pilot, app)
        assert repo.head() == base
