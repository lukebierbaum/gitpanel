"""Changes tree for the Commit tool window: Conflicts / Staged / Changes / Unversioned."""

from __future__ import annotations

import posixpath
from dataclasses import dataclass

from rich.text import Text
from textual.binding import Binding
from textual.message import Message
from textual.widgets import Tree
from textual.widgets.tree import TreeNode

from ...actions.context import FileGroup, FileRef
from ...git import patch as P
from ...git.models import ChangeType, FileStatus, RepoStatus

GROUPS: list[tuple[FileGroup, str]] = [
    ("conflict", "Merge Conflicts"),
    ("staged", "Staged"),
    ("unstaged", "Changes"),
    ("untracked", "Unversioned Files"),
]

STATUS_STYLE = {
    ChangeType.ADDED: "#6CCB3A",
    ChangeType.MODIFIED: "#B58CE8",
    ChangeType.DELETED: "red",
    ChangeType.RENAMED: "cyan",
    ChangeType.COPIED: "cyan",
    ChangeType.TYPE_CHANGED: "magenta",
    ChangeType.UNMERGED: "bold red",
    ChangeType.UNTRACKED: "yellow",
}


@dataclass(frozen=True)
class GroupNode:
    group: FileGroup


def _source(group: FileGroup) -> P.DiffSource:
    return P.Staged() if group == "staged" else P.WorkingTree()


def _group_files(st: RepoStatus) -> dict[FileGroup, list[FileRef]]:
    groups: dict[FileGroup, list[FileRef]] = {g: [] for g, _ in GROUPS}
    for f in st.files:
        if f.unmerged:
            groups["conflict"].append(FileRef(f.path, P.WorkingTree(), "conflict", f))
            continue
        if f.untracked:
            groups["untracked"].append(FileRef(f.path, P.WorkingTree(), "untracked", f))
            continue
        if f.has_staged:
            groups["staged"].append(FileRef(f.path, P.Staged(), "staged", f))
        if f.has_unstaged:
            groups["unstaged"].append(FileRef(f.path, P.WorkingTree(), "unstaged", f))
    for files in groups.values():
        files.sort(key=lambda r: r.path)
    return groups


def _change_for(ref: FileRef) -> ChangeType:
    st: FileStatus | None = ref.status
    if st is None:
        return ChangeType.MODIFIED
    if ref.group == "conflict":
        return ChangeType.UNMERGED
    if ref.group == "untracked":
        return ChangeType.UNTRACKED
    return st.staged if ref.group == "staged" else st.unstaged


def file_label(ref: FileRef) -> Text:
    change = _change_for(ref)
    name = posixpath.basename(ref.path)
    folder = posixpath.dirname(ref.path)
    t = Text()
    t.append(f"{change.value} ", style=STATUS_STYLE.get(change, ""))
    t.append(name, style=STATUS_STYLE.get(change, ""))
    if ref.status and ref.status.orig_path and ref.group == "staged":
        t.append(f"  ← {ref.status.orig_path}", style="dim")
    if folder:
        t.append(f"  {folder}", style="dim")
    if ref.status and ref.status.has_staged and ref.status.has_unstaged:
        t.append("  ◧", style="dim")
    return t


class FileTree(Tree[FileRef | GroupNode]):
    """Space/stage keys are provided by the action registry, not by the tree."""

    BINDINGS = [
        Binding("up,k", "cursor_up", "Up", show=False),
        Binding("down,j", "cursor_down", "Down", show=False),
        Binding("left,h", "collapse_or_parent", "Collapse", show=False),
        Binding("right,l", "expand_or_child", "Expand", show=False),
        Binding("enter", "select_cursor", "Show diff", show=False),
        Binding("pageup", "page_up", show=False),
        Binding("pagedown", "page_down", show=False),
        Binding("home", "scroll_home", show=False),
        Binding("end", "scroll_end", show=False),
    ]
    inherit_bindings = False

    class FocusedChanged(Message):
        def __init__(self, tree: FileTree) -> None:
            super().__init__()
            self.tree = tree

    def __init__(self, *, id: str | None = None) -> None:
        super().__init__("changes", id=id)
        self.show_root = False
        self.guide_depth = 2
        self.groups: dict[FileGroup, list[FileRef]] = {}
        self._collapsed: set[FileGroup] = set()

    def populate(self, st: RepoStatus) -> None:
        prev = self.cursor_node.data if self.cursor_node else None
        prev_line = self.cursor_line
        self.groups = _group_files(st)
        self.clear()
        target: TreeNode[FileRef | GroupNode] | None = None
        for group, title in GROUPS:
            files = self.groups[group]
            if not files:
                continue
            label = Text.assemble((title, "bold"), (f"  {len(files)}", "dim"))
            node = self.root.add(label, GroupNode(group), expand=group not in self._collapsed)
            if prev == GroupNode(group):
                target = node
            for ref in files:
                leaf = node.add_leaf(file_label(ref), ref)
                if isinstance(prev, FileRef) and prev.group == ref.group and prev.path == ref.path:
                    target = leaf
        self.root.expand()
        if target is not None:
            self.move_cursor(target)
        elif prev_line >= 0:
            self.cursor_line = min(prev_line, max(self.last_line, 0))
        else:
            self.cursor_line = 0
        self.post_message(self.FocusedChanged(self))

    def focused_refs(self) -> list[FileRef]:
        node = self.cursor_node
        if node is None or node.data is None:
            return []
        if isinstance(node.data, GroupNode):
            return list(self.groups.get(node.data.group, []))
        return [node.data]

    def focused_file(self) -> FileRef | None:
        node = self.cursor_node
        return node.data if node is not None and isinstance(node.data, FileRef) else None

    def action_collapse_or_parent(self) -> None:
        node = self.cursor_node
        if node is None:
            return
        if node.allow_expand and node.is_expanded:
            node.collapse()
            if isinstance(node.data, GroupNode):
                self._collapsed.add(node.data.group)
        elif node.parent is not None and node.parent is not self.root:
            self.move_cursor(node.parent)

    def action_expand_or_child(self) -> None:
        node = self.cursor_node
        if node is None:
            return
        if node.allow_expand and not node.is_expanded:
            node.expand()
            if isinstance(node.data, GroupNode):
                self._collapsed.discard(node.data.group)
        elif node.children:
            self.move_cursor(node.children[0])

    def on_tree_node_highlighted(self, event: Tree.NodeHighlighted) -> None:
        event.stop()
        self.post_message(self.FocusedChanged(self))

    def on_tree_node_selected(self, event: Tree.NodeSelected) -> None:
        event.stop()
        self.post_message(self.FocusedChanged(self))

    def on_tree_node_collapsed(self, event: Tree.NodeCollapsed) -> None:
        event.stop()
        if isinstance(event.node.data, GroupNode):
            self._collapsed.add(event.node.data.group)

    def on_tree_node_expanded(self, event: Tree.NodeExpanded) -> None:
        event.stop()
        if isinstance(event.node.data, GroupNode):
            self._collapsed.discard(event.node.data.group)
