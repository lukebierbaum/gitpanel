from __future__ import annotations

from gitpanel.ui.app import GitPanelApp
from gitpanel.ui.screens.compare import CompareView
from gitpanel.ui.widgets.commit_table import CommitTable
from gitpanel.ui.widgets.diff_view import DiffView
from gitpanel.ui.widgets.file_list import FileList

from .test_commit_view import settle


async def test_pick_from_log_marks_picked_and_compare(repo):
    repo.branch("feature")
    repo.checkout("feature")
    repo.commit({"a.txt": "a\n"}, "feature a")
    repo.commit({"b.txt": "b\n"}, "feature b")
    repo.checkout("main")
    repo.commit({"m.txt": "m\n"}, "main work")
    app = GitPanelApp(repo.path)
    async with app.run_test(size=(160, 48)) as pilot:
        await settle(pilot, app)
        await pilot.press("alt+9")
        await settle(pilot, app)
        table = app.query_one("#log-table", CommitTable)
        idx = next(i for i, r in enumerate(table.rows) if r.commit.subject == "feature a")
        table.cursor = idx
        await settle(pilot, app)
        await pilot.press("C")
        await settle(pilot, app)
        await settle(pilot, app)
        assert repo.git("log", "-1", "--format=%s").strip() == "feature a"
        row = next(
            r for r in table.rows if r.commit.subject == "feature a" and r.commit.sha != repo.head()
        )
        assert any(m.kind == "picked" for m in row.markers)

        await pilot.press("b")
        await settle(pilot, app)
        await pilot.press(*"feature")
        await settle(pilot, app)
        await pilot.press("ctrl+e")
        await settle(pilot, app)
        await settle(pilot, app)
        assert isinstance(app.view, CompareView)
        right = app.query_one("#cmp-b", CommitTable)
        marks = {r.commit.subject: bool(r.markers) for r in right.rows}
        assert marks == {"feature b": False, "feature a": True}


async def test_compare_lets_you_pick_a_file_of_the_commit(repo):
    repo.branch("feature")
    repo.checkout("feature")
    repo.commit({"one.txt": "1\n", "two.txt": "2\n"}, "two files")
    repo.checkout("main")
    app = GitPanelApp(repo.path)
    async with app.run_test(size=(160, 48)) as pilot:
        await settle(pilot, app)
        await pilot.press("b")
        await settle(pilot, app)
        await pilot.press(*"feature")
        await settle(pilot, app)
        await pilot.press("ctrl+e")
        await settle(pilot, app)
        await settle(pilot, app)
        files = app.query_one("#cmp-files", FileList)
        diff = app.query_one("#cmp-diff", DiffView)
        assert [d.path for d in files.diffs] == ["one.txt", "two.txt"]
        assert diff.diff is not None and diff.diff.path == "one.txt"
        files.highlighted = 1
        await settle(pilot, app)
        assert diff.diff.path == "two.txt"
