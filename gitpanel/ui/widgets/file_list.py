"""The changed-files list that sits beside a diff (Log, Shelf, Compare, PRs).

It owns the files of one change, draws them the same way everywhere (status letter in its
colour, path, `← old path` for renames) and reports the highlighted file with
`FileList.Selected`; the window shows it in its DiffView:

    @on(FileList.Selected, "#log-files")
    def _file(self, event: FileList.Selected) -> None:
        self.query_one(DiffView).set_diff(event.source, event.diff)
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from rich.text import Text
from textual.message import Message
from textual.widgets import OptionList
from textual.widgets.option_list import Option

from ...git import patch as P
from ..colors import STATUS_COLOR, C

Decorate = Callable[[P.FileDiff], Text | None]


def file_label(d: P.FileDiff, extra: Text | None = None) -> Text:
    label = Text()
    label.append(f"{d.status} ", style=C[STATUS_COLOR.get(d.status, "")])
    label.append(d.path)
    if d.status in "RC" and d.old_path:
        label.append(f"  ← {d.old_path}", style="dim")
    if extra is not None:
        label.append_text(extra)
    return label


class FileList(OptionList):
    DEFAULT_CSS = """
    FileList { background: $surface; border: none; }
    """

    class Selected(Message):
        """The highlighted file changed (`diff` is None when the list is empty)."""

        def __init__(
            self, file_list: FileList, source: P.DiffSource | None, diff: P.FileDiff | None
        ) -> None:
            super().__init__()
            self.file_list = file_list
            self.source = source
            self.diff = diff

        @property
        def control(self) -> FileList:
            return self.file_list

    def __init__(self, *, id: str | None = None) -> None:
        super().__init__(id=id, compact=True)
        self.source: P.DiffSource | None = None
        self.diffs: list[P.FileDiff] = []
        self._decorate: Decorate | None = None

    def set_files(
        self,
        source: P.DiffSource | None,
        diffs: Sequence[P.FileDiff],
        *,
        decorate: Decorate | None = None,
    ) -> None:
        """Show `diffs` (from `source`) and highlight the first; `decorate` adds to labels."""
        self.source = source
        self.diffs = list(diffs)
        self._decorate = decorate
        self.clear_options()
        for d in self.diffs:
            self.add_option(Option(self._label(d), id=d.path))
        if self.diffs:
            self.highlighted = 0
        else:
            self.post_message(self.Selected(self, source, None))

    def redecorate(self, decorate: Decorate | None) -> None:
        """Redraw the labels with new decorations (e.g. comment counts that just arrived)."""
        self._decorate = decorate
        for d in self.diffs:
            self.replace_option_prompt(d.path, self._label(d))

    def _label(self, d: P.FileDiff) -> Text:
        return file_label(d, self._decorate(d) if self._decorate else None)

    @property
    def current(self) -> P.FileDiff | None:
        if self.highlighted is None or not 0 <= self.highlighted < len(self.diffs):
            return None
        return self.diffs[self.highlighted]

    def _on_option_list_option_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        if event.option_list is self:
            self.post_message(self.Selected(self, self.source, self.current))
