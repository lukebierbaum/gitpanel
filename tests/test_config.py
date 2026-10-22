from __future__ import annotations

from gitpanel import __main__ as cli
from gitpanel.config import EXAMPLE, load, write_example


def test_defaults_and_example_parses(tmp_path):
    cfg = load(tmp_path / "missing.toml")
    assert cfg.theme == "hulk" and cfg.errors == [] and cfg.source is None
    p = write_example(tmp_path / "c.toml")
    assert p.read_text() == EXAMPLE
    cfg = load(p)
    assert cfg.errors == [] and cfg.worktree_template == "../{repo}.worktrees/{branch}"


def test_values_errors_and_repo_trust(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text(
        'theme = "nord"\n[diff]\nside_by_side = true\ncontext = "x"\n'
        '[keys]\n"app.quit" = ""\n"commit.commit" = "ctrl+s"\n'
    )
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".gitpanel.toml").write_text("[confirm]\ndestructive = false\n")
    cfg = load(p, repo)
    assert cfg.theme == "nord" and cfg.side_by_side is True and cfg.diff_context == 3
    assert cfg.keys == {"app.quit": None, "commit.commit": "ctrl+s"}
    assert any("context must be int" in e for e in cfg.errors)
    assert any("ignored" in e for e in cfg.errors) and cfg.confirm_destructive is True
    assert load(p, repo, trust_repo=True).confirm_destructive is False
    (tmp_path / "bad.toml").write_text("theme = ")
    assert load(tmp_path / "bad.toml").errors


def test_cli_init_config(tmp_path, capsys):
    target = tmp_path / "cfg.toml"
    cli.main(["--init-config", "--config", str(target)])
    assert target.exists() and str(target) in capsys.readouterr().out
