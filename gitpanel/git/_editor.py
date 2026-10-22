"""GIT_EDITOR / GIT_SEQUENCE_EDITOR helper.

git invokes `python -m gitpanel.git._editor <file>`. When a scripted rebase
has staged instructions (GITPANEL_EDITOR_DIR, see scripted_rebase.py), the
helper answers from them:

- a rebase todo file (`git-rebase-todo`) is replaced by `<dir>/todo`;
- a commit message file is replaced by the next message from
  `<dir>/messages.json` for the commit being processed, if any.

Without instructions it leaves message files unchanged when they already
contain text (so `--no-edit`-like behaviour) and otherwise fails, so git
never blocks waiting for an interactive editor.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def _message_for(directory: Path, git_dir: Path | None) -> str | None:
    msgs_file = directory / "messages.json"
    if not msgs_file.exists():
        return None
    messages: dict[str, str] = json.loads(msgs_file.read_text())
    if git_dir is None:
        return None
    # current commit being edited: rebase-merge/stopped-sha or the last "done" line
    rm = git_dir / "rebase-merge"
    candidates: list[str] = []
    done = rm / "done"
    if done.exists():
        lines = [ln.split() for ln in done.read_text().splitlines() if ln.strip()]
        if lines and len(lines[-1]) > 1:
            candidates.append(lines[-1][1])
    stopped = rm / "stopped-sha"
    if stopped.exists():
        candidates.append(stopped.read_text().strip())
    for c in candidates:
        for sha, msg in messages.items():
            if sha.startswith(c) or c.startswith(sha):
                return msg
    return None


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("gitpanel editor helper: no file given", file=sys.stderr)
        return 1
    target = Path(argv[1])
    directory = os.environ.get("GITPANEL_EDITOR_DIR")
    git_dir_env = os.environ.get("GITPANEL_GIT_DIR")
    git_dir = Path(git_dir_env) if git_dir_env else None

    if directory:
        d = Path(directory)
        if target.name == "git-rebase-todo":
            todo = d / "todo"
            if todo.exists():
                target.write_text(todo.read_text())
                return 0
        else:
            msg = _message_for(d, git_dir)
            if msg is not None:
                target.write_text(msg if msg.endswith("\n") else msg + "\n")
                return 0

    if target.name == "git-rebase-todo":
        return 0  # keep git's default todo
    try:
        content = target.read_text()
    except OSError:
        content = ""
    meaningful = [ln for ln in content.splitlines() if ln.strip() and not ln.startswith("#")]
    if meaningful:
        return 0
    print(
        "gitpanel: git asked for an interactive editor; provide the message via gitpanel instead",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
