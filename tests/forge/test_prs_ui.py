from __future__ import annotations

from textual.widgets import DataTable, OptionList, Select, TabbedContent

from gitpanel.config import Config
from gitpanel.ui.app import GitPanelApp
from gitpanel.ui.screens.prs import PRsView
from gitpanel.ui.widgets.commit_table import CommitTable
from gitpanel.ui.widgets.diff_view import DiffView

from ..ui.test_commit_view import settle


async def test_pr_window_list_details_files_threads(gh_repo):
    app = GitPanelApp(gh_repo.repo.path, config=Config(pr_default_query="review"))
    async with app.run_test(size=(170, 48)) as pilot:
        await settle(pilot, app)
        await pilot.press("7")
        await settle(pilot, app)
        view = app.view
        assert isinstance(view, PRsView)
        table = view.query_one("#pr-list", DataTable)
        assert table.row_count == 1 and view.detail is not None and view.detail.number == 1
        assert "Body of #1" in view.query_one("#pr-body").source  # type: ignore[attr-defined]
        assert view.query_one("#pr-checks", DataTable).row_count == 2

        view.query_one("#pr-query", Select).value = "open"
        await settle(pilot, app)
        assert table.row_count == 2

        view.query_one("#pr-tabs", TabbedContent).active = "tab-commits"
        await settle(pilot, app)
        subjects = [r.commit.subject for r in view.query_one("#pr-commits", CommitTable).rows]
        assert subjects == ["Harden parser", "Add fuzz harness"]

        view.query_one("#pr-tabs", TabbedContent).active = "tab-files"
        await settle(pilot, app)
        files = view.query_one("#pr-files", OptionList)
        assert files.option_count == 2
        files.highlighted = next(
            i for i in range(files.option_count) if files.get_option_at_index(i).id == "app.py"
        )
        await settle(pilot, app)
        diff = view.query_one("#pr-diff", DiffView)
        assert diff.line_marks == {("RIGHT", 4): "◆"}
        # put the cursor on the commented line → the thread shows below the diff
        diff.split = False
        diff._rebuild()
        diff.cursor = next(i for i, r in enumerate(diff.rows) if r.new_no == 4 and r.kind == "line")
        diff.focus()
        await settle(pilot, app)
        assert view.query_one("#pr-thread").has_class("-shown")
        await pilot.press("x")  # resolve
        await settle(pilot, app)
        assert gh_repo.state["threads"]["1"][0]["isResolved"] is True


async def test_header_badge_for_current_branch_pr(gh_repo):
    gh_repo.repo.checkout("feature/fuzz")
    app = GitPanelApp(gh_repo.repo.path)
    async with app.run_test(size=(170, 40)) as pilot:
        await settle(pilot, app)
        await settle(pilot, app)
        header = str(app.query_one("#header").render())
        assert "PR #1" in header and "✗" in header  # lint check is failing


async def test_pr_window_without_github(repo):
    app = GitPanelApp(repo.path)
    async with app.run_test(size=(150, 40)) as pilot:
        await settle(pilot, app)
        await pilot.press("7")
        await settle(pilot, app)
        empty = app.view.query_one("#pr-empty")
        assert empty.has_class("-shown") and "github.com remote" in str(empty.render())


async def test_pr_files_diff_fits_the_screen(gh_repo):
    app = GitPanelApp(
        gh_repo.repo.path, config=Config(pr_default_query="review", side_by_side=True)
    )
    async with app.run_test(size=(150, 40)) as pilot:
        await settle(pilot, app)
        await pilot.press("7")
        await settle(pilot, app)
        app.view.query_one("#pr-tabs", TabbedContent).active = "tab-files"
        await settle(pilot, app)
        diff = app.view.query_one("#pr-diff", DiffView)
        assert diff.region.right <= 150
