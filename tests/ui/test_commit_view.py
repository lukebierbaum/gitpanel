from __future__ import annotations

from textual.screen import ModalScreen

from gitpanel.ui.app import GitPanelApp
from gitpanel.ui.widgets.diff_view import DiffView
from gitpanel.ui.widgets.file_tree import FileTree, GroupNode

BASE = "".join(f"line {i}\n" for i in range(1, 41))


async def settle(pilot, app) -> None:
    """Wait until background work is done, or a dialog is waiting for input."""
    for _ in range(200):
        await pilot.pause(0.01)
        if isinstance(app.screen, ModalScreen):
            return
        pending = any(getattr(v, "pending", False) for v in app.query("LogView"))
        if not pending and all(w.is_finished for w in app.workers):
            await pilot.pause()
            if all(w.is_finished for w in app.workers):
                return
    raise AssertionError("app did not settle")


def tree_paths(app, group: str) -> list[str]:
    return [r.path for r in app.query_one(FileTree).groups.get(group, [])]


def move_to(app, group: str, path: str | None = None) -> None:
    tree = app.query_one(FileTree)
    for node in tree.root.children:
        if isinstance(node.data, GroupNode) and node.data.group == group:
            if path is None:
                tree.move_cursor(node)
                return
            for leaf in node.children:
                if leaf.data.path == path:
                    tree.move_cursor(leaf)
                    return
    raise AssertionError(f"{group}/{path} not in tree")


async def test_stage_file_and_commit(repo):
    repo.write("README.md", "hello\nworld\n")
    app = GitPanelApp(repo.path)
    async with app.run_test(size=(140, 40)) as pilot:
        await settle(pilot, app)
        assert tree_paths(app, "unstaged") == ["README.md"]
        move_to(app, "unstaged", "README.md")
        app.query_one(FileTree).focus()
        await settle(pilot, app)
        await pilot.press("space")
        await settle(pilot, app)
        assert tree_paths(app, "staged") == ["README.md"]
        assert tree_paths(app, "unstaged") == []

        app.query_one("#commit-msg").focus()
        await pilot.press(*"Add world")
        await pilot.press("ctrl+k")
        await settle(pilot, app)
        assert repo.git("log", "-1", "--format=%s").strip() == "Add world"
        assert app.query_one("#commit-msg").text == ""
        assert tree_paths(app, "staged") == []


async def test_stage_hunk_from_diff_view(repo):
    repo.commit({"a.txt": BASE})
    lines = BASE.splitlines(keepends=True)
    lines[2] = "changed 3\n"
    lines[30] = "changed 31\n"
    repo.write("a.txt", "".join(lines))
    app = GitPanelApp(repo.path)
    async with app.run_test(size=(140, 40)) as pilot:
        await settle(pilot, app)
        move_to(app, "unstaged", "a.txt")
        await settle(pilot, app)
        view = app.query_one("#diff", DiffView)
        assert view.diff is not None and len(view.diff.hunks) == 2
        view.focus()
        await pilot.press("n")  # jump to the second change block
        await settle(pilot, app)
        await pilot.press("space")
        await settle(pilot, app)
        staged = repo.staged_diff()
        assert "+changed 31" in staged and "changed 3\n" not in staged


async def test_stage_single_line_with_range(repo):
    repo.commit({"f.txt": "a\nb\nc\n"})
    repo.write("f.txt", "a\nX\nY\nc\n")
    app = GitPanelApp(repo.path)
    async with app.run_test(size=(140, 40)) as pilot:
        await settle(pilot, app)
        move_to(app, "unstaged", "f.txt")
        await settle(pilot, app)
        view = app.query_one("#diff", DiffView)
        view.focus()
        # rows: hunk, " a", "-b", "+X", "+Y", " c"; cursor starts at first change (-b)
        await pilot.press("down")  # +X
        await pilot.press("v")
        await pilot.press("space")
        await settle(pilot, app)
        assert repo.index_content("f.txt") == "a\nb\nX\nc\n"


async def test_amend_toggle_loads_message(repo):
    repo.commit({"f.txt": "1\n"}, "Original message")
    parent = repo.git("rev-parse", "HEAD~1").strip()
    app = GitPanelApp(repo.path)
    async with app.run_test(size=(140, 40)) as pilot:
        await settle(pilot, app)
        app.query_one(FileTree).focus()
        await pilot.press("alt+m")
        await settle(pilot, app)
        msg = app.query_one("#commit-msg")
        assert msg.text == "Original message"
        msg.text = "Better message"
        await pilot.press("ctrl+k")
        await settle(pilot, app)
        assert repo.git("log", "-1", "--format=%s").strip() == "Better message"
        assert repo.git("rev-parse", "HEAD~1").strip() == parent


