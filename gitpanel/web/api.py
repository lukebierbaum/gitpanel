"""Request handlers for one browser tab: what the Textual screens load, as JSON calls.

The browser sends `{"type": "call", "id": n, "method": "log.page", "params": {...}}` and gets
`{"type": "result", "id": n, "result": ...}` (or `"error"`). Selections come back as a plain
`ctx` dict (`screen`, `focus`, commit shas, ref names, file paths, a hunk/line selection,
form values); `Connection.context()` turns it into the same `SelectionContext` the TUI builds,
using what this tab loaded, so actions run unchanged.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from ..actions.context import FileRef, PatchSelection, Scope, SelectionContext
from ..actions.registry import Action, Dispatcher
from ..git import commit as GC
from ..git import patch as P
from ..git import refs as R
from ..git import tags as T
from ..git.changelists import Changelists
from ..git.graph import GraphBuilder
from ..git.log import (
    LogFilter,
    commit_details,
    containing_branches,
    containing_remote_branches,
    has_remotes,
    load_page,
)
from ..git.markers import Marker, collect, default_providers
from ..git.models import Commit, Ref
from ..git.operations import detect
from ..git.status import status
from . import serialize as S
from .bridge import Channel, WebUi

if TYPE_CHECKING:
    from .server import WebApp

PAGE = 300
GROUPS = ("conflict", "staged", "unstaged", "untracked")
# actions about the terminal UI itself; the browser has its own way (close the tab) or no use
TUI_ONLY = frozenset({"app.quit", "log.toggle_branch_folders"})


class RpcError(Exception):
    """A bad request from the browser (shown to the user as an error)."""


def norm_key(key: str) -> str:
    """Textual key syntax with modifiers in a fixed order ("alt+ctrl+k" == "ctrl+alt+k")."""
    *mods, base = key.split("+") if key != "+" else ["+"]
    return "+".join([*sorted(mods), base])


class Connection:
    def __init__(self, app: WebApp, channel: Channel) -> None:
        self.app = app
        self.ch = channel
        self.ui = WebUi(channel)
        self.dispatcher = Dispatcher(app.registry, self.ui, app.config)
        self.providers = default_providers()
        # what this tab has loaded, so ctx dicts can name things by sha/name/path
        self.commits: dict[str, Commit] = {}
        self.markers: dict[str, list[Marker]] = {}
        self.refs: dict[str, Any] = {}  # full name → BranchRef / Ref
        self.status_refs: dict[tuple[str, str], FileRef] = {}
        self.all_changed: tuple[str, ...] = ()
        self.diffs: dict[tuple[str, str], list[P.FileDiff]] = {}  # (source key, path|"") → diffs
        self.builder = GraphBuilder()
        self.log_gen = 0
        self.log_lock = asyncio.Lock()
        self.methods: dict[str, Callable[[dict[str, Any]], Awaitable[Any]]] = {
            "repo.state": self.repo_state,
            "repo.focus": self.repo_focus,
            "log.page": self.log_page,
            "log.slow_markers": self.log_slow_markers,
            "log.details": self.log_details,
            "branches.list": self.branches_list,
            "changes.status": self.changes_status,
            "diff.files": self.diff_files,
            "diff.file": self.diff_file,
            "actions.available": self.actions_available,
            "changes.last_message": self.last_message,
            "actions.run": self.actions_run,
            "actions.key": self.actions_key,
        }

    @property
    def session(self):  # noqa: ANN201 - RepoSession
        return self.app.session

    @property
    def git(self):  # noqa: ANN201 - Git
        return self.app.session.git

    async def call(self, method: str, params: dict[str, Any]) -> Any:
        handler = self.methods.get(method)
        if handler is None:
            raise RpcError(f"unknown method {method!r}")
        return await handler(params)

    # -- repository ------------------------------------------------------------
    async def repo_state(self, _p: dict[str, Any]) -> dict[str, Any]:
        session = self.session
        await session.refresh_active()
        st = await status(self.git)
        op = await detect(self.git)
        wt = session.active
        undo = session.journal.can_undo()
        redo = session.journal.can_redo()
        return {
            "repo": session.common_dir.parent.name,
            "path": str(wt.path),
            "branch": st.branch.branch,
            "head": st.branch.head,
            "upstream": st.branch.upstream,
            "ahead": st.branch.ahead,
            "behind": st.branch.behind,
            "worktree": None if wt.is_main else str(wt.path),
            "changes": len(st.files),
            "operation": S.operation_json(op),
            "undo": undo.op if undo else None,
            "redo": redo.op if redo else None,
        }

    async def repo_focus(self, _p: dict[str, Any]) -> None:
        """The tab got focus: files may have changed outside (like the TUI's AppFocus)."""
        from ..git.session import ChangeKind

        self.session.emit(ChangeKind.INDEX | ChangeKind.WORKTREE)

    # -- log -------------------------------------------------------------------------
    @staticmethod
    def _filter(f: dict[str, Any]) -> LogFilter:
        def val(k: str) -> str:
            return str(f.get(k) or "").strip()

        branch, paths = val("branch"), val("paths")
        return LogFilter(
            refs=tuple(branch.split()) if branch else (),
            author=val("author") or None,
            text=val("text") or None,
            paths=tuple(paths.split()) if paths else (),
            since=val("since") or None,
        )

    async def log_page(self, p: dict[str, Any]) -> dict[str, Any]:
        offset = int(p.get("offset", 0))
        gen = int(p.get("gen", 0))
        filt = self._filter(p.get("filter") or {})
        async with self.log_lock:
            if offset == 0:
                self.log_gen = gen
                self.builder = GraphBuilder()
            elif gen != self.log_gen:
                return {"stale": True}
            try:
                commits = await load_page(self.git, filt, offset, PAGE)
            except Exception as e:  # bad filter values (unknown branch …) shouldn't crash
                raise RpcError(f"Log: {e}") from e
            graph = self.builder.add_all(commits)
        shas = [c.sha for c in commits]
        markers = await collect(self.providers, self.session, shas, slow=False)
        rows = []
        for c, g in zip(commits, graph, strict=True):
            self.commits[c.sha] = c
            self.markers[c.sha] = markers.get(c.sha, [])
            rows.append(
                {
                    **S.commit_json(c),
                    "graph": S.graph_json(g),
                    "markers": [S.marker_json(m) for m in self.markers[c.sha]],
                }
            )
        return {"rows": rows, "has_more": len(commits) == PAGE, "gen": gen}

    async def log_slow_markers(self, p: dict[str, Any]) -> dict[str, Any]:
        shas = [s for s in p.get("shas", []) if s in self.commits]
        extra = await collect(self.providers, self.session, shas, slow=True)
        out = {}
        for sha, more in extra.items():
            merged = sorted([*self.markers.get(sha, []), *more], key=lambda m: (m.order, m.label))
            self.markers[sha] = merged
            out[sha] = [S.marker_json(m) for m in merged]
        return out

    async def log_details(self, p: dict[str, Any]) -> dict[str, Any]:
        sha = str(p["sha"])
        git = self.git
        c = await commit_details(git, sha)
        diffs = await self._diffs({"kind": "commit", "sha": sha})
        remote_has = await containing_remote_branches(git, sha)
        branches = await containing_branches(git, sha)
        return {
            **S.details_json(c),
            "files": [S.diff_summary_json(d) for d in diffs],
            "has_remotes": await has_remotes(git),
            "pushed_to": remote_has,
            "branches": [b for b in branches if b not in remote_has],
        }

    # -- branches ----------------------------------------------------------------------
    async def branches_list(self, _p: dict[str, Any]) -> dict[str, Any]:
        branches = await R.branches(self.git)
        tags = await T.tags(self.git)
        current = self.session.active.branch_short
        self.refs = {b.name: b for b in branches} | {t.name: t for t in tags}
        return {
            "current": current,
            "local": [S.branch_json(b, current) for b in branches if b.is_local],
            "remote": [S.branch_json(b, current) for b in branches if b.is_remote],
            "tags": [S.tag_json(t) for t in tags],
        }

    # -- working tree --------------------------------------------------------------------
    async def changes_status(self, _p: dict[str, Any]) -> dict[str, Any]:
        st = await status(self.git)
        cl = Changelists.load(self.session.private_dir() / "changelists.json")
        cl.prune([f.path for f in st.files if f.has_unstaged and not f.untracked])
        groups: dict[str, list[FileRef]] = {g: [] for g in GROUPS}
        for f in st.files:
            if f.unmerged:
                groups["conflict"].append(FileRef(f.path, P.WorkingTree(), "conflict", f))
            elif f.untracked:
                groups["untracked"].append(FileRef(f.path, P.WorkingTree(), "untracked", f))
            else:
                if f.has_staged:
                    groups["staged"].append(FileRef(f.path, P.Staged(), "staged", f))
                if f.has_unstaged:
                    groups["unstaged"].append(FileRef(f.path, P.WorkingTree(), "unstaged", f))
        self.status_refs = {(r.group, r.path): r for refs in groups.values() for r in refs}
        self.all_changed = tuple(f.path for f in st.files)
        self.diffs = {k: v for k, v in self.diffs.items() if k[0].startswith("commit:")}
        return {
            g: [S.file_ref_json(r) for r in sorted(refs, key=lambda r: r.path)]
            for g, refs in groups.items()
        }

    async def last_message(self, _p: dict[str, Any]) -> str:
        return await GC.last_message(self.git)

    # -- diffs ------------------------------------------------------------------------------
    @staticmethod
    def _source(spec: dict[str, Any]) -> tuple[str, P.DiffSource]:
        kind = spec.get("kind")
        if kind == "commit":
            return f"commit:{spec['sha']}", P.CommitDiff(str(spec["sha"]))
        if kind == "staged":
            return "staged", P.Staged()
        if kind in ("unstaged", "untracked", "conflict"):
            return str(kind), P.WorkingTree()
        raise RpcError(f"unknown diff source {kind!r}")

    async def _diffs(self, spec: dict[str, Any], path: str = "") -> list[P.FileDiff]:
        key, source = self._source(spec)
        cache_key = (key, path if not key.startswith("commit:") else "")
        if cache_key in self.diffs:
            return self.diffs[cache_key]
        ctx_lines = self.app.config.diff_context
        if key.startswith("commit:"):
            diffs = await P.load_diff(self.git, source, context=ctx_lines)
            if len([k for k in self.diffs if k[0].startswith("commit:")]) > 20:
                self.diffs = {k: v for k, v in self.diffs.items() if not k[0].startswith("commit:")}
        elif key == "untracked":
            diffs = await P.load_diff(self.git, source, include_untracked=[path], context=ctx_lines)
        else:
            paths = [path]
            ref = self.status_refs.get((key, path))
            if ref and ref.status and ref.status.orig_path and key == "staged":
                paths.append(ref.status.orig_path)
            diffs = await P.load_diff(self.git, source, paths, context=ctx_lines)
        self.diffs[cache_key] = diffs
        return diffs

    async def diff_files(self, p: dict[str, Any]) -> list[dict[str, Any]]:
        return [S.diff_summary_json(d) for d in await self._diffs(p["source"])]

    async def diff_file(self, p: dict[str, Any]) -> dict[str, Any] | None:
        path = str(p["path"])
        diffs = await self._diffs(p["source"], path)
        d = next((d for d in diffs if d.path == path), None)
        return S.diff_json(d) if d is not None else None

    # -- context and actions -----------------------------------------------------------------
    async def context(self, c: dict[str, Any]) -> SelectionContext:
        screen = str(c.get("screen", ""))
        try:
            focus = Scope(str(c.get("focus", "global")))
        except ValueError as e:
            raise RpcError(f"unknown focus {c.get('focus')!r}") from e
        commits = tuple(self.commits[s] for s in c.get("commits", []) if s in self.commits)
        refs: tuple[Ref, ...] = tuple(self.refs[n] for n in c.get("refs", []) if n in self.refs)
        files: list[FileRef] = []
        for f in c.get("files", []):
            group, path = str(f.get("group")), str(f.get("path"))
            if group == "commit":
                files.append(FileRef(path, P.CommitDiff(str(f["sha"])), "commit"))
            elif (group, path) in self.status_refs:
                files.append(self.status_refs[(group, path)])
        patch = await self._patch(c.get("patch"))
        form = dict(c.get("form") or {})
        if screen == "log":
            form["picked"] = tuple(
                s for s, ms in self.markers.items() if any(m.kind == "picked" for m in ms)
            )
        if screen == "commit":
            form.setdefault("all_changed", self.all_changed)
            form.setdefault("changelist", None)
        return SelectionContext(
            self.session,
            screen=screen,
            focus=focus,
            commits=commits,
            refs=refs,
            files=tuple(files),
            patch=patch,
            operation=await detect(self.git),
            form=form,
        )

    async def _patch(self, spec: dict[str, Any] | None) -> PatchSelection | None:
        """{"source": {...}, "path": p, "hunks": [i], "lines": [[hunk, line]]} → selection."""
        if not spec:
            return None
        path = str(spec["path"])
        _, source = self._source(spec["source"])
        diff = next((d for d in await self._diffs(spec["source"], path) if d.path == path), None)
        if diff is None:
            return None
        sel = P.Selection()
        if not diff.partial_allowed:
            sel.whole_files.add(path)
        else:
            for hi in spec.get("hunks", []):
                if 0 <= int(hi) < len(diff.hunks):
                    sel.add_hunk(diff, int(hi))
            valid = {(hi, li) for hi, h in enumerate(diff.hunks) for li in h.change_indexes()}
            sel.add_lines(path, {(int(h), int(i)) for h, i in spec.get("lines", [])} & valid)
        return PatchSelection(source, (diff,), sel)

    def _action_json(self, a: Action, ctx: SelectionContext) -> dict[str, Any]:
        return {
            "id": a.id,
            "label": a.label_for(ctx),
            "keys": [norm_key(k) for k in self.app.registry.keys_for(a)],
            "scoped": Scope.GLOBAL not in a.scopes or a.screens is not None,
            "group": a.group,
            "danger": a.destructive and not a.silent_backup,
            "description": a.description,
        }

    async def actions_available(self, p: dict[str, Any]) -> list[dict[str, Any]]:
        ctx = await self.context(p.get("ctx") or {})
        palette = bool(p.get("palette"))
        return [
            self._action_json(a, ctx)
            for a in self.app.registry.available(ctx)
            if a.show_in_palette
            and a.id not in TUI_ONLY
            and (palette or not a.id.startswith("app."))
        ]

    async def actions_run(self, p: dict[str, Any]) -> None:
        try:
            action = self.app.registry.get(str(p["id"]))
        except KeyError as e:
            raise RpcError(f"unknown action {p['id']!r}") from e
        ctx = await self.context(p.get("ctx") or {})
        if not action.applies(ctx):
            self.ui.notify(f"{action.label}: not available here", severity="warning")
            return
        await self.dispatcher.dispatch(action, ctx)

    async def actions_key(self, p: dict[str, Any]) -> bool:
        key = norm_key(str(p["key"]))
        ctx = await self.context(p.get("ctx") or {})
        reg = self.app.registry
        originals = {k for a in reg.all() for k in reg.keys_for(a) if norm_key(k) == key}
        for original in originals:
            action = reg.for_key(ctx, original)
            if action is not None:
                await self.dispatcher.dispatch(action, ctx)
                return True
        return False
