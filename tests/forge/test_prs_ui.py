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
        # CI and review arrive after the list, from the batched status query
        assert str(table.get_row("1")[1]) == "✗" and str(table.get_row("1")[2]) == "Review"
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
        empty = app.view.query_one("#pr-left .lt-message")
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


async def test_pr_list_filters_as_you_type(gh_repo):
    app = GitPanelApp(gh_repo.repo.path, config=Config(pr_default_query="open"))
    async with app.run_test(size=(150, 40)) as pilot:
        await settle(pilot, app)
        await pilot.press("7")
        await settle(pilot, app)
        view = app.view
        table = view.query_one("#pr-list", DataTable)
        assert table.row_count == 2
        await pilot.press("slash")
        assert app.focused is view.query_one("#pr-left .lt-filter")
        await pilot.press(*"fuzz")
        await settle(pilot, app)
        assert table.row_count == 1 and view.detail is not None and view.detail.number == 1
        await pilot.press("ctrl+u", *"status:failure")  # CI state from the status query
        await settle(pilot, app)
        assert [str(table.get_row_at(i)[0]) for i in range(table.row_count)] == ["#1"]
        await pilot.press("escape")
        await settle(pilot, app)
        assert table.row_count == 2 and app.focused is table


async def test_pr_list_sorts_by_column(gh_repo):
    app = GitPanelApp(gh_repo.repo.path, config=Config(pr_default_query="open"))
    async with app.run_test(size=(150, 40)) as pilot:
        await settle(pilot, app)
        await pilot.press("7")
        await settle(pilot, app)
        view = app.view
        table = view.query_one("#pr-list", DataTable)

        def numbers() -> list[str]:
            return [str(table.get_row_at(i)[0]) for i in range(table.row_count)]

        listing = view.pr_list
        listing.sort_by("num")  # newest first
        assert numbers() == ["#2", "#1"] and str(table.columns["num"].label) == "# ▼"
        listing.sort_by("num")  # same column again flips
        assert numbers() == ["#1", "#2"]
        listing.sort_by("ci")  # failing first; #2 has no checks
        await settle(pilot, app)
        assert numbers() == ["#1", "#2"]
        table.focus()
        await pilot.press("<")  # reverse
        assert numbers() == ["#2", "#1"]
        listing.sort_by("title")
        assert numbers() == ["#2", "#1"]  # "Add CLI flags" < "Harden parser…"
        await pilot.press(">")  # next column: author ("lin" < "me")
        assert numbers() == ["#1", "#2"] and str(table.columns["author"].label) == "Author ▲"


async def test_pr_list_columns_fit_the_pane(gh_repo):
    app = GitPanelApp(gh_repo.repo.path, config=Config(pr_default_query="open"))
    async with app.run_test(size=(140, 38)) as pilot:
        await settle(pilot, app)
        await pilot.press("7")
        await settle(pilot, app)
        table = app.view.query_one("#pr-list", DataTable)
        assert table.virtual_size.width <= table.scrollable_content_region.width


async def test_stacks_in_the_pr_list(gh_repo):
    import json

    from gitpanel.ui.widgets.list_table import Group
    from tests.forge.conftest import _pr

    repo = gh_repo.repo
    repo.checkout("feature/fuzz")
    repo.git("switch", "-q", "-c", "feature/fuzz-2")
    repo.commit({"fuzz2.py": "x\n"}, "More fuzzing")
    repo.git("push", "-q", "-u", "origin", "feature/fuzz-2")
    sha = repo.head()
    repo.git("switch", "-q", "-c", "feature/fuzz-3")  # local only, no PR
    repo.commit({"fuzz3.py": "y\n"}, "Even more")
    repo.checkout("main")
    repo.git("--git-dir", str(gh_repo.bare), "update-ref", "refs/pull/3/head", sha)
    gh_repo.update(
        lambda st: st["prs"].append(_pr(3, "Fuzz the lexer too", "lin", "feature/fuzz-2", sha,
                                        baseRefName="feature/fuzz"))
    )  # fmt: skip

    def rows(view) -> list[str]:  # type: ignore[no-untyped-def]
        out = []
        for r in view.pr_list.shown:
            if isinstance(r, Group):
                out.append(" | ".join(["== " + r.label.plain, *(c.plain for _, c in r.cells)]))
            else:
                out.append(f"#{r.number}" if hasattr(r, "number") else r.branch)
        return out

    app = GitPanelApp(gh_repo.repo.path, config=Config(pr_default_query="open"))
    async with app.run_test(size=(160, 40)) as pilot:
        await settle(pilot, app)
        await pilot.press("7")
        await settle(pilot, app)
        view = app.view
        assert rows(view) == [
            "== ⚠ Not a GitHub stack yet | 2 PRs | → main", "#1", "#3",
            "== Not in a stack", "#2",
        ]  # fmt: skip
        table = view.query_one("#pr-list", DataTable)
        assert table.get_row_at(1)[3].plain.startswith("├ Harden parser")
        assert "2/2" in table.get_row_at(2)[3].plain
        # the cursor skips the label rows
        assert view.detail.number == 1
        await pilot.press("]")  # up the stack
        await settle(pilot, app)
        assert view.detail.number == 3
        assert "not a GitHub stack yet" in str(view.query_one("#pr-meta").render())
        await pilot.press("[")
        await settle(pilot, app)
        assert view.detail.number == 1

        # the same PRs as a GitHub stack
        gh_repo.update(lambda st: st.setdefault("stacks", []).append(
            {"number": 7, "base": "main", "prs": [1, 3]}
        ))  # fmt: skip
        view.reload()
        await settle(pilot, app)
        assert rows(view)[0] == "== Stack #7 | 2 PRs | → main"

        # every stack in the repo, incl. a local layer from gh-stack's record
        (app.session.common_dir / "gh-stack").write_text(json.dumps({"stacks": [{
            "trunk": {"branch": "main"},
            "branches": [{"branch": "feature/fuzz", "base": ""},
                         {"branch": "feature/fuzz-2", "base": ""},
                         {"branch": "feature/fuzz-3", "base": ""}],
        }]}))  # fmt: skip
        view.query_one("#pr-query").value = "stacks"
        await settle(pilot, app)
        # feature/fuzz-3 has no PR: it joins Stack #7 as a local layer on top
        assert rows(view) == [
            "== Stack #7 | 2 PRs + 1 | → main", "#1", "#3", "feature/fuzz-3",
        ]  # fmt: skip
        assert (
            view.query_one("#pr-list", DataTable)
            .get_row_at(3)[3]
            .plain.startswith("└ feature/fuzz-3  no PR yet")
        )
