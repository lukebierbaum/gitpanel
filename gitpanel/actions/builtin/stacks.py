"""Stacked pull requests: link / publish as a GitHub stack, a new branch on top, restack,
and merging a stack (GitHub's atomic stack merge via `gh stack`)."""

from __future__ import annotations

from typing import Any

from ...forge.github import GitHub
from ...forge.models import PullRequest
from ...forge.stacks import Stack
from ...git import refs as R
from ...git import remote as RM
from ...git.operations import detect as detect_operation
from ...git.stack import RestackLayer, is_ancestor, read_gh_stack, restack
from ..registry import Action, ActionEnv, ActionError
from ..ui_protocol import Field

SCREEN = frozenset({"prs"})
GROUP = "Stack"


def _stack(env: ActionEnv) -> tuple[GitHub, Stack, int]:
    provider, stack = env.ctx.form.get("provider"), env.ctx.form.get("stack")
    if provider is None or stack is None:
        raise ActionError("Select a pull request or branch in a stack first")
    return provider, stack, int(env.ctx.form.get("stack_index", 0))


def _span(numbers: list[int]) -> str:
    if len(numbers) == 1:
        return f"#{numbers[0]}"
    return f"#{numbers[0]}–#{numbers[-1]}" if len(numbers) > 2 else f"#{numbers[0]}, #{numbers[1]}"


def _refresh(env: ActionEnv, number: int | None = None) -> None:
    env.ui.post("prs.refresh", select=number)


# -- L: make it a GitHub stack ----------------------------------------------------------------


def _linkable(ctx: Any) -> bool:
    stack = ctx.form.get("stack")
    return stack is not None and (stack.kind != "github" or stack.local_layers > 0)


async def _link(env: ActionEnv) -> None:
    provider, stack, _ = _stack(env)
    bare = [ly.branch for ly in stack.layers if ly.pr is None]
    if stack.kind == "github":
        if not await env.ui.confirm(
            f"Add to {stack.label}?",
            f"{', '.join(bare)} will be pushed and get draft pull requests on top of "
            f"{stack.label}.",
        ):
            return
        out = await provider.stack_link([str(stack.number), *bare])
    elif stack.kind == "chain":
        if any(ly.depth != i for i, ly in enumerate(stack.layers)):
            raise ActionError(
                "These PRs branch off each other (more than one PR on the same base); "
                "a GitHub stack is a single line of PRs"
            )
        prs = stack.prs
        if not await env.ui.confirm(
            "Make a GitHub stack?",
            f"Link {_span(prs)} as a stack on GitHub (gh stack link). Their branches and "
            "bases stay as they are; merging then works for the whole stack at once.",
        ):
            return
        out = await provider.stack_link([str(n) for n in prs])
    else:
        with_pr = [ly for ly in stack.layers if ly.pr is not None]
        body = (
            f"Push {len(stack.layers)} branches ({', '.join(ly.branch for ly in stack.layers)}) "
            f"and open {len(bare)} draft pull request{'s' * (len(bare) != 1)}, each based on "
            f"the one below, onto {stack.base}"
        )
        if with_pr:
            body += f"; {_span([ly.pr for ly in with_pr if ly.pr])} already exist"
        if not await env.ui.confirm("Publish as a GitHub stack?", body + "."):
            return
        out = await provider.stack_link([ly.branch for ly in stack.layers], base=stack.base)
    env.ui.notify(out.splitlines()[-1] if out else "Stack linked on GitHub")
    _refresh(env)


# -- N: a new branch on top --------------------------------------------------------------------


def _next_name(branch: str, taken: set[str]) -> str:
    stem, _, tail = branch.rpartition("-")
    if stem and tail.isdigit():
        stem, n = stem, int(tail) + 1
    else:
        stem, n = branch, 2
    while f"{stem}-{n}" in taken:
        n += 1
    return f"{stem}-{n}"


async def _on_top(env: ActionEnv) -> None:
    provider = env.ctx.form.get("provider")
    pr: PullRequest | None = env.ctx.form.get("pr")
    row = env.ctx.form.get("branch_row")
    git = env.session.git
    parent = pr.head if pr is not None else row.branch if row is not None else None
    if provider is None or parent is None:
        raise ActionError("Select a pull request or branch first")
    local = {b.short for b in await R.branches(git) if b.is_local}
    if parent not in local:
        if pr is None or pr.cross_repo:
            raise ActionError(f"{parent} isn't a branch of this repository")
        head = await provider.fetch_head(pr)  # the PR's branch, as a local branch
        await R.create(git, parent, head, checkout=False)
        local.add(parent)
    name = await env.ui.prompt(
        f"New branch on top of {parent}", default=_next_name(parent, local), placeholder="name"
    )
    if not name:
        return
    if name in local:
        raise ActionError(f"There's already a branch called {name}")
    await R.create(git, name, parent, checkout=True)
    if env.entry:
        env.entry.op = f"New branch {name} on top of {parent}"
    env.ui.notify(f"On {name}, on top of {parent} — commit, then n to open its PR")
    _refresh(env)


# -- S: restack --------------------------------------------------------------------------------


