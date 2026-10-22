"""Branches, remotes and tags."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from .models import Ref
from .runner import Git, GitError, GitResult

REF_FORMAT = (
    "%(refname)%00%(objectname)%00%(*objectname)%00%(upstream)%00%(upstream:track,nobracket)"
)
BRANCH_FORMAT = (
    "%(refname)%00%(objectname)%00%(upstream)%00%(upstream:track,nobracket)%00"
    "%(committerdate:unix)%00%(worktreepath)%00%(contents:subject)"
)


async def list_refs(git: Git, *patterns: str) -> list[Ref]:
    """Local branches, remote branches and tags (tags peeled to their commit)."""
    pats = patterns or ("refs/heads", "refs/remotes", "refs/tags")
    res = await git.run("for-each-ref", f"--format={REF_FORMAT}", *pats, read_only=True)
    refs: list[Ref] = []
    for line in res.text.splitlines():
        name, sha, peeled, upstream, track = (line.split("\0") + [""] * 5)[:5]
        if name.endswith("/HEAD") and name.startswith("refs/remotes/"):
            continue
        refs.append(
            Ref(
                name=name,
                sha=peeled or sha,
                upstream=upstream or None,
                extra={"track": track} if track else {},
            )
        )
    return refs


async def head_ref(git: Git) -> str | None:
    """Full name of the checked-out branch, or None when detached/unborn."""
    res = await git.run("symbolic-ref", "-q", "HEAD", check=False, read_only=True)
    return res.text.strip() or None


async def current_branch(git: Git) -> str | None:
    ref = await head_ref(git)
    return ref.removeprefix("refs/heads/") if ref else None


# --- branches -------------------------------------------------------------------


@dataclass(frozen=True)
class BranchRef:
    name: str  # full refname
    sha: str
    upstream: str | None = None  # full refname
    ahead: int = 0
    behind: int = 0
    gone: bool = False  # upstream configured but deleted on the remote
    date: datetime | None = None
    worktree: Path | None = None  # where it is checked out (any worktree)
    subject: str = ""

    @property
    def short(self) -> str:
        return self.name.removeprefix("refs/heads/").removeprefix("refs/remotes/")

    @property
    def is_remote(self) -> bool:
        return self.name.startswith("refs/remotes/")

    @property
    def is_local(self) -> bool:
        return self.name.startswith("refs/heads/")

    @property
    def is_tag(self) -> bool:
        return False

    @property
    def remote(self) -> str | None:
        return self.short.split("/", 1)[0] if self.is_remote else None

    @property
    def remote_branch(self) -> str | None:
        return self.short.split("/", 1)[1] if self.is_remote and "/" in self.short else None

    @property
    def upstream_short(self) -> str | None:
        return self.upstream.removeprefix("refs/remotes/") if self.upstream else None


_TRACK = re.compile(r"(ahead|behind) (\d+)")


def parse_track(track: str) -> tuple[int, int, bool]:
    if track == "gone":
        return 0, 0, True
    counts = {k: int(v) for k, v in _TRACK.findall(track)}
    return counts.get("ahead", 0), counts.get("behind", 0), False


async def branches(git: Git) -> list[BranchRef]:
    res = await git.run(
        "for-each-ref", f"--format={BRANCH_FORMAT}", "refs/heads", "refs/remotes", read_only=True
    )
    out: list[BranchRef] = []
    for line in res.text.splitlines():
        name, sha, upstream, track, date, wt, subject = (line.split("\0") + [""] * 7)[:7]
        if name.startswith("refs/remotes/") and name.endswith("/HEAD"):
            continue
        ahead, behind, gone = parse_track(track)
        out.append(
            BranchRef(
                name=name,
                sha=sha,
                upstream=upstream or None,
                ahead=ahead,
                behind=behind,
                gone=gone,
                date=datetime.fromtimestamp(int(date), UTC) if date.isdigit() else None,
                worktree=Path(wt) if wt else None,
                subject=subject,
            )
        )
    return out


async def recent_branches(git: Git, limit: int = 8) -> list[str]:
    """Most recently checked-out local branches (short names), newest first."""
    res = await git.run(
        "reflog", "show", "--format=%gs", "-n", "300", "HEAD", check=False, read_only=True
    )
    seen: list[str] = []
    existing = {b.short for b in await branches(git) if not b.is_remote}
    for line in res.text.splitlines():
        m = re.match(r"checkout: moving from (.+) to (.+)$", line)
        if not m:
            continue
        for name in (m.group(2), m.group(1)):
            if name in existing and name not in seen:
                seen.append(name)
        if len(seen) >= limit:
            break
    return seen[:limit]


class Favorites:
    """Starred branches, stored per repository (shared by all worktrees)."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> set[str]:
        try:
            return set(json.loads(self.path.read_text()))
        except (OSError, ValueError):
            return {"refs/heads/main", "refs/heads/master"}

    def toggle(self, name: str) -> bool:
        favs = self.load()
        on = name not in favs
        if on:
            favs.add(name)
        else:
            favs.discard(name)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(sorted(favs)))
        return on


