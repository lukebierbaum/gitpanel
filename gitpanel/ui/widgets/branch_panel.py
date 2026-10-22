"""Branches panel beside the Log (PyCharm): pick branches to filter the log to their history."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from rich.text import Text
from textual.actions import SkipAction
from textual.binding import Binding
from textual.message import Message
from textual.widgets import Tree
from textual.widgets.tree import TreeNode

from ...git.models import Ref
from ...git.refs import BranchRef

GREEN = "#6CCB3A"
PURPLE = "#A66BE0"

Kind = Literal["all", "head", "ref", "group"]


@dataclass(frozen=True)
class PanelItem:
    kind: Kind
    filter: str = ""  # value for the log's branch filter
    ref: Any = None  # BranchRef or tag Ref
    key: str = ""  # stable identity for restoring the cursor
    strip: str = ""  # folder prefix hidden from the label (e.g. "origin/feature/")


class BranchPanel(Tree[PanelItem]):
    """Highlight a branch to filter the Log; `space` marks several to combine them."""

    DEFAULT_CSS = """
    BranchPanel { width: 32; background: $surface; }
    BranchPanel.-hidden { display: none; }
    """
    BINDINGS = [
        Binding("up,k", "cursor_up", show=False),
        Binding("down,j", "cursor_down", show=False),
        Binding("left,h", "collapse_or_parent", show=False),
        Binding("right,l", "expand_or_child", show=False),
        Binding("pageup", "page_up", show=False),
        Binding("pagedown", "page_down", show=False),
        Binding("home", "scroll_home", show=False),
        Binding("end", "scroll_end", show=False),
        Binding("space", "toggle_mark", "Mark", show=False),
        Binding("escape", "clear_marks", show=False),
    ]
    inherit_bindings = False

    class Changed(Message):
        """The branch filter the panel asks the Log to show (empty = all)."""

        def __init__(self, filters: tuple[str, ...]) -> None:
            super().__init__()
            self.filters = filters

    def __init__(self, *, id: str | None = None) -> None:
        super().__init__("branches", id=id)
        self.show_root = False
        self.guide_depth = 2
        self.marked: dict[str, str] = {}  # key → filter
        self._collapsed: set[str] = {"group:tags"}
        self._expanded: set[str] = set()  # folders the user opened (folders start closed)
        self.folders = True  # group "a/b" branch names into folders
        self._last: tuple[str, ...] | None = None
        self._data: dict[str, Any] = {}

    # -- content --------------------------------------------------------------------
    def populate(
        self,
        branches: list[BranchRef],
        tags: list[Ref],
        favorites: set[str],
        current: str | None,
        active: Path,
    ) -> None:
        self._data = {
            "favorites": favorites,
            "current": current,
            "active": active,
            "has_remotes": any(b.is_remote for b in branches),
        }
        prev = self.cursor_node.data.key if self.cursor_node and self.cursor_node.data else None
        self.clear()
        known = {b.name for b in branches} | {t.name for t in tags} | {"head"}
        self.marked = {k: f for k, f in self.marked.items() if k in known}

        self.root.add_leaf(Text("All branches", style="bold"), PanelItem("all", key="all"))
        head_label = Text("HEAD", style=f"bold {GREEN}")
        head_label.append(
            f"  {current.removeprefix('refs/heads/') if current else 'detached'}", style="dim"
        )
        self.root.add_leaf(head_label, PanelItem("head", "HEAD", key="head"))

        def fav_first(b: Any) -> tuple[bool, str]:
            return (b.name not in favorites, b.short.lower())

        self._fav_first = fav_first
        local = [b for b in branches if b.is_local]
        node = self._group("Local", "group:local", len(local))
        self._add_nested(node, local, "", "local")

        by_remote: dict[str, list[BranchRef]] = defaultdict(list)
        for b in branches:
            if b.is_remote and b.remote:
                by_remote[b.remote].append(b)
        if by_remote:
            remote_node = self._group("Remote", "group:remote", sum(map(len, by_remote.values())))
            for remote in sorted(by_remote):
                sub = remote_node.add(
                    Text(remote, style=PURPLE),
                    PanelItem("group", key=f"group:remote:{remote}"),
                    expand=f"group:remote:{remote}" not in self._collapsed,
                )
                self._add_nested(sub, by_remote[remote], f"{remote}/", f"remote:{remote}")
        if tags:
            tag_node = self._group("Tags", "group:tags", len(tags))
            self._add_nested(tag_node, tags, "", "tags")
        self.root.expand()
        self.call_after_refresh(self._restore, prev)

    def _add_nested(
        self, parent: TreeNode[PanelItem], refs: list[Any], strip: str, scope: str
    ) -> None:
        """Branches named a/b/c become folders a › b › leaf c (folders first, then leaves)."""
        folders: dict[str, list[Any]] = defaultdict(list)
        leaves: list[Any] = []
        for r in refs:
            head, sep, _rest = r.short.removeprefix(strip).partition("/")
            if sep and self.folders:
                folders[head].append(r)
            else:
                leaves.append(r)
        current = self._data.get("current")
        for name in sorted(folders, key=str.lower):
            items = folders[name]
            key = f"dir:{scope}:{strip}{name}"
            holds_focus = any(r.name == current or r.name in self.marked for r in items)
            expand = key in self._expanded or (holds_focus and key not in self._collapsed)
            label = Text.assemble((name, f"bold {PURPLE}"), (f"  {len(items)}", "dim"))
            folder = parent.add(label, PanelItem("group", key=key), expand=expand)
            self._add_nested(folder, items, f"{strip}{name}/", scope)
        tag_order = scope == "tags"
        for r in sorted(
            leaves,
            key=(lambda r: r.short.lower()) if tag_order else self._fav_first,
            reverse=tag_order,
        ):
            folder_prefix = strip
            parent.add_leaf(
                self._label(r, strip=folder_prefix),
                PanelItem("ref", r.short, r, r.name, folder_prefix),
            )

    def _group(self, title: str, key: str, count: int) -> TreeNode[PanelItem]:
        label = Text.assemble((title, f"bold {PURPLE}"), (f"  {count}", "dim"))
        return self.root.add(label, PanelItem("group", key=key), expand=key not in self._collapsed)

    def _label(self, ref: Any, strip: str = "") -> Text:
        t = Text()
        marked = ref.name in self.marked
        is_current = ref.name == self._data.get("current")
        t.append("▣ " if marked else "  ", style=f"bold {GREEN}")
        # a space after each symbol: fonts often draw ★ ● wider than one cell
        t.append("★ " if ref.name in self._data.get("favorites", set()) else "  ", style="#E3B23C")
        t.append("● " if is_current else "  ", style=GREEN)
        t.append(ref.short.removeprefix(strip), style=f"bold {GREEN}" if is_current else "")
        if isinstance(ref, BranchRef):
            wt = ref.worktree
            if wt is not None and wt.resolve() != self._data.get("active"):
                t.append(" ⎇", style="#3FB8AF")
            if ref.ahead or ref.behind:
                t.append(f" ↑{ref.ahead}↓{ref.behind}", style="dim")
            if ref.is_local and self._data.get("has_remotes"):
                if ref.gone:
                    t.append(" upstream gone", style="dim italic")
                elif ref.upstream is None:
                    t.append(" local", style="#E3B23C")  # never pushed / no upstream
        return t

    def _restore(self, prev: str | None) -> None:
        target = None
        if prev is not None:
            target = self._find(self.root, prev)
        if target is not None and target.line >= 0:
            self.move_cursor(target)
        elif self.cursor_line < 0:
            self.cursor_line = 0

    def _find(self, node: TreeNode[PanelItem], key: str) -> TreeNode[PanelItem] | None:
        for child in node.children:
            if child.data is not None and child.data.key == key:
                return child
            found = self._find(child, key)
            if found is not None:
                return found
        return None

    # -- selection ---------------------------------------------------------------------
    def selected_item(self) -> PanelItem | None:
        node = self.cursor_node
        return node.data if node is not None else None

    def selected_ref(self) -> Any | None:
        item = self.selected_item()
        return item.ref if item is not None and item.kind == "ref" else None

    def current_filters(self) -> tuple[str, ...] | None:
        """None = keep the current filter (a group header is highlighted)."""
        if self.marked:
            return tuple(self.marked.values())
        item = self.selected_item()
        if item is None or item.kind == "group":
            return None
        return () if item.kind == "all" else (item.filter,)

    def _announce(self) -> None:
        filters = self.current_filters()
        if filters is not None and filters != self._last:
            self._last = filters
            self.post_message(self.Changed(filters))

    def on_tree_node_highlighted(self, event: Tree.NodeHighlighted) -> None:
        event.stop()
        self._announce()

    def on_tree_node_selected(self, event: Tree.NodeSelected) -> None:
        event.stop()

    def on_tree_node_expanded(self, event: Tree.NodeExpanded) -> None:
        event.stop()
        if event.node.data is not None:
            self._collapsed.discard(event.node.data.key)
            self._expanded.add(event.node.data.key)

    def on_tree_node_collapsed(self, event: Tree.NodeCollapsed) -> None:
        event.stop()
        if event.node.data is not None:
            self._expanded.discard(event.node.data.key)
            self._collapsed.add(event.node.data.key)

    def action_toggle_mark(self) -> None:
        node = self.cursor_node
        item = self.selected_item()
        if node is None or item is None or item.kind == "all":
            return
        if item.kind == "group":
            # a folder/section: mark every branch inside it (or unmark them all)
            leaves = [n.data for n in self._leaves(node)]
            if not leaves:
                return
            if all(d.ref.name in self.marked for d in leaves):
                for d in leaves:
                    self.marked.pop(d.ref.name, None)
            else:
                for d in leaves:
                    self.marked[d.ref.name] = d.filter
            self._refresh_labels()
        else:
            key = item.ref.name if item.kind == "ref" else "head"
            if key in self.marked:
                del self.marked[key]
            else:
                self.marked[key] = item.filter
            if item.kind == "ref":
                node.set_label(self._label(item.ref, strip=item.strip))
        self._announce()

    def _leaves(self, node: TreeNode[PanelItem]) -> list[TreeNode[PanelItem]]:
        out = []
        for child in node.children:
            if child.data is not None and child.data.kind == "ref":
                out.append(child)
            out += self._leaves(child)
        return out

    def action_clear_marks(self) -> None:
        if not self.marked:
            raise SkipAction()
        self.marked.clear()
        self._last = None
        self.post_message(self.Changed(()))
        self.app.call_later(self._refresh_labels)

    def _refresh_labels(self) -> None:
        def walk(node: TreeNode[PanelItem]) -> None:
            for child in node.children:
                if child.data is not None and child.data.kind == "ref":
                    child.set_label(self._label(child.data.ref, strip=child.data.strip))
                walk(child)

        walk(self.root)

    def action_collapse_or_parent(self) -> None:
        node = self.cursor_node
        if node is None:
            return
        if node.allow_expand and node.is_expanded:
            node.collapse()
        elif node.parent is not None and node.parent is not self.root:
            self.move_cursor(node.parent)

    def action_expand_or_child(self) -> None:
        node = self.cursor_node
        if node is None:
            return
        if node.allow_expand and not node.is_expanded:
            node.expand()
        elif node.children:
            self.move_cursor(node.children[0])