async def _boundary(
    env: ActionEnv,
    provider: GitHub,
    head: str,
    candidates: list[str | None],
    new_base: str,
    *,
    bottom: bool,
) -> str:
    """Where a layer's own commits start: the first candidate its head contains. For the
    bottom layer, also a recently merged PR's head (a squash-merged parent) and finally the
    merge base with the new base."""
    git = env.session.git
    for c in candidates:
        if c and c != head and await is_ancestor(git, c, head):
            return c
    if bottom:
        info = await provider.repo()
        for merged in await provider.recently_merged(info.default_branch):
            sha = merged.head_sha
            if not sha or sha == head:
                continue
            if not await git.try_rev_parse(sha):
                await provider.fetch_head(merged)
            if await is_ancestor(git, sha, head):
                return sha
    res = await git.run("merge-base", head, new_base, read_only=True)
    return res.text.strip()


async def _restack(env: ActionEnv) -> None:
    provider, stack, _ = _stack(env)
    session, git = env.session, env.session.git
    if await detect_operation(git) is not None:
        raise ActionError("Finish or abort the operation in progress first")
    changed = await git.run(
        "status", "--porcelain", "--untracked-files=no", read_only=True, check=False
    )
    if changed.text.strip():
        raise ActionError("Commit, stash or shelve your local changes before restacking")
    remote = await provider.remote_name()
    info = await provider.repo()
    branches = [ly.branch for ly in stack.layers]
    others = [
        wt for wt in await session.worktrees()
        if wt.branch_short in branches and wt.path.resolve() != session.active.path.resolve()
    ]  # fmt: skip
    if others:
        raise ActionError(
            f"{others[0].branch_short} is checked out in {others[0].path}; "
            "switch to that worktree or check out something else there first"
        )

    # one fetch: the base and every layer that's on GitHub
    base = stack.base
    refspecs = [f"+refs/heads/{b}:refs/remotes/{remote}/{b}" for b in {base, *branches}]
    await git.run("fetch", "--quiet", "--no-tags", remote, *refspecs, check=False,
                  env=RM.NO_PROMPT)  # fmt: skip
    if not await git.try_rev_parse(f"refs/remotes/{remote}/{base}"):
        base = info.default_branch  # the base branch is gone (its PR was merged)
        await git.run("fetch", "--quiet", "--no-tags", remote, base, check=False,
                      env=RM.NO_PROMPT)  # fmt: skip
    new_base = f"refs/remotes/{remote}/{base}"
    if not await git.try_rev_parse(new_base):
        new_base = base

    # local branches up to date with GitHub, then each layer's own commits
    recorded = {
        b: sha
        for ls in read_gh_stack(session)
        for b, sha in zip(ls.branches, ls.bases, strict=True)
    }
    leases: dict[str, str] = {}
    layers: list[RestackLayer] = []
    prev_head: str | None = None
    for i, layer in enumerate(stack.layers):
        b = layer.branch
        local = await git.try_rev_parse(f"refs/heads/{b}")
        theirs = await git.try_rev_parse(f"refs/remotes/{remote}/{b}")
        if theirs:
            leases[b] = theirs
        if local is None and theirs is None:
            raise ActionError(f"{b} isn't here or on {remote}")
        if local is None:
            await git.run("branch", "--quiet", b, f"refs/remotes/{remote}/{b}")
            local = theirs
        elif theirs and local != theirs:
            if await is_ancestor(git, local, theirs):
                await git.run("update-ref", f"refs/heads/{b}", theirs, local)
                local = theirs
            elif not await is_ancestor(git, theirs, local):
                raise ActionError(f"{b} and {remote}/{b} have diverged — pull or push it first")
        assert local is not None
        old_base = await _boundary(
            env, provider, local, [recorded.get(b), prev_head], new_base, bottom=i == 0
        )
        layers.append(RestackLayer(b, local, old_base))
        prev_head = local

    before = {ly.branch: ly.head for ly in layers}
    original = session.active.branch_short
    if env.entry:
        env.entry.op = f"Restack {stack.label if stack.number else ', '.join(branches)}"
    result = await restack(session, new_base, layers)
    if result.status != "done":
        env.ui.notify(
            "The restack stopped on a conflict: resolve it, Continue in the banner, "
            "then press S again to push",
            severity="warning",
        )
        return
    if original and original not in branches:
        await git.run("switch", "--quiet", original)
    after = {b: await git.rev_parse(f"refs/heads/{b}") for b in branches}
    moved = [b for b in branches if after[b] != before[b]]
    env.ui.notify(f"Restacked {len(branches)} branches onto {base}" if moved else "Already stacked")
    await _push_stack(env, provider, stack, remote, base, branches, leases, after)


