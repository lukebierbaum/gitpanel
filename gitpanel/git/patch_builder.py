"""Move parts of a commit around (lazygit's "custom patch", done through scripted_rebase)."""

from __future__ import annotations

from collections.abc import Sequence

from . import patch as P
from . import scripted_rebase as SR
from .log import commit_details, is_ancestor
from .runner import Git
from .session import RepoSession


class PatchBuilderError(Exception):
    pass


async def _selection_patch(git: Git, sha: str, sel: P.Selection) -> tuple[bytes, bytes]:
    """(forward patch, patch for reverse application) of `sel` within commit `sha`."""
    diffs = await P.load_diff(git, P.CommitDiff(sha), sorted(sel.paths()))
    forward = P.build_patch(diffs, sel)
    reverse = P.build_patch(diffs, sel, reverse=True)
    if not forward.strip():
        raise PatchBuilderError("Nothing selected")
    return forward, reverse


async def _check_on_branch(git: Git, *shas: str) -> str:
    head = await git.try_rev_parse("HEAD")
    if not head:
        raise PatchBuilderError("No commits yet")
    for sha in shas:
        if not await is_ancestor(git, sha, head):
            raise PatchBuilderError(f"{sha[:8]} is not on the current branch")
    status = await git.run("status", "--porcelain", "--untracked-files=no", read_only=True)
    if status.text.strip():
        raise PatchBuilderError("Commit or stash your local changes first")
    return head


async def _amend_without(git: Git, reverse: bytes) -> None:
    await P.apply(git, reverse, to="both", reverse=True)
    await git.run("commit", "--amend", "--no-edit", "--allow-empty", "--no-verify", "-q")


async def _rebase_with_edits(
    session: RepoSession, oldest: str, handlers: dict[str, SR.OnStop]
) -> SR.RebaseResult:
    details = await commit_details(session.git, oldest)
    base = details.parents[0] if details.parents else None
    todo = await SR.todo_for(session.git, base)
    for item in todo:
        if item.sha in handlers:
            item.action = "edit"

    async def on_stop(stop: SR.StopInfo) -> None:
        assert stop.item is not None and stop.item.sha is not None
        await handlers[stop.item.sha](stop)

    return await SR.run(session, base, todo, on_stop=on_stop, autostash=False)


async def remove_from_commit(session: RepoSession, sha: str, sel: P.Selection) -> SR.RebaseResult:
    git = session.git
    head = await _check_on_branch(git, sha)
    _, reverse = await _selection_patch(git, sha, sel)
    if sha == head:
        await _amend_without(git, reverse)
        return SR.RebaseResult("done")

    async def drop_part(_stop: SR.StopInfo) -> None:
        await _amend_without(git, reverse)

    return await _rebase_with_edits(session, sha, {sha: drop_part})


async def move_to_new_commit(
    session: RepoSession, sha: str, sel: P.Selection, message: str
) -> SR.RebaseResult:
    git = session.git
    head = await _check_on_branch(git, sha)
    forward, reverse = await _selection_patch(git, sha, sel)

    async def split(_stop: SR.StopInfo | None = None) -> None:
        await _amend_without(git, reverse)
        await P.apply(git, forward, to="both")
        await git.run("commit", "--no-verify", "-q", "-F", "-", input=message.encode())

    if sha == head:
        await split()
        return SR.RebaseResult("done")
    return await _rebase_with_edits(session, sha, {sha: split})


async def move_to_commit(
    session: RepoSession, sha: str, sel: P.Selection, target: str
) -> SR.RebaseResult:
    git = session.git
    if target == sha:
        raise PatchBuilderError("Choose a different commit")
    await _check_on_branch(git, sha, target)
    forward, reverse = await _selection_patch(git, sha, sel)

    async def remove(_stop: SR.StopInfo) -> None:
        await _amend_without(git, reverse)

    async def add(_stop: SR.StopInfo) -> None:
        await P.apply(git, forward, to="both", three_way=True)
        await git.run("commit", "--amend", "--no-edit", "--no-verify", "-q")

    if await is_ancestor(git, target, sha):
        # moving into an older commit: once it carries the change, picking `sha`
        # on top no longer re-applies it, so only the target needs editing
        return await _rebase_with_edits(session, target, {target: add})
    return await _rebase_with_edits(session, sha, {sha: remove, target: add})


async def move_to_index(session: RepoSession, sha: str, sel: P.Selection) -> SR.RebaseResult:
    git = session.git
    forward, _ = await _selection_patch(git, sha, sel)
    res = await remove_from_commit(session, sha, sel)
    if res.status == "done":
        await P.apply(git, forward, to="both", three_way=True)
    return res


async def revert_in_worktree(git: Git, sha: str, sel: P.Selection) -> None:
    _, reverse = await _selection_patch(git, sha, sel)
    await P.apply(git, reverse, to="worktree", reverse=True, three_way=False)


def selection_for_files(paths: Sequence[str]) -> P.Selection:
    return P.Selection.files(paths)
