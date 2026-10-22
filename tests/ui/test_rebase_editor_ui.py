from __future__ import annotations

from gitpanel.ui.app import GitPanelApp
from gitpanel.ui.screens.rebase_editor import RebaseEditorScreen
from gitpanel.ui.widgets.commit_table import CommitTable

from .test_commit_view import settle


async def test_editor_drop_and_reorder(repo):
    for i in range(1, 4):
        repo.commit({f"f{i}.txt": f"{i}\n"}, f"commit {i}")
    app = GitPanelApp(repo.path)
    async with app.run_test(size=(150, 45)) as pilot:
        await settle(pilot, app)
        await pilot.press("alt+9")
        await settle(pilot, app)
        table = app.query_one("#log-table", CommitTable)
        table.cursor = 2  # "commit 1"
        await settle(pilot, app)
        await pilot.press("I")
        await settle(pilot, app)
        assert isinstance(app.screen, RebaseEditorScreen)
        assert [t.subject for t in app.screen.todo] == ["commit 1", "commit 2", "commit 3"]
        await pilot.press("d")  # drop commit 1
        await pilot.press("down", "alt+down")  # move commit 2 below commit 3
        await settle(pilot, app)
        assert [t.subject for t in app.screen.todo] == ["commit 1", "commit 3", "commit 2"]
        await pilot.press("ctrl+s")
        await settle(pilot, app)
        await settle(pilot, app)
        assert not isinstance(app.screen, RebaseEditorScreen)
        assert repo.git("log", "--format=%s").split("\n")[:3] == ["commit 2", "commit 3", "initial"]
