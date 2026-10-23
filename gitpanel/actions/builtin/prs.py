"""Pull request actions (PR window, plus "Create Pull Request" on branches)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ...forge.github import GitHub, detect
from ...forge.models import PullRequest
from ...git import refs as R
from ...git import remote as RM
from ...git import worktrees as W
from ...git.journal import snapshot_worktree
from ..context import Scope, SelectionContext
from ..registry import Action, ActionEnv, ActionError
from ..ui_protocol import Field

SCREEN = frozenset({"prs"})
PR = frozenset({Scope.PR})


def _pr(ctx: SelectionContext) -> PullRequest | None:
    return ctx.form.get("pr")


def _provider(ctx: SelectionContext) -> GitHub | None:
    return ctx.form.get("provider")


def _where(ctx: SelectionContext) -> str:
    return str(ctx.form.get("where", ""))


def _open(ctx: SelectionContext) -> bool:
    pr = _pr(ctx)
    return pr is not None and pr.state == "OPEN"


def _need(env: ActionEnv) -> tuple[GitHub, PullRequest]:
    provider, pr = _provider(env.ctx), _pr(env.ctx)
    if provider is None or pr is None:
        raise ActionError("Select a pull request first")
    return provider, pr


def _refresh(env: ActionEnv, number: int | None = None) -> None:
    env.ui.post("prs.refresh", select=number)


# -- browse ---------------------------------------------------------------------------------


async def _checkout(env: ActionEnv) -> None:
    provider, pr = _need(env)
    git = env.session.git
    holder = W.for_branch(await env.session.worktrees(), pr.head) if not pr.cross_repo else None
    if holder is not None and holder.path.resolve() != env.session.active.path.resolve():
        if await env.ui.confirm(
            "Already checked out in another worktree",
            f"{pr.head} is checked out in {holder.path}.\nSwitch gitpanel to that worktree?",
        ):
            await env.session.switch(holder.path)
            env.ui.open_screen("commit")
        return
    try:
        await provider.checkout(pr.number)
    except Exception as e:  # noqa: BLE001 - offer smart / force for blocked checkouts
        text = str(getattr(e, "details", e)).lower()
        if "would be overwritten" not in text and "commit your changes" not in text:
            raise
        choice = await env.ui.choose(
            f"Local changes would be overwritten by checking out #{pr.number}",
            [
                ("smart", "Smart Checkout — carry your local changes over"),
                ("force", "Force Checkout — discard your local changes (undo with z)"),
            ],
        )
        if choice == "smart":
            await git.run(
                "stash",
                "push",
                "--include-untracked",
                "-q",
                "-m",
                f"gitpanel: before checking out PR #{pr.number}",
            )
            await provider.checkout(pr.number)
            res = await git.run("stash", "pop", check=False)
            if not res.ok:
                env.ui.notify(
                    "Your changes conflict with the PR — resolve them; they're also "
                    "kept in the stash",
                    severity="warning",
                )
        elif choice == "force":
            if env.entry is not None and env.entry.backup is None:
                env.entry.backup = await snapshot_worktree(git, f"force checkout PR {pr.number}")
            await git.run("reset", "--hard", "-q")
            await git.run("clean", "-fdq")
            await provider.checkout(pr.number)
        else:
            return
    if env.entry:
        env.entry.op = f"Checkout PR #{pr.number}"
    env.ui.notify(f"Checked out #{pr.number} ({pr.head})")


async def _checkout_worktree(env: ActionEnv) -> None:
    provider, pr = _need(env)
    git = env.session.git
    head = await provider.fetch_head(pr)
    main = (await env.session.worktrees())[0].path
    existing = {b.short: b for b in await R.branches(git) if b.is_local}
    branch = pr.head if not pr.cross_repo else f"pr-{pr.number}-{pr.head}"
    if branch in existing and existing[branch].sha != head:
        branch = f"pr-{pr.number}"
    template = env.setting("worktree_template", W.DEFAULT_TEMPLATE)
    answer = await env.ui.form(
        f"Check out #{pr.number} in a new worktree",
        [
            Field("branch", "Local branch", default=branch),
            Field("path", "Path", default=str(W.default_path(main, branch, template))),
            Field("switch", "Switch to it", "bool", True),
        ],
        ok_label="Create worktree",
    )
    if not answer:
        return
    branch = str(answer["branch"]).strip() or branch
    path = Path(str(answer["path"])).expanduser()
    if branch in existing:
        await W.add(git, path, branch=branch)
    else:
        await W.add(git, path, new_branch=branch, start=head)
        if not pr.cross_repo:
            remote = await provider.remote_name()
            fetched = await git.run(
                "fetch", "--quiet", remote, pr.head, env=RM.NO_PROMPT, check=False
            )
            if fetched.ok:
                await git.run("update-ref", f"refs/remotes/{remote}/{pr.head}", "FETCH_HEAD")
                await git.run(
                    "branch", f"--set-upstream-to={remote}/{pr.head}", branch, check=False
                )
    if env.entry:
        env.entry.op = f"Worktree for PR #{pr.number}"
    env.ui.notify(f"Checked out #{pr.number} in {path}")
    if answer["switch"]:
        await env.session.switch(path)
        env.ui.open_screen("commit")


async def _open_browser(env: ActionEnv) -> None:
    _, pr = _need(env)
    env.ui.post("open_url", url=pr.url)


async def _copy_url(env: ActionEnv) -> None:
    _, pr = _need(env)
    env.ui.post("clipboard", text=pr.url)
    env.ui.notify(f"Copied {pr.url}")


async def _open_check(env: ActionEnv) -> None:
    check = env.ctx.form.get("check")
    if check is None or not check.url:
        raise ActionError("This check has no link")
    env.ui.post("open_url", url=check.url)


# -- act -------------------------------------------------------------------------------------


async def create_pr_flow(env: ActionEnv, branch: str | None = None) -> None:
    provider = _provider(env.ctx) or await detect(env.session)
    if provider is None:
        raise ActionError("This repository has no GitHub remote (or gh isn't installed)")
    git = env.session.git
    branch = branch or env.session.active.branch_short
    if not branch:
        raise ActionError("HEAD is detached — check out the branch to open a PR for")
    info = await provider.repo()
    remote = await provider.remote_name()
    local = next((b for b in await R.branches(git) if b.is_local and b.short == branch), None)
    if local is None:
        raise ActionError(f"No local branch {branch}")
    if local.upstream is None or local.ahead:
        why = (
            "isn't on GitHub yet"
            if local.upstream is None
            else f"has {local.ahead} unpushed commit(s)"
        )
        if not await env.ui.confirm("Push first?", f"{branch} {why}. Push it to {remote} now?"):
            return
        await RM.push(git, branch, remote, set_upstream=local.upstream is None)
    base = info.default_branch
    res = await git.run(
        "log",
        "--reverse",
        "--format=%s%x1f%b%x1e",
        f"{remote}/{base}..{branch}",
        check=False,
        read_only=True,
    )
    commits = [c.split("\x1f", 1) for c in res.text.split("\x1e") if c.strip()]
    subjects = [c[0].strip() for c in commits]
    if len(commits) == 1:
        title, body = subjects[0], (commits[0][1].strip() if len(commits[0]) > 1 else "")
    else:
        title = branch.rsplit("/", 1)[-1].replace("-", " ").replace("_", " ").capitalize()
        body = "\n".join(f"- {s}" for s in subjects)
    answer = await env.ui.form(
        f"Create pull request: {branch} → {base}",
        [
            Field("base", "Base branch", default=base),
            Field("title", "Title", default=title),
            Field("reviewers", "Reviewers (comma separated)", placeholder="octocat, hubot"),
            Field("draft", "Draft", "bool", False),
        ],
        body=(f"{len(commits)} commit(s):\n" + "\n".join(f"  {s}" for s in subjects[:15])),
        ok_label="Continue",
    )
    if not answer:
        return
    body = await env.ui.prompt("Description", default=body, multiline=True)
    if body is None:
        return
    reviewers = [r.strip() for r in str(answer["reviewers"]).split(",") if r.strip()]
    url = await provider.create(
        branch,
        str(answer["base"]).strip() or base,
        str(answer["title"]).strip() or title,
        body,
        draft=bool(answer["draft"]),
        reviewers=reviewers,
    )
    env.ui.notify(f"Created {url}")
    number = int(url.rstrip("/").rsplit("/", 1)[-1]) if url.rsplit("/", 1)[-1].isdigit() else None
    _refresh(env, number)


async def _create(env: ActionEnv) -> None:
    await create_pr_flow(env)


async def _create_for_branch(env: ActionEnv) -> None:
    ref = env.ctx.refs[0]
    await create_pr_flow(env, ref.short)


async def _review(env: ActionEnv) -> None:
    provider, pr = _need(env)
    verdict = await env.ui.choose(
        f"Review #{pr.number}",
        [("approve", "Approve"), ("request-changes", "Request changes"), ("comment", "Comment")],
    )
    if verdict is None:
        return
    body = await env.ui.prompt(
        "Review comment" + (" (optional)" if verdict == "approve" else ""), multiline=True
    )
    if body is None:
        return
    if verdict != "approve" and not body.strip():
        raise ActionError("A review that requests changes or comments needs some text")
    await provider.review(pr.number, verdict, body)  # type: ignore[arg-type]
    env.ui.notify(f"Review submitted on #{pr.number}")
    _refresh(env, pr.number)


async def _comment(env: ActionEnv) -> None:
    provider, pr = _need(env)
    body = await env.ui.prompt(f"Comment on #{pr.number}", multiline=True)
    if not body or not body.strip():
        return
    await provider.comment(pr.number, body)
    env.ui.notify(f"Commented on #{pr.number}")
    _refresh(env, pr.number)


async def _merge(env: ActionEnv) -> None:
    provider, pr = _need(env)
    if not pr.detailed:  # the warnings below need mergeability, checks and reviews
        pr = await provider.pr(pr.number)
    default = env.setting("merge_method", "squash")
    methods = [
        ("squash", "Squash and merge"),
        ("merge", "Create a merge commit"),
        ("rebase", "Rebase and merge"),
    ]
    warn = []
    if pr.draft:
        warn.append("This PR is a draft.")
    if pr.ci == "failure":
        warn.append("Some checks are failing.")
    if pr.review_decision == "CHANGES_REQUESTED":
        warn.append("Changes were requested.")
    if pr.mergeable == "CONFLICTING":
        warn.append("It has merge conflicts with the base branch.")
    answer = await env.ui.form(
        f"Merge #{pr.number} into {pr.base}",
        [
            Field("method", "Method", "choice", default, options=methods),
            Field("delete", "Delete the branch afterwards", "bool", not pr.cross_repo),
            Field("auto", "Merge automatically when checks pass", "bool", pr.ci == "pending"),
        ],
        body=f"{pr.title}\n{pr.head} → {pr.base}" + ("\n\n⚠ " + " ".join(warn) if warn else ""),
        ok_label="Merge",
        danger=bool(warn),
    )
    if not answer:
        return
    await provider.merge(
        pr.number, answer["method"], delete_branch=bool(answer["delete"]), auto=bool(answer["auto"])
    )
    if env.entry:
        env.entry.op = f"Merge PR #{pr.number}"
    env.ui.notify(
        f"#{pr.number} will merge when checks pass" if answer["auto"] else f"Merged #{pr.number}"
    )
    _refresh(env, pr.number)


async def _close(env: ActionEnv) -> None:
    provider, pr = _need(env)
    await provider.close(pr.number)
    env.ui.notify(f"Closed #{pr.number}")
    _refresh(env, pr.number)


async def _reopen(env: ActionEnv) -> None:
    provider, pr = _need(env)
    await provider.reopen(pr.number)
    env.ui.notify(f"Reopened #{pr.number}")
    _refresh(env, pr.number)


async def _ready(env: ActionEnv) -> None:
    provider, pr = _need(env)
    await provider.ready(pr.number, draft=not pr.draft)
    env.ui.notify(f"#{pr.number} is now " + ("a draft" if not pr.draft else "ready for review"))
    _refresh(env, pr.number)


async def _rerun(env: ActionEnv) -> None:
    provider, pr = _need(env)
    runs = await provider.rerun_failed(pr)
    env.ui.notify(f"Re-running failed jobs in {len(runs)} workflow run(s)")
    _refresh(env, pr.number)


# -- inline review threads ---------------------------------------------------------------------


async def _line_comment(env: ActionEnv) -> None:
    provider, pr = _need(env)
    line: dict[str, Any] = env.ctx.form["line"]
    body = await env.ui.prompt(f"Comment on {line['path']}:{line['line']}", multiline=True)
    if not body or not body.strip():
        return
    await provider.add_line_comment(pr, line["path"], int(line["line"]), line["side"], body)
    env.ui.notify("Comment added")
    env.ui.post("prs.threads")


async def _reply(env: ActionEnv) -> None:
    provider, pr = _need(env)
    thread = env.ctx.form["thread"]
    body = await env.ui.prompt(f"Reply to {thread.comments[0].author}", multiline=True)
    if not body or not body.strip():
        return
    await provider.reply(pr, thread.comments[0].database_id, body)
    env.ui.notify("Reply posted")
    env.ui.post("prs.threads")


async def _resolve(env: ActionEnv) -> None:
    provider, _pr_ = _need(env)
    thread = env.ctx.form["thread"]
    await provider.set_resolved(thread.id, not thread.resolved)
    env.ui.notify("Thread " + ("reopened" if thread.resolved else "resolved"))
    env.ui.post("prs.threads")


async def _show_prs(env: ActionEnv) -> None:
    env.ui.open_screen("prs")


def actions() -> list[Action]:
    def a(id_, label, run, key=None, when=_open, group="Pull request", **kw):  # type: ignore[no-untyped-def]
        return Action(
            id_, label, run, scopes=PR, when=when, key=key, group=group, screens=SCREEN, **kw
        )

    in_list = lambda c: _pr(c) is not None and _where(c) == "list"  # noqa: E731
    has_pr = lambda c: _pr(c) is not None  # noqa: E731
    return [
        Action("app.show_prs", "Pull Requests", _show_prs, key="7,alt+7", group="Tool windows"),
        a("pr.checkout", "Check Out", _checkout, "enter", in_list, mutating=True),
        a(
            "pr.checkout_worktree",
            "Check Out in New Worktree…",
            _checkout_worktree,
            "w",
            has_pr,
            mutating=True,
        ),
        a("pr.open_browser", "Open on GitHub", _open_browser, "o", has_pr),
        a("pr.copy_url", "Copy Link", _copy_url, "y", has_pr),
        a(
            "pr.open_check",
            "Open Check Details",
            _open_check,
            "enter",
            lambda c: c.form.get("check") is not None,
            group="Checks",
        ),
        Action(
            "pr.create",
            "Create Pull Request…",
            _create,
            key="n",
            group="Pull request",
            screens=SCREEN,
            mutating=True,
            when=lambda c: c.form.get("provider") is not None,
        ),
        Action(
            "branch.create_pr",
            "Create Pull Request…",
            _create_for_branch,
            scopes=frozenset({Scope.BRANCH}),
            when=lambda c: bool(c.refs) and c.refs[0].is_local,
            group="Pull request",
            screens=frozenset({"branches", "log"}),
            mutating=True,
        ),
        a("pr.review", "Review…", _review, "r"),
        a("pr.comment", "Comment…", _comment, "m"),
        a("pr.merge", "Merge…", _merge, "M", mutating=True),
        a(
            "pr.close",
            "Close Pull Request",
            _close,
            confirm="Close pull request #{pr} without merging?",
        ),
        a(
            "pr.reopen",
            "Reopen Pull Request",
            _reopen,
            when=lambda c: _pr(c) is not None and _pr(c).state == "CLOSED",
        ),  # type: ignore[union-attr]
        a("pr.ready", "Toggle Draft / Ready for Review", _ready),
        a(
            "pr.rerun_failed",
            "Re-run Failed Checks",
            _rerun,
            when=lambda c: _open(c) and _pr(c).ci == "failure",
        ),  # type: ignore[union-attr]
        a(
            "pr.line_comment",
            "Comment on This Line…",
            _line_comment,
            "c",
            when=lambda c: _open(c) and c.form.get("line") is not None,
            group="Review",
        ),
        a(
            "pr.reply",
            "Reply to Thread…",
            _reply,
            "R",
            when=lambda c: _open(c) and c.form.get("thread") is not None,
            group="Review",
        ),
        a(
            "pr.resolve",
            "Resolve / Unresolve Thread",
            _resolve,
            "x",
            when=lambda c: c.form.get("thread") is not None,
            group="Review",
        ),
    ]
