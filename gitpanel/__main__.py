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
    p.add_argument("--version", action="version", version=f"gitpanel {__version__}")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    from .ui.app import GitPanelApp

    app = GitPanelApp(Path(args.path))
    app.run()
    if app.startup_error:
        print(app.startup_error, file=sys.stderr)
        sys.exit(2)
    if args.cd_file and app.session is not None:
        Path(args.cd_file).write_text(str(app.session.active.path))


if __name__ == "__main__":
    main()
