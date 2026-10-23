from __future__ import annotations

from textual.app import App, ComposeResult

from gitpanel.git.patch import parse_diff
from gitpanel.ui.widgets.diff_view import DiffView

LONG = "x = compute(" + ", ".join(f"arg{i}" for i in range(30)) + ")  # END"
DIFF = f"""diff --git a/a.py b/a.py
--- a/a.py
+++ b/a.py
@@ -1,2 +1,2 @@
 keep
-old
+{LONG}
"""


class _Host(App):
    def compose(self) -> ComposeResult:
        yield DiffView(id="d")


def _text(view: DiffView, y: int) -> str:
    return "".join(seg.text for seg in view.render_line(y))


async def test_side_by_side_scrolls_sideways_to_show_long_lines():
    app = _Host()
    async with app.run_test(size=(60, 10)) as pilot:
        view = app.query_one(DiffView)
        view.split = True
        view.set_diff(None, parse_diff(DIFF)[0])
        await pilot.pause()
        row = next(
            i for i, r in enumerate(view.rows) if r.pair is not None and r.pair.marker == "+"
        )
        y = row - view.scroll_offset.y
        assert "END" not in _text(view, y)
        assert view.virtual_size.width > view.scrollable_content_region.width
        view.scroll_to(x=view.max_scroll_x, animate=False)
        await pilot.pause()
        line = _text(view, y)
        assert "END" in line
        # line numbers stay in place while the text scrolls
        assert line.split("│")[1].lstrip().startswith("2 +")


async def test_ranges_extend_clear_and_lists_drop_them_on_plain_moves():
    from datetime import UTC, datetime

    from gitpanel.git.graph import GraphRow
    from gitpanel.git.models import Commit
    from gitpanel.ui.widgets.commit_table import CommitTable, LogRow

    class Host(App):
        def compose(self) -> ComposeResult:
            yield DiffView(id="d")
            yield CommitTable(id="t")

    app = Host()
    async with app.run_test(size=(80, 30)) as pilot:
        diff = app.query_one(DiffView)
        diff.set_diff(None, parse_diff(DIFF)[0])
        diff.focus()
        diff.cursor = 0
        await pilot.press("shift+down", "shift+down")
        assert diff.selection_range() == (0, 2)
        await pilot.press("down")  # text views keep extending from the anchor
        assert diff.selection_range() == (0, 3)
        await pilot.press("escape")
        assert diff.anchor is None

        when = datetime(2026, 1, 1, tzinfo=UTC)
        commits = [
            Commit(f"{i:040x}", subject=f"c{i}", author_name="a", author_date=when)
            for i in range(5)
        ]
        table = app.query_one(CommitTable)
        table.set_rows(
            [LogRow(c, GraphRow(c.sha, 0, (("●", 0),))) for c in commits], has_more=False
        )
        table.focus()
        await pilot.press("shift+down", "shift+down")
        assert [r.commit.subject for r in table.selected_rows()] == ["c0", "c1", "c2"]
        await pilot.press("down")  # lists drop the range on a plain move
        assert table.anchor is None and table.cursor == 3


async def test_hidden_unchanged_lines_expand_and_whole_file(repo):
    from gitpanel.ui.app import GitPanelApp

    from .test_commit_view import move_to, settle

    lines = [f"line {i}" for i in range(1, 41)]
    repo.commit({"f.txt": "\n".join(lines) + "\n"}, "base")
    changed = list(lines)
    changed[4] = "FIVE"
    changed[30] = "THIRTY-ONE"
    (repo.path / "f.txt").write_text("\n".join(changed) + "\n")
    app = GitPanelApp(repo.path)
    async with app.run_test(size=(120, 40)) as pilot:
        await settle(pilot, app)
        move_to(app, "unstaged", "f.txt")
        await settle(pilot, app)
        diff = app.query_one("#diff", DiffView)
        diff.split = False
        diff._rebuild()
        await settle(pilot, app)
        gaps = [r.text for r in diff.rows if r.kind == "gap"]
        assert gaps == ["⋯ 1 unchanged line", "⋯ 19 unchanged lines", "⋯ 6 unchanged lines"]
        middle = next(i for i, r in enumerate(diff.rows) if r.text == "⋯ 19 unchanged lines")
        diff.focus()
        diff.cursor = middle
        await pilot.press("enter")
        await settle(pilot, app)
        assert [r.text for r in diff.rows if r.kind == "gap"] == gaps[:1] + gaps[2:]
        fold = diff.rows[diff.cursor]  # the expanded lines keep a handle to fold them again
        assert (fold.kind, fold.text) == ("fold", "⌃ 19 unchanged lines")
        await pilot.press("enter")
        await settle(pilot, app)
        assert [r.text for r in diff.rows if r.kind == "gap"] == gaps
        await pilot.press("enter")  # open it again
        await settle(pilot, app)
        await pilot.press("down")
        row = diff.rows[diff.cursor]
        assert (row.text, row.old_no, row.new_no) == ("line 9", 9, 9)
        await pilot.press("f")  # whole file: every line, no gaps or hunk headers
        await settle(pilot, app)
        assert not any(r.kind in ("gap", "hunk") for r in diff.rows)
        assert [r.new_no for r in diff.rows if r.marker != "-"] == list(range(1, 41))
        assert diff.rows[diff.cursor].text == "line 9"  # still on the same line
        await pilot.press("f")  # back to changes only: opened gaps are folded again
        await settle(pilot, app)
        assert [r.text for r in diff.rows if r.kind == "gap"] == gaps
        await pilot.press("f")
        await settle(pilot, app)
        # staging a hunk still works on the whole-file view
        diff.cursor = next(i for i, r in enumerate(diff.rows) if r.text == "FIVE")
        await pilot.press("space")
        await settle(pilot, app)
        staged = repo.git("diff", "--cached")
        assert "+FIVE" in staged and "THIRTY-ONE" not in staged
