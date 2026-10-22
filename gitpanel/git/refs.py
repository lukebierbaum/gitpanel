"""Branches, remotes and tags."""

from __future__ import annotations

from .models import Ref
from .runner import Git

REF_FORMAT = (
    "%(refname)%00%(objectname)%00%(*objectname)%00%(upstream)%00%(upstream:track,nobracket)"
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
