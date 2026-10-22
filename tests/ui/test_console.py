from __future__ import annotations

from textual.widgets import RichLog

from gitpanel.git.console import CONSOLE
from gitpanel.ui.app import GitPanelApp
from gitpanel.ui.screens.console import ConsoleView

from .test_commit_view import settle


def log_text(view: ConsoleView) -> str:
    log = view.query_one(RichLog)
    return "\n".join(line.text for line in log.lines)


async def test_console_shows_commands_and_output(repo):
    CONSOLE.clear()
    repo.write("README.md", "changed\n")
    app = GitPanelApp(repo.path)
    async with app.run_test(size=(160, 45)) as pilot:
        await settle(pilot, app)
        await pilot.press("a")  # stage all (Commit window)
        await settle(pilot, app)
        app.query_one("#commit-msg").text = "console test"
        await pilot.press("ctrl+k")
        await settle(pilot, app)
        await pilot.press("6")
        await settle(pilot, app)
        view = app.view
        assert isinstance(view, ConsoleView)
        text = log_text(view)
        assert "git add -A" in text
        assert "git commit --cleanup=strip -F -" in text
        assert "← console test" in text  # the message sent on stdin
        assert "git status" not in text  # read-only queries hidden by default
        assert "No commands yet" not in text
        await pilot.press("a")
        await settle(pilot, app)
        assert "git status --porcelain=v2" in log_text(view)
        await pilot.press("o")
        await settle(pilot, app)
        await pilot.press("c")
        await settle(pilot, app)
        assert "No commands yet" in log_text(view)


async def test_failed_command_is_recorded_with_stderr(repo):
    CONSOLE.clear()
    import pytest

    from gitpanel.git.runner import GitError

    with pytest.raises(GitError):
        await repo.g.run("checkout", "no-such-branch")
    entry = CONSOLE.entries[-1]
    assert entry.args == ("checkout", "no-such-branch") and entry.returncode != 0
    assert "no-such-branch" in entry.stderr and not entry.read_only