async def _push_stack(
    env: ActionEnv,
    provider: GitHub,
    stack: Stack,
    remote: str,
    base: str,
    branches: list[str],
    leases: dict[str, str],
    heads: dict[str, str],
) -> None:
    pushable = [b for b in branches if b in leases and heads[b] != leases[b]]
    if not pushable:
        return
    mode = env.setting("pr_restack_push", "ask")
    if mode == "never":
        return
    if mode != "always":
        lines = "\n".join(f"  {b}  {leases[b][:8]} → {heads[b][:8]}" for b in pushable)
        if not await env.ui.confirm(
            "Push the restacked branches?",
            f"Force-push (with lease) to {remote}, all or nothing:\n{lines}\n\n"
            "Pushing can't be undone with z.",
        ):
            return
    await RM.push_many(env.session.git, remote, pushable, leases)
    bottom = stack.layers[0]
    # a chain whose old base was merged without being deleted: GitHub didn't move the PR
    if (
        stack.kind == "chain"
        and bottom.pr is not None
        and (await provider.pr(bottom.pr)).base != base
    ):
        await provider.retarget(bottom.pr, base)
    env.ui.notify(f"Pushed {len(pushable)} branch{'es' * (len(pushable) != 1)}")
    _refresh(env)


# -- M on a stacked PR -------------------------------------------------------------------------


async def merge_stacked(env: ActionEnv, provider: GitHub, pr: PullRequest, stack: Stack) -> None:
    """Merge with GitHub's atomic stack merge: this PR and everything below it, or the whole
    stack. On the bottom PR, merging just it is offered too."""
    prs = stack.prs
    pos, top = prs.index(pr.number), len(prs) - 1
    below, whole = prs[: pos + 1], prs
    if pos == top:
        choices = [("whole", f"Merge the whole stack {_span(whole)}")]
    elif pos == 0:
        choices = [
            ("whole", f"Merge the whole stack {_span(whole)}"),
            ("upto", f"Merge #{pr.number} only"),
        ]
    else:
        choices = [
            ("upto", f"Merge {_span(below)} (this PR and everything below)"),
            ("whole", f"Merge the whole stack {_span(whole)}"),
        ]
    if stack.kind == "chain":
        choices = [(f"link-{v}", f"{label} as a GitHub stack") for v, label in choices]
        choices.append(("plain", f"Merge #{pr.number} only, into {pr.base} (not as a stack)"))
    default = env.setting("merge_method", "squash")
    methods = [
        ("squash", "Squash and merge"),
        ("merge", "Create a merge commit"),
        ("rebase", "Rebase and merge"),
    ]
    details = {n: await provider.pr(n) for n in whole}
    lines, warn = [], False
    for n in whole:
        d = details[n]
        issues = [
            *(["draft"] if d.draft else []),
            *(["checks failing"] if d.ci == "failure" else []),
            *(["changes requested"] if d.review_decision == "CHANGES_REQUESTED" else []),
            *(["conflicts"] if d.mergeable == "CONFLICTING" else []),
        ]
        warn = warn or bool(issues) and n in below
        lines.append(f"  #{n} {d.title}" + (f"   ⚠ {', '.join(issues)}" if issues else ""))
    note = (
        "Not a GitHub stack yet: merging as a stack links them first (gh stack link)."
        if stack.kind == "chain"
        else f"{stack.label}: everything up to the chosen PR merges in one step — or none of it."
    )
    answer = await env.ui.form(
        f"Merge {stack.label}" if stack.kind == "github" else f"Merge #{pr.number}",
        [
            Field("what", "Merge", "choice", choices[0][0], options=choices),
            Field("method", "Method", "choice", default, options=methods),
        ],
        body=note + "\n\nBottom to top:\n" + "\n".join(lines),
        ok_label="Merge",
        danger=warn,
    )
    if not answer:
        return
    what, method = str(answer["what"]), str(answer["method"])
    if what == "plain":
        await provider.merge(pr.number, method, delete_branch=True, auto=False)  # type: ignore[arg-type]
        env.ui.notify(f"Merged #{pr.number} into {pr.base}")
        _refresh(env, pr.number)
        return
    if what.startswith("link-"):
        await provider.stack_link([str(n) for n in whole])
        what = what.removeprefix("link-")
    target = prs[-1] if what == "whole" else pr.number
    merged = prs[: prs.index(target) + 1]
    await provider.stack_merge(target, method)  # type: ignore[arg-type]
    if env.entry:
        env.entry.op = f"Merge {_span(merged)}"
    rest = prs[len(merged) :]
    env.ui.notify(
        f"Merged {_span(merged)}" + (f" — {_span(rest)} may need a restack (S)" if rest else "")
    )
    _refresh(env, rest[0] if rest else None)


def actions() -> list[Action]:
    in_stack = lambda c: c.form.get("stack") is not None  # noqa: E731

    def on_layer(c: Any) -> bool:
        pr = c.form.get("pr")
        return (pr is not None and pr.state == "OPEN") or c.form.get("branch_row") is not None

    return [
        Action("pr.stack_link", "Make GitHub Stack…", _link, key="L", group=GROUP,
               screens=SCREEN, when=_linkable),
        Action("pr.create_on_top", "New Branch on Top…", _on_top, key="N", group=GROUP,
               screens=SCREEN, when=on_layer, mutating=True),
        Action("pr.restack", "Restack…", _restack, key="S", group=GROUP, screens=SCREEN,
               when=in_stack, mutating=True),
    ]  # fmt: skip
