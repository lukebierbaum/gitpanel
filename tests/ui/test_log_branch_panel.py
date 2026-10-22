from __future__ import annotations

from gitpanel.ui.app import GitPanelApp
from gitpanel.ui.widgets.branch_panel import BranchPanel
from gitpanel.ui.widgets.commit_table import CommitTable

from .test_commit_view import settle


def subjects(app) -> list[str]:
    return [r.commit.subject for r in app.query_one("#log-table", CommitTable).rows]


def move_to_branch(panel: BranchPanel, name: str) -> None:
    def walk(node):
        for child in node.children:
            if child.data is not None and child.data.filter == name:
                return child
            found = walk(child)
            if found is not None:
                return found
        return None

    node = walk(panel.root)
    assert node is not None, name
    panel.move_cursor(node)


async def test_branch_panel_filters_log_marks_and_checks_out(repo):
    repo.branch("feature")
    repo.branch("bugfix")
    repo.checkout("feature")
    repo.commit({"f.txt": "f\n"}, "feature work")
    repo.checkout("bugfix")
    repo.commit({"b.txt": "b\n"}, "bugfix work")
    repo.checkout("main")
    repo.commit({"m.txt": "m\n"}, "main work")
    app = GitPanelApp(repo.path)
    async with app.run_test(size=(170, 45)) as pilot:
        await settle(pilot, app)
        await pilot.press("2")
        await settle(pilot, app)
        assert set(subjects(app)) >= {"feature work", "bugfix work", "main work"}
        panel = app.query_one(BranchPanel)
        panel.focus()
        move_to_branch(panel, "feature")
        await settle(pilot, app)
        assert subjects(app) == ["feature work", "initial"]
        assert app.query_one("#f-branch").value == "feature"
        # mark feature + bugfix → both histories, not main's
        await pilot.press("space")
        move_to_branch(panel, "bugfix")
        await settle(pilot, app)
        await pilot.press("space")
        await settle(pilot, app)
        assert set(subjects(app)) == {"feature work", "bugfix work", "initial"}
        # escape clears the marks → back to the highlighted branch only
        await pilot.press("escape")
        await settle(pilot, app)
        move_to_branch(panel, "")  # "All branches"
        await settle(pilot, app)
        assert "main work" in subjects(app)
        # enter on a branch checks it out (branch actions work in the panel)
        move_to_branch(panel, "bugfix")
        await settle(pilot, app)
        await pilot.press("enter")
        await settle(pilot, app)
        assert repo.git("branch", "--show-current").strip() == "bugfix"
        # [ hides the panel
        app.query_one("#log-table").focus()
        await pilot.press("left_square_bracket")
        await settle(pilot, app)
        assert panel.has_class("-hidden")
