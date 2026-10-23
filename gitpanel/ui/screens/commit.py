"""Commit tool window: changes tree, diff with hunk/line selection, commit message."""

from __future__ import annotations

from typing import Any

from textual import events, on, work
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Checkbox, Input, Label, TextArea

from ...actions.context import FileRef, PatchSelection, Scope, SelectionContext
from ...git import commit as C
from ...git import patch as P
from ...git.changelists import Changelists
from ...git.status import status
from ..base import ToolWindow
from ..widgets.diff_view import DiffView
from ..widgets.file_tree import FileTree
from ..widgets.splitter import Splitter
from ..widgets.title_bar import TitleBar


class CommitView(ToolWindow):
    name_id = "commit"

    DEFAULT_CSS = """
    CommitView { height: 1fr; }
    CommitView #commit-body { height: 1fr; }
    CommitView #commit-left { width: 42%; min-width: 24; }
    CommitView FileTree { height: 1fr; background: $surface; }
    CommitView #commit-panel { height: 15; padding: 0 1; }
    CommitView #commit-msg { height: 1fr; min-height: 3; }
    CommitView #commit-options { height: auto; margin-top: 1; }
    CommitView #commit-options Checkbox { margin-right: 1; }
    CommitView #commit-options .spacer { width: 1fr; }
    CommitView #commit-author { width: 1fr; margin-top: 1; }
    CommitView #commit-button { margin-left: 1; }
    CommitView DiffView { height: 1fr; }
    """

    def __init__(self) -> None:
        super().__init__(id="commit-view")
        self._diff_cache: dict[tuple[str, str], list[P.FileDiff]] = {}
        self._history_pos = -1
        self._generation = 0
        self._all_changed: tuple[str, ...] = ()

    def compose(self) -> ComposeResult:
        with Horizontal(id="commit-body"):
            with Vertical(id="commit-left"):
                yield FileTree(id="changes")
                # drag to make the message box taller or shorter
                yield Splitter("commit.message", "y", minimum=8, resizes="next")
                with Vertical(id="commit-panel"):
                    yield TextArea(
                        id="commit-msg",
                        soft_wrap=True,
                        placeholder="Commit message  (alt+↑/↓ history)",
                        show_line_numbers=False,
                    )
                    yield Input(
                        placeholder="Author (optional, Name <email>)",
                        id="commit-author",
                        compact=True,
                    )
                    with Horizontal(id="commit-options"):
                        yield Checkbox("Amend", id="commit-amend", compact=True)
                        yield Checkbox("Sign-off", id="commit-signoff", compact=True)
                        yield Label("", classes="spacer")
                        yield Button("Commit", id="commit-button", variant="primary", compact=True)
            yield Splitter("commit.changes", "x", minimum=24)
            with Vertical(id="commit-right"):
                yield TitleBar(id="diff-title")
                yield DiffView(id="diff")

    stale = False

    def on_mount(self) -> None:
        self.query_one(FileTree).focus()

    def focus_default(self) -> None:
        self.query_one(FileTree).focus()

    # -- data ---------------------------------------------------------------
    @work(exclusive=True, group="commit-reload")
    async def reload(self) -> None:
        session = self.gp.session
        if session is None:
            return
        self._generation += 1
        self._diff_cache.clear()
        st = await status(session.git)
        cl = Changelists.load(session.private_dir() / "changelists.json")
        changed = [f.path for f in st.files if f.has_unstaged and not f.untracked]
        cl.prune(changed)
        self._all_changed = tuple(f.path for f in st.files)
        # populate() restores the cursor and then posts FocusedChanged, which shows the diff
        self.query_one(FileTree).populate(st, cl)

    async def _load(self, ref: FileRef) -> list[P.FileDiff]:
        key = (ref.group, ref.path)
        if key in self._diff_cache:
            return self._diff_cache[key]
        git = self.gp.session.git
        paths = [ref.path]
        if ref.status and ref.status.orig_path and ref.group == "staged":
            paths.append(ref.status.orig_path)
        ctx_lines = self.gp.config.diff_context
        if ref.group == "untracked":
            diffs = await P.load_diff(
                git, P.WorkingTree(), include_untracked=[ref.path], context=ctx_lines
            )
        elif ref.group == "staged":
            diffs = await P.load_diff(git, P.Staged(), paths, context=ctx_lines)
        else:
            diffs = await P.load_diff(git, P.WorkingTree(), paths, context=ctx_lines)
        self._diff_cache[key] = diffs
        return diffs

    async def _show_focused_diff(self, *, keep_position: bool = False) -> None:
        tree = self.query_one(FileTree)
        view = self.query_one(DiffView)
        title = self.query_one("#diff-title", TitleBar)
        ref = tree.focused_file()
        if ref is None:
            refs = tree.focused_refs()
            if refs:
                title.set(f"{len(refs)} file{'s' if len(refs) != 1 else ''}")
                view.set_diff(None, None, message="Select a file to see its changes")
            else:
                title.update("")
                msg = "No local changes" if not tree.groups or not any(tree.groups.values()) else ""
                view.set_diff(None, None, message=msg)
            return
        group_title = {
            "staged": "Staged",
            "unstaged": "Unstaged",
            "untracked": "Unversioned",
            "conflict": "Conflict",
        }[ref.group]
        title.set(ref.path, group_title)
        if ref.group == "conflict":
            code = ref.status.conflict if ref.status and ref.status.conflict else "UU"
            from ...git.conflicts import Conflict

            view.set_diff(
                None,
                None,
                message=f"Merge conflict ({Conflict(ref.path, code).describe}) — enter: resolve "
                "in the 3-pane merger · alt+←: accept yours · alt+→: accept theirs",
            )
            return
        generation = self._generation
        diffs = await self._load(ref)
        if generation != self._generation:
            return
        diff = next((d for d in diffs if d.path == ref.path), diffs[0] if diffs else None)
        view.set_diff(ref.source, diff, message="No changes", keep_position=keep_position)

    @on(FileTree.FocusedChanged)
    async def _focused_changed(self) -> None:
        # same file as before (e.g. after staging a hunk) → stay where you were in its diff
        await self._show_focused_diff(keep_position=True)
        self.gp.refresh_hints()

    @on(DiffView.SelectionChanged)
    def _selection_changed(self) -> None:
        self.gp.refresh_hints()

    # -- context --------------------------------------------------------------
    def form(self) -> dict[str, Any]:
        tree = self.query_one(FileTree)
        return {
            "all_changed": self._all_changed,
            "changelist": tree.focused_changelist() if self.screen.focused is tree else None,
            "message": self.query_one("#commit-msg", TextArea).text,
            "amend": self.query_one("#commit-amend", Checkbox).value,
            "signoff": self.query_one("#commit-signoff", Checkbox).value,
            "author": self.query_one("#commit-author", Input).value,
        }

    def context(self, base: SelectionContext) -> SelectionContext:
        focused = self.screen.focused
        tree = self.query_one(FileTree)
        view = self.query_one(DiffView)
        kwargs: dict[str, Any] = {"screen": "commit", "form": self.form()}
        if focused is view and view.diff is not None and view.source is not None:
            sel = view.selection()
            if sel is not None:
                kwargs.update(
                    focus=Scope.HUNK,
                    patch=PatchSelection(view.source, (view.diff,), sel),
                    files=tuple(r for r in [tree.focused_file()] if r is not None),
                )
        elif focused is tree:
            kwargs.update(focus=Scope.FILE, files=tuple(tree.focused_refs()))
        return base.with_(**kwargs)

    def hint_extras(self) -> list[tuple[str, str]]:
        focused = self.screen.focused
        if isinstance(focused, DiffView):
            return [("v", "select lines"), ("n/p", "next/prev change")]
        if isinstance(focused, FileTree):
            return [("tab", "diff")]
        return []

    # -- UI events --------------------------------------------------------------
    def handle_event(self, event: str, data: dict[str, Any]) -> None:
        if event == "commit.done":
            self.query_one("#commit-msg", TextArea).text = ""
            self.query_one("#commit-amend", Checkbox).value = False
            self._history_pos = -1
        elif event == "commit.toggle_amend":
            box = self.query_one("#commit-amend", Checkbox)
            box.value = not box.value

    @on(Checkbox.Changed, "#commit-amend")
    async def _amend_changed(self, event: Checkbox.Changed) -> None:
        msg = self.query_one("#commit-msg", TextArea)
        if event.value and not msg.text.strip():
            msg.text = await C.last_message(self.gp.session.git)
        self.gp.refresh_hints()

    @on(Button.Pressed, "#commit-button")
    def _commit_pressed(self) -> None:
        self.gp.dispatch_id("commit.commit")

    def on_key(self, event: events.Key) -> None:
        msg = self.query_one("#commit-msg", TextArea)
        focused = self.screen.focused
        # focus order: changes tree → diff → message (DOM order would skip the diff)
        cycle = {
            ("tab", FileTree): "#diff",
            ("tab", DiffView): "#commit-msg",
            ("shift+tab", DiffView): "#changes",
            ("shift+tab", TextArea): "#diff",
        }
        for (key, kind), target in cycle.items():
            if event.key == key and isinstance(focused, kind):
                event.stop()
                event.prevent_default()
                self.query_one(target).focus()
                return
        if focused is not msg or event.key not in ("alt+up", "alt+down"):
            return
        event.stop()
        event.prevent_default()
        history = C.MessageHistory(self.gp.session.private_dir() / "messages.json").load()
        if not history:
            return
        step = 1 if event.key == "alt+up" else -1
        self._history_pos = max(-1, min(self._history_pos + step, len(history) - 1))
        msg.text = "" if self._history_pos < 0 else history[self._history_pos]