# --- branch operations --------------------------------------------------------------


CheckoutMode = Literal["normal", "force"]


async def checkout(
    git: Git, target: str, *, mode: CheckoutMode = "normal", detach: bool = False
) -> GitResult:
    """Switch to `target`. `force` discards local changes and untracked files in the way."""
    args = ["switch", "-q"]
    if detach:
        args.append("--detach")
    if mode == "force":
        args.append("--discard-changes")
    return await git.run(*args, target)


def is_local_changes_error(err: GitError) -> bool:
    text = (err.result.stderr + err.result.text).lower()
    return "would be overwritten by checkout" in text or "please commit your changes" in text


async def smart_checkout(git: Git, target: str, *, detach: bool = False) -> GitResult:
    """PyCharm's Smart Checkout: stash local changes, switch, then unstash them.

    Returns the unstash result: not ok (with CONFLICT) when the changes clash with
    the target, in which case the stash is kept so nothing is lost.
    """
    await git.run(
        "stash", "push", "--include-untracked", "-q", "-m", f"gitpanel smart checkout to {target}"
    )
    try:
        await checkout(git, target, detach=detach)
    except GitError:
        await git.run("stash", "pop", "-q", "--index", check=False)  # put things back as they were
        raise
    # plain pop (not --index): it reports clashes as ordinary merge conflicts
    return await git.run("stash", "pop", check=False)


async def checkout_remote(git: Git, remote_ref: str) -> str:
    """Check out a remote branch, creating (or reusing) a local tracking branch."""
    short = remote_ref.removeprefix("refs/remotes/")
    local = short.split("/", 1)[1]
    exists = await git.try_rev_parse(f"refs/heads/{local}")
    if exists:
        await git.run("switch", "-q", local)
    else:
        await git.run("switch", "-q", "--track", "-c", local, short)
    return local


async def create(git: Git, name: str, start: str, *, checkout: bool = True) -> None:
    if checkout:
        await git.run("switch", "-q", "-c", name, start)
    else:
        await git.run("branch", name, start)


async def rename(git: Git, old: str, new: str) -> None:
    await git.run("branch", "-m", old, new)


async def delete(git: Git, name: str, *, force: bool = False) -> None:
    await git.run("branch", "-D" if force else "-d", name)


async def is_merged(git: Git, name: str, into: str = "HEAD") -> bool:
    res = await git.run("merge-base", "--is-ancestor", name, into, check=False, read_only=True)
    return res.ok


async def set_upstream(git: Git, branch: str, upstream: str) -> None:
    await git.run("branch", f"--set-upstream-to={upstream}", branch)


async def unset_upstream(git: Git, branch: str) -> None:
    await git.run("branch", "--unset-upstream", branch)


async def merge_into_current(git: Git, branch: str, *, no_ff: bool = False) -> GitResult:
    args = ["merge", "--no-edit"]
    if no_ff:
        args.append("--no-ff")
    return await git.run(*args, branch, check=False)


async def rebase_current_onto(git: Git, branch: str) -> GitResult:
    return await git.run("rebase", branch, check=False)


async def checkout_and_rebase_onto_current(git: Git, branch: str, current: str) -> GitResult:
    return await git.run("rebase", current, branch, check=False)
