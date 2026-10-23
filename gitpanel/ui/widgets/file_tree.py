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
from ...git.changelists import Changelists
from ...git.models import ChangeType, FileStatus, RepoStatus
from ..colors import C

GROUPS: list[tuple[FileGroup, str]] = [
    ("conflict", "Merge Conflicts"),
    ("staged", "Staged"),
    ("unstaged", "Changes"),
    ("untracked", "Unversioned Files"),
]

STATUS_STYLE = {  # colour names (see ui/colors.py); others are plain Rich styles
    ChangeType.ADDED: "added",
    ChangeType.MODIFIED: "modified",
    ChangeType.DELETED: "removed",
    ChangeType.RENAMED: "renamed",
    ChangeType.COPIED: "renamed",
    ChangeType.TYPE_CHANGED: "purple",
    ChangeType.UNMERGED: "fail",
    ChangeType.UNTRACKED: "amber",
}


@dataclass(frozen=True)
class GroupNode:
    group: FileGroup
    changelist: str | None = None  # for "unstaged" groups split by changelist


def _key(g: GroupNode) -> str:
    return g.group if g.changelist is None else f"cl:{g.changelist}"


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
    style = C[STATUS_STYLE.get(change, "")]
    t.append(f"{change.value} ", style=style)
    t.append(name, style=style)
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
        self.changelists: Changelists | None = None
        self._collapsed: set[str] = set()

    def populate(self, st: RepoStatus, changelists: Changelists | None = None) -> None:
        prev = self.cursor_node.data if self.cursor_node else None
        prev_line = self.cursor_line
        self.groups = _group_files(st)
        self.changelists = changelists
        self.clear()
        target: TreeNode[FileRef | GroupNode] | None = None
        sections: list[tuple[GroupNode, str, list[FileRef]]] = []
        for group, title in GROUPS:
            files = self.groups[group]
            if group == "unstaged" and changelists is not None:
                by_list = changelists.group(r.path for r in files)
                for name in changelists.lists:
                    members = [r for r in files if r.path in set(by_list.get(name, []))]
                    if not members and name != changelists.active:
                        continue
                    marker = (
                        "  (active)"
                        if name == changelists.active and len(changelists.lists) > 1
                        else ""
                    )
                    sections.append((GroupNode("unstaged", name), name + marker, members))
                continue
            if files:
                sections.append((GroupNode(group), title, files))
        for gnode, title, files in sections:
            if not files and gnode.changelist is None:
                continue
            label = Text.assemble((title, "bold"), (f"  {len(files)}", "dim"))
            key = gnode.group if gnode.changelist is None else f"cl:{gnode.changelist}"
            node = self.root.add(label, gnode, expand=key not in self._collapsed)
            if prev == gnode:
                target = node
            for ref in files:
                leaf = node.add_leaf(file_label(ref), ref)
                if isinstance(prev, FileRef) and prev.group == ref.group and prev.path == ref.path:
                    target = leaf
        self.root.expand()
        # Node line numbers only exist once the rebuilt tree has been laid out,
        # so restoring the cursor has to wait until after the next refresh.
        self.call_after_refresh(self._restore_cursor, target, prev_line)

    def _restore_cursor(self, target: TreeNode[FileRef | GroupNode] | None, prev_line: int) -> None:
        if target is not None and target.line >= 0:
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
            return [c.data for c in node.children if isinstance(c.data, FileRef)]
        return [node.data]

    def focused_changelist(self) -> str | None:
        node = self.cursor_node
        if node is None:
            return None
        data = node.data
        if isinstance(data, GroupNode):
            return data.changelist
        if isinstance(data, FileRef) and data.group == "unstaged" and self.changelists:
            return self.changelists.list_for(data.path)
        return None

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
                self._collapsed.add(_key(node.data))
        elif node.parent is not None and node.parent is not self.root:
            self.move_cursor(node.parent)

    def action_expand_or_child(self) -> None:
        node = self.cursor_node
        if node is None:
            return
        if node.allow_expand and not node.is_expanded:
            node.expand()
            if isinstance(node.data, GroupNode):
                self._collapsed.discard(_key(node.data))
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
            self._collapsed.add(_key(event.node.data))

    def on_tree_node_expanded(self, event: Tree.NodeExpanded) -> None:
        event.stop()
        if isinstance(event.node.data, GroupNode):
            self._collapsed.discard(_key(event.node.data))
