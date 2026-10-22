"""Cherry-picking between branches. See docs/milestones/M4.md."""

from __future__ import annotations

import contextlib
import re
import shutil
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from . import patch as P
from . import worktrees as W
from .log import LOG_FORMAT, parse_record
from .models import Commit
from .operations import OperationState, detect
from .runner import Git, GitError
from .session import RepoSession
from .status import status

TRAILER = re.compile(r"\(cherry picked from commit ([0-9a-f]{7,64})\)")


class PickError(Exception):
    pass


@dataclass(frozen=True)
class PickResult:
    status: Literal["done", "conflict"]
    state: OperationState | None = None
    worktree: Path | None = None  # where the pick happened, when not the active worktree
    output: str = ""


async def _ordered(git: Git, shas: Sequence[str]) -> list[str]:
    """Oldest first: an ancestor always has fewer ancestors than its descendants."""
    if len(shas) < 2:
        return list(shas)
    keyed = []
    for i, sha in enumerate(shas):
        res = await git.run("rev-list", "--count", sha, read_only=True)
        keyed.append((int(res.text.strip() or 0), -i, sha))
    return [sha for _, _, sha in sorted(keyed)]


async def cherry_pick(
    session: RepoSession,
    shas: Sequence[str],
    *,
    git: Git | None = None,
    git_dir: Path | None = None,
    record_origin: bool = False,
    no_commit: bool = False,
    mainline: int | None = None,
) -> PickResult:
    git = git or session.git
    if await detect(git) is not None:
        raise PickError("Another operation is in progress")
    ordered = await _ordered(git, shas)
    gd = git_dir or Path(await git.rev_parse("--path-format=absolute", "--git-dir"))
    (gd / "gitpanel").mkdir(parents=True, exist_ok=True)
    (gd / "gitpanel" / "sequence-total").write_text(str(len(ordered)))
    args = ["cherry-pick", "--empty=drop"]
    if record_origin:
        args.append("-x")
    if no_commit:
        args.append("--no-commit")
    if mainline:
        args += ["-m", str(mainline)]
    res = await git.run(*args, *ordered, check=False)
    state = await detect(git)
    if state is not None:
        return PickResult("conflict", state, output=res.text + res.stderr)
    if not res.ok:
        raise GitError(res)
    return PickResult("done", output=res.text + res.stderr)


# --- already-picked detection ----------------------------------------------------------


async def _patch_ids(git: Git, rev_args: Sequence[str]) -> dict[str, str]:
    """commit sha → stable patch id for the given `git log` arguments (merges skipped)."""
    log = await git.run("log", "--no-merges", "-p", "--format=commit %H", *rev_args, read_only=True)
    if not log.stdout.strip():
        return {}
    res = await git.run("patch-id", "--stable", input=log.stdout, read_only=True)
    out: dict[str, str] = {}
    for line in res.text.splitlines():
        parts = line.split()
        if len(parts) == 2:
            out[parts[1]] = parts[0]
    return out


async def picked_shas(
    git: Git, candidates: Sequence[str], head: str = "HEAD", depth: int = 2000
) -> set[str]:
    """Commits among `candidates` (not on `head`) whose change `head` already contains."""
    if not candidates or not await git.try_rev_parse(head):
        return set()
    on_head = set(
        (await git.run("rev-list", f"--max-count={depth * 2}", head, read_only=True)).text.split()
    )
    foreign = [c for c in candidates if c not in on_head]
    if not foreign:
        return set()
    head_ids = await _patch_ids(git, [f"--max-count={depth}", head])
    head_pids = set(head_ids.values())
    bodies = await git.run("log", f"--max-count={depth}", "--format=%B%x00", head, read_only=True)
    trailers = set(TRAILER.findall(bodies.text))
    cand_ids = await _patch_ids(git, ["--no-walk", *foreign])
    picked = {sha for sha, pid in cand_ids.items() if pid in head_pids}
    picked |= {c for c in foreign if any(c.startswith(t) or t.startswith(c) for t in trailers)}
    return picked


@dataclass
class CompareResult:
    a: str
    b: str
    only_a: list[tuple[Commit, bool]] = field(default_factory=list)  # (commit, picked)
    only_b: list[tuple[Commit, bool]] = field(default_factory=list)


async def compare(git: Git, a: str, b: str, limit: int = 1000) -> CompareResult:
    out = CompareResult(a, b)
    for side, flag in (("only_a", "--left-only"), ("only_b", "--right-only")):
        res = await git.run(
            "log",
            "-z",
            "--cherry-mark",
            flag,
            "--topo-order",
            f"--max-count={limit}",
            f"--format=%m{LOG_FORMAT}",
            f"{a}...{b}",
            read_only=True,
        )
        rows = []
        for rec in res.text.split("\0"):
            if not rec.strip():
                continue
            mark, body = rec.lstrip("\n")[0], rec.lstrip("\n")[1:]
            rows.append((parse_record(body), mark == "="))
        setattr(out, side, rows)
    # -x trailers also count as picked
    for side, other in (("only_b", a), ("only_a", b)):
        rows = getattr(out, side)
        extra = await picked_shas(git, [c.sha for c, p in rows if not p], other)
        setattr(out, side, [(c, p or c.sha in extra) for c, p in rows])
    return out


# --- pick onto another branch ------------------------------------------------------------


async def pick_onto_branch(
    session: RepoSession,
    branch: str,
    shas: Sequence[str],
    *,
    record_origin: bool = False,
    mainline: int | None = None,
) -> PickResult:
    """Cherry-pick onto `branch` without checking it out in the active worktree."""
    if branch == session.active.branch_short:
        return await cherry_pick(session, shas, record_origin=record_origin, mainline=mainline)
    holder = W.for_branch(await session.worktrees(), branch)
    if holder is not None:
        git = Git(holder.path)
        st = await status(git)
        if st.tracked or st.unmerged:
            raise PickError(f"{branch} is checked out in {holder.path}, which has local changes")
        gd = Path(await git.rev_parse("--path-format=absolute", "--git-dir"))
        res = await cherry_pick(
            session, shas, git=git, git_dir=gd, record_origin=record_origin, mainline=mainline
        )
        return PickResult(res.status, res.state, holder.path, res.output)

    base = session.private_dir(shared=True) / "pick-wt"
    base.mkdir(parents=True, exist_ok=True)
    path = base / re.sub(r"[^A-Za-z0-9._-]+", "-", branch)
    if path.exists():
        raise PickError(f"A previous pick onto {branch} is still pending in {path}")
    await W.add(session.git, path, branch=branch)
    git = Git(path)
    try:
        gd = Path(await git.rev_parse("--path-format=absolute", "--git-dir"))
        res = await cherry_pick(
            session, shas, git=git, git_dir=gd, record_origin=record_origin, mainline=mainline
        )
    except BaseException:
        await W.remove(session.git, path, force=True)
        raise
    if res.status == "done":
        await W.remove(session.git, path, force=True)
        with contextlib.suppress(OSError):
            shutil.rmtree(path)
        return PickResult("done", None, None, res.output)
    return PickResult("conflict", res.state, path, res.output)


# --- patch-level pick ------------------------------------------------------------------------


async def apply_from_commit(git: Git, sha: str, selection: P.Selection) -> None:
    diffs = await P.load_diff(git, P.CommitDiff(sha), sorted(selection.paths()))
    patch = P.build_patch(diffs, selection)
    await P.apply(git, patch, to="worktree", three_way=False)


async def get_file_from_commit(git: Git, sha: str, paths: Sequence[str]) -> None:
    await git.run("restore", f"--source={sha}", "--worktree", "--", *paths)
