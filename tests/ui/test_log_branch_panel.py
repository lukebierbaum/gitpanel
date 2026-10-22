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


async def test_branches_are_grouped_into_folders(repo):
    for name in ("feature/search", "feature/tokens", "feature/ui/dark", "release/1.x"):
        repo.branch(name)
        repo.checkout(name)
        repo.commit({f"{name.replace('/', '_')}.txt": "x\n"}, f"work on {name}")
    repo.checkout("main")
    app = GitPanelApp(repo.path)
    async with app.run_test(size=(170, 45)) as pilot:
        await settle(pilot, app)
        await pilot.press("2")
        await settle(pilot, app)
        panel = app.query_one(BranchPanel)
        local = next(n for n in panel.root.children if n.data.key == "group:local")
        names = [str(n.label).split("  ")[0] for n in local.children]
        assert names[:2] == ["feature", "release"]  # folders first
        feature = local.children[0]
        assert not feature.is_expanded  # folders start closed
        sub = [str(n.label).strip().split("  ")[0] for n in feature.children]
        assert sub[0] == "ui" and {s.strip() for s in sub[1:]} == {"search", "tokens"}
        # open the folder, pick a branch inside it
        panel.focus()
        panel.move_cursor(feature)
        await settle(pilot, app)
        await pilot.press("right")
        await settle(pilot, app)
        assert feature.is_expanded
        search = next(n for n in feature.children if n.data.filter == "feature/search")
        panel.move_cursor(search)
        await settle(pilot, app)
        assert subjects(app)[0] == "work on feature/search"
        # space on the folder marks every branch in it
        panel.move_cursor(feature)
        await settle(pilot, app)
        await pilot.press("space")
        await settle(pilot, app)
        assert set(panel.marked) == {
            "refs/heads/feature/search",
            "refs/heads/feature/tokens",
            "refs/heads/feature/ui/dark",
        }
        assert {s for s in subjects(app) if s.startswith("work")} == {
            "work on feature/search",
            "work on feature/tokens",
            "work on feature/ui/dark",
        }
        # folder state survives a refresh
        app.view.reload()
        await settle(pilot, app)
        local = next(n for n in panel.root.children if n.data.key == "group:local")
        assert local.children[0].is_expanded


async def test_folders_toggle_and_config(repo):
    from gitpanel.config import Config

    repo.branch("feature/a")
    repo.branch("feature/b")
    app = GitPanelApp(repo.path, config=Config(branch_folders=False))
    async with app.run_test(size=(170, 45)) as pilot:
        await settle(pilot, app)
        await pilot.press("2")
        await settle(pilot, app)
        panel = app.query_one(BranchPanel)

        def local_labels():
            local = next(n for n in panel.root.children if n.data.key == "group:local")
            return [str(n.label).strip().split("  ")[0].lstrip("★● ") for n in local.children]

        assert local_labels() == ["main", "feature/a", "feature/b"]  # flat, favourites first
        await pilot.press("g")
        await settle(pilot, app)
        assert local_labels()[0] == "feature" and panel.folders
        await pilot.press("g")
        await settle(pilot, app)
        assert local_labels() == ["main", "feature/a", "feature/b"]
