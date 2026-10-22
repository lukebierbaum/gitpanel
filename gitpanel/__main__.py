"""gitpanel command line entry point."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="gitpanel", description="PyCharm-style Git client for the terminal."
    )
    p.add_argument("path", nargs="?", default=".", help="repository or worktree (default: .)")
    p.add_argument(
        "--cd-file",
        metavar="FILE",
        help="on exit, write the active worktree path to FILE (for a shell `cd` wrapper)",
    )
    p.add_argument(
        "--config", metavar="FILE", help="config file (default: ~/.config/gitpanel/config.toml)"
    )
    p.add_argument(
        "--trust-repo-config",
        action="store_true",
        help="also load the repository's .gitpanel.toml (it can define commands)",
    )
    p.add_argument(
        "--init-config", action="store_true", help="write an example config file and exit"
    )
    p.add_argument("--version", action="version", version=f"gitpanel {__version__}")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    if args.init_config:
        from .config import write_example

        print(write_example(Path(args.config) if args.config else None))
        return
    from .ui.app import GitPanelApp

    app = GitPanelApp(
        Path(args.path),
        config_path=Path(args.config) if args.config else None,
        trust_repo_config=args.trust_repo_config,
    )
    app.run()
    if app.startup_error:
        print(app.startup_error, file=sys.stderr)
        sys.exit(2)
    if args.cd_file and app.session is not None:
        Path(args.cd_file).write_text(str(app.session.active.path))


if __name__ == "__main__":
    main()