async def test_banner_and_abort(repo):
    repo.conflict()
    app = GitPanelApp(repo.path)
    async with app.run_test(size=(140, 40)) as pilot:
        await settle(pilot, app)
        banner = app.query_one("#banner")
        assert banner.has_class("-active")
        assert tree_paths(app, "conflict") == ["conflict.txt"]
        await pilot.click("#op-abort")
        await settle(pilot, app)
        await pilot.press("enter")  # confirm dialog: OK is focused
        await settle(pilot, app)
        assert not banner.has_class("-active")
        assert not (repo.path / ".git" / "MERGE_HEAD").exists()


async def test_rollback_and_undo_from_ui(repo):
    repo.write("README.md", "dirty\n")
    app = GitPanelApp(repo.path)
    async with app.run_test(size=(140, 40)) as pilot:
        await settle(pilot, app)
        move_to(app, "unstaged", "README.md")
        app.query_one(FileTree).focus()
        await settle(pilot, app)
        await pilot.press("ctrl+alt+z")
        await settle(pilot, app)
        await pilot.press("enter")
        await settle(pilot, app)
        assert repo.read("README.md") == "hello\n"
        await pilot.press("z")
        await settle(pilot, app)
        assert repo.read("README.md") == "dirty\n"


async def test_not_a_repo(tmp_path):
    app = GitPanelApp(tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
    assert app.startup_error and "not a git repository" in app.startup_error


async def test_staging_a_hunk_keeps_the_file_in_the_diff_pane(repo):
    base = "".join(f"line {i}\n" for i in range(60))
    repo.commit({"app.py": base})
    repo.write(
        "app.py",
        base.replace("line 3\n", "X3\n")
        .replace("line 30\n", "X30\n")
        .replace("line 55\n", "X55\n"),
    )
    app = GitPanelApp(repo.path)
    async with app.run_test(size=(140, 40)) as pilot:
        await settle(pilot, app)
        move_to(app, "unstaged", "app.py")
        await settle(pilot, app)
        view = app.query_one("#diff", DiffView)
        view.focus()
        await pilot.press("n")  # second hunk
        await settle(pilot, app)
        await pilot.press("space")
        await settle(pilot, app)
        tree = app.query_one(FileTree)
        node = tree.cursor_node.data
        assert node.path == "app.py" and node.group == "unstaged"
        assert view.diff is not None and view.diff.path == "app.py"
        assert len(view.diff.hunks) == 2  # the staged hunk left, the other two remain
        assert app.focused is view
        # unstaging from the Staged group behaves the same way
        move_to(app, "staged", "app.py")
        await settle(pilot, app)
        view.focus()
        await pilot.press("space")
        await settle(pilot, app)
        assert tree.cursor_node.data.group in ("unstaged", "staged")
        assert repo.git("diff", "--cached").strip() == ""


async def test_digit_keys_switch_tool_windows_and_hints_spell_out_alt(repo):
    app = GitPanelApp(repo.path)
    async with app.run_test(size=(160, 40)) as pilot:
        await settle(pilot, app)
        hints = app.query_one("#hints").text
        assert "⌥" not in hints and "alt+enter" in hints
        for key, view in (
            ("2", "log-view"),
            ("3", "worktrees-view"),
            ("5", "shelf-view"),
            ("4", "compare-view"),
            ("1", "commit-view"),
        ):
            await pilot.press(key)
            await settle(pilot, app)
            assert app.view.id == view, key
        # digits typed into the commit message stay text
        app.query_one("#commit-msg").focus()
        await pilot.press("2")
        await settle(pilot, app)
        assert app.view.id == "commit-view" and app.query_one("#commit-msg").text == "2"


async def test_help_key_is_always_pinned_to_the_right_of_the_hint_bar(repo):
    app = GitPanelApp(repo.path)
    async with app.run_test(size=(80, 30)) as pilot:  # far too narrow for all the hints
        await settle(pilot, app)
        bar = app.query_one("#hints")
        line = str(bar.render())
        assert line.rstrip().endswith("?  help") and "…" in line
        assert len(line) <= bar.content_region.width
