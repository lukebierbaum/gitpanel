from __future__ import annotations

from gitpanel.ui.app import GitPanelApp
from gitpanel.ui.screens.resolver import ResolverScreen, ThreeWayView

from ..git.test_conflicts import make_conflict
from .test_commit_view import move_to, settle


async def test_resolve_two_chunks_and_continue(repo):
    make_conflict(repo)
    app = GitPanelApp(repo.path)
    async with app.run_test(size=(150, 45)) as pilot:
        await settle(pilot, app)
        assert app.query_one("#banner").has_class("-active")
        move_to(app, "conflict", "f.txt")
        app.query_one("#changes").focus()
        await settle(pilot, app)
        await pilot.press("enter")
        await settle(pilot, app)
        assert isinstance(app.screen, ResolverScreen)
        view = app.screen.query_one(ThreeWayView)
        assert view.model is not None and len(view.model.chunks) == 2
        await pilot.press("left")  # chunk 1: ours, advances to chunk 2
        await settle(pilot, app)
        assert view.current == 1
        await pilot.press("right")  # chunk 2: theirs
        await settle(pilot, app)
        assert view.model.unresolved == 0
        await pilot.press("ctrl+s")
        await settle(pilot, app)
        await settle(pilot, app)
        assert not isinstance(app.screen, ResolverScreen)
        content = repo.read("f.txt")
        assert "ONE-main" in content and "five-other" in content and "<<<" not in content
        assert repo.git("diff", "--name-only", "--diff-filter=U").strip() == ""
        assert "ready to continue" in str(app.query_one("#op-label").render())
        await pilot.click("#op-continue")
        await settle(pilot, app)
        assert not (repo.path / ".git" / "MERGE_HEAD").exists()
        assert repo.git("log", "-1", "--format=%p").count(" ") == 1  # merge commit
