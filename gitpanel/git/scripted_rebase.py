"""Drive `git rebase -i` programmatically. See docs/architecture.md §9.

Used for reword/fixup/squash/drop from the Log, the interactive rebase
editor and the patch builder. Callers run it inside one journal mutation so
the whole rewrite is a single undo step.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from .operations import OperationState, OpKind, detect
from .runner import Git, GitError, GitResult
from .session import RepoSession

Action = Literal[
    "pick", "reword", "edit", "squash", "fixup", "drop",
    "exec", "break", "label", "reset", "merge", "update-ref",
]  # fmt: skip


class RebaseError(Exception):
    pass


@dataclass
class TodoItem:
    action: Action
    sha: str | None
    subject: str = ""
    arg: str | None = None

    def line(self) -> str:
        if self.action in ("exec", "label", "reset", "update-ref", "break"):
            return f"{self.action} {self.arg or ''}".rstrip()
        return f"{self.action} {self.sha} {self.subject}".rstrip()


@dataclass(frozen=True)
class StopInfo:
    sha: str
    item: TodoItem | None


@dataclass(frozen=True)
class RebaseResult:
    status: Literal["done", "conflict", "stopped", "aborted"]
    state: OperationState | None = None
    output: str = ""


async def todo_for(git: Git, base: str | None) -> list[TodoItem]:
    """The default todo for rebasing HEAD onto `base` (None = root), oldest first."""
    rng = f"{base}..HEAD" if base else "HEAD"
    res = await git.run(
        "log", "--reverse", "--topo-order", "--format=%H%x1f%P%x1f%s", rng, read_only=True
    )
    items: list[TodoItem] = []
    for line in res.text.splitlines():
        sha, parents, subject = line.split("\x1f", 2)
        if len(parents.split()) > 1:
            raise RebaseError(
                "The range contains merge commits; rebasing merges is not supported yet"
            )
        items.append(TodoItem("pick", sha, subject))
    return items


def _env(session: RepoSession) -> dict[str, str]:
    d = session.private_dir() / "rebase"
    return {"GITPANEL_EDITOR_DIR": str(d), "GITPANEL_GIT_DIR": str(session.git_dir)}


def _write_instructions(
    session: RepoSession, todo: Sequence[TodoItem], messages: Mapping[str, str]
) -> None:
    d = session.private_dir() / "rebase"
    if d.exists():
        shutil.rmtree(d)
    d.mkdir(parents=True)
    (d / "todo").write_text("".join(item.line() + "\n" for item in todo))
    (d / "messages.json").write_text(json.dumps(dict(messages)))
    (d / "items.json").write_text(
        json.dumps(
            [{"action": i.action, "sha": i.sha, "subject": i.subject, "arg": i.arg} for i in todo]
        )
    )


def _clear_instructions(session: RepoSession) -> None:
    d = session.private_dir() / "rebase"
    if d.exists():
        shutil.rmtree(d)


def _item_for(session: RepoSession, sha: str | None) -> TodoItem | None:
    if not sha:
        return None
    f = session.private_dir() / "rebase" / "items.json"
    if not f.exists():
        return None
    for d in json.loads(f.read_text()):
        if d["sha"] and (d["sha"].startswith(sha) or sha.startswith(d["sha"])):
            return TodoItem(d["action"], d["sha"], d["subject"], d["arg"])
    return None


OnStop = Callable[[StopInfo], Awaitable[None]]


async def run(
    session: RepoSession,
    base: str | None,
    todo: Sequence[TodoItem],
    *,
    messages: Mapping[str, str] | None = None,
    on_stop: OnStop | None = None,
    autostash: bool = True,
) -> RebaseResult:
    """Rebase HEAD onto `base` (None = --root) following `todo`."""
    if await detect(session.git) is not None:
        raise RebaseError("Another operation is in progress")
    _write_instructions(session, todo, messages or {})
    args = ["rebase", "--interactive", "--no-update-refs"]
    if autostash:
        args.append("--autostash")
    args.append(base if base else "--root")
    res = await session.git.run(*args, env=_env(session), check=False)
    return await _drive(session, res, on_stop)


async def resume(session: RepoSession, on_stop: OnStop | None = None) -> RebaseResult:
    """Continue after conflicts were resolved (or after a stop)."""
    res = await session.git.run("rebase", "--continue", env=_env(session), check=False)
    return await _drive(session, res, on_stop)


async def abort(session: RepoSession) -> None:
    await session.git.run("rebase", "--abort")
    _clear_instructions(session)


async def _drive(session: RepoSession, res: GitResult, on_stop: OnStop | None) -> RebaseResult:
    git = session.git
    for _ in range(10_000):
        state = await detect(git)
        if state is None:
            _clear_instructions(session)
            if not res.ok:
                raise GitError(res)
            return RebaseResult("done", None, res.text + res.stderr)
        if state.kind is not OpKind.REBASE:
            return RebaseResult("stopped", state, res.stderr)
        if state.conflicts or (not res.ok and "CONFLICT" in (res.text + res.stderr)):
            return RebaseResult("conflict", state, res.text + res.stderr)
        stopped_at = state.current or await git.try_rev_parse("HEAD")
        item = _item_for(session, stopped_at)
        if item is None or item.action != "edit" or on_stop is None:
            # a conflict-free stop we don't drive ourselves: hand over to the user
            return RebaseResult("stopped", state, res.text + res.stderr)
        head = await git.rev_parse("HEAD")
        await on_stop(StopInfo(head, item))
        res = await git.run("rebase", "--continue", env=_env(session), check=False)
    raise RebaseError("rebase did not finish")


# --- plans -------------------------------------------------------------------------------


def validate(todo: Sequence[TodoItem]) -> None:
    live = [t for t in todo if t.action != "drop"]
    if not live:
        raise RebaseError("At least one commit must be kept")
    if live[0].action in ("squash", "fixup"):
        raise RebaseError("The first commit can't be squashed or fixed up into a previous one")


def squash_plan(todo: list[TodoItem], shas: Sequence[str]) -> tuple[list[TodoItem], str]:
    """Squash `shas` into the oldest of them. Returns (new todo, sha that gets the message)."""
    wanted = set(shas)
    chosen = [t for t in todo if t.sha in wanted]
    if len(chosen) < 2:
        raise RebaseError("Select at least two commits on the current branch")
    first = chosen[0]
    new = [t for t in todo if t.sha not in wanted]
    at = sum(1 for t in todo[: todo.index(first)] if t.sha not in wanted)
    group = [TodoItem("pick", first.sha, first.subject)] + [
        TodoItem("squash", t.sha, t.subject) for t in chosen[1:]
    ]
    new[at:at] = group
    return new, chosen[-1].sha or ""


def fixup_plan(todo: list[TodoItem], target: str, fixup_sha: str) -> list[TodoItem]:
    """Move `fixup_sha` right after `target` as a fixup."""
    fix = next(t for t in todo if t.sha == fixup_sha)
    new = [t for t in todo if t.sha != fixup_sha]
    idx = next(i for i, t in enumerate(new) if t.sha == target)
    new.insert(idx + 1, TodoItem("fixup", fix.sha, fix.subject))
    return new
