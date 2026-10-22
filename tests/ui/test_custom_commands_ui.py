from __future__ import annotations

from gitpanel.config import Config
from gitpanel.ui.app import GitPanelApp

from .test_commit_view import settle


async def test_custom_command_from_config_runs_on_key(repo):
    cfg = Config(
        commands=[
            {"id": "mark", "label": "Mark repo", "key": "ctrl+o", "run": "touch marked-{branch}"}
        ]
    )
    app = GitPanelApp(repo.path, config=cfg)
    async with app.run_test(size=(150, 45)) as pilot:
        await settle(pilot, app)
        assert "custom.mark" in {a.id for a in app.registry.all()}
        await pilot.press("ctrl+o")
        await settle(pilot, app)
        assert (repo.path / "marked-").exists() or (repo.path / "marked-main").exists()
