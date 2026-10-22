"""Fetch, update project (pull), push dialog."""

from __future__ import annotations

from ...git import refs as R
from ...git import remote as RM
from ...git.runner import GitError
from ..registry import Action, ActionEnv, ActionError
from ..ui_protocol import Field
from .network import with_auth_retry


async def _fetch(env: ActionEnv) -> None:
    git = env.session.git
    res = await with_auth_retry(env, lambda: RM.fetch(git), ["fetch", "--all", "--prune"])
    if res is not None:
        env.ui.notify("Fetched all remotes")


async def _update(env: ActionEnv) -> None:
    """PyCharm "Update Project": pull the current branch, merge or rebase."""
    git = env.session.git
    if env.session.active.branch is None:
        raise ActionError("HEAD is detached — check out a branch to update it")
    mode = await env.ui.choose(
        "Update Project",
        [
            ("rebase", "Rebase local commits onto the upstream"),
            ("merge", "Merge the upstream into the current branch"),
        ],
    )
    if mode is None:
        return
    res = await with_auth_retry(
        env,
        lambda: RM.pull(git, mode),  # type: ignore[arg-type]
        ["pull", "--rebase" if mode == "rebase" else "--no-rebase"],
    )
    if res is None:
        return
    if not res.ok:
        if "CONFLICT" in res.text + res.stderr:
            env.ui.notify(
                "Update stopped with conflicts — resolve them, then Continue", severity="warning"
            )
            return
        raise GitError(res)
    if env.entry:
        env.entry.op = f"Update project ({mode})"
    env.ui.notify("Project updated")


async def push_flow(env: ActionEnv, branch: str | None = None) -> bool:
    """PyCharm's Push dialog for `branch` (default: current). Returns True when pushed."""
    git = env.session.git
    branch = branch or env.session.active.branch_short
    if not branch:
        raise ActionError("HEAD is detached — nothing to push")
    remotes = await RM.remotes(git)
    if not remotes:
        raise ActionError("No remotes configured")
    info = next((b for b in await R.branches(git) if b.short == branch and b.is_local), None)
    upstream = info.upstream_short if info else None
    commits = await RM.outgoing(git, branch, upstream)
    target_remote = upstream.split("/", 1)[0] if upstream else remotes[0].name
    target_branch = upstream.split("/", 1)[1] if upstream else branch
    lines = [f"{c.short}  {c.subject}" for c in commits[:40]]
    if len(commits) > 40:
        lines.append(f"… and {len(commits) - 40} more")
    body = f"{branch} → {target_remote}/{target_branch}\n\n" + (
        "\n".join(lines) if lines else "No commits to push."
    )
    if info is not None and info.behind:
        body += f"\n\n⚠ {upstream} has {info.behind} commit(s) you don't have."
    answer = await env.ui.form(
        f"Push {len(commits)} commit{'s' if len(commits) != 1 else ''}",
        [
            Field(
                "remote",
                "Remote",
                "choice",
                target_remote,
                options=[(r.name, f"{r.name}  {r.push_url}") for r in remotes],
            ),
            Field("remote_branch", "Remote branch", default=target_branch),
            Field("set_upstream", "Set upstream", "bool", upstream is None),
            Field("force", "Force with lease", "bool", False),
            Field("tags", "Push tags", "bool", False),
        ],
        body=body,
        ok_label="Push",
    )
    if not answer:
        return False
    remote = str(answer["remote"])
    rbranch = str(answer["remote_branch"]).strip() or branch
    argv = ["push", remote, f"refs/heads/{branch}:refs/heads/{rbranch}"]
    res = await with_auth_retry(
        env,
        lambda: RM.push(
            git,
            branch,
            remote,
            remote_branch=rbranch,
            set_upstream=bool(answer["set_upstream"]),
            force_with_lease=bool(answer["force"]),
            tags=bool(answer["tags"]),
        ),
        argv,
    )
    if res is None:
        return False
    env.ui.notify(f"Pushed {branch} to {remote}/{rbranch}")
    return True


async def _push(env: ActionEnv) -> None:
    await push_flow(env)


def actions() -> list[Action]:
    return [
        Action("remote.fetch", "Fetch", _fetch, key="F", group="Remote", mutating=True),
        Action(
            "remote.update", "Update Project…", _update, key="ctrl+t", group="Remote", mutating=True
        ),
        Action("remote.push", "Push…", _push, key="P", group="Remote", mutating=True),
    ]
