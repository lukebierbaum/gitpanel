"""Tags."""

from __future__ import annotations

from .models import Ref
from .refs import list_refs
from .remote import NO_PROMPT
from .runner import Git


async def tags(git: Git) -> list[Ref]:
    return await list_refs(git, "refs/tags")


async def create(git: Git, name: str, sha: str, message: str | None = None) -> None:
    if message:
        await git.run("tag", "-a", name, sha, "-F", "-", input=message.encode())
    else:
        await git.run("tag", name, sha)


async def delete(git: Git, name: str) -> None:
    await git.run("tag", "-d", name)


async def push_tag(git: Git, remote: str, name: str) -> None:
    await git.run("push", remote, f"refs/tags/{name}", env=NO_PROMPT)


async def delete_remote_tag(git: Git, remote: str, name: str) -> None:
    await git.run("push", remote, "--delete", f"refs/tags/{name}", env=NO_PROMPT)
