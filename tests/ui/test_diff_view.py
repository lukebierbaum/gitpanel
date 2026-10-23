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
