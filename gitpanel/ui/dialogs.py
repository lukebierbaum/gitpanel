"""Modal dialogs backing the `Ui` protocol."""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence

from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, OptionList, RichLog, Static, TextArea
from textual.widgets.option_list import Option

DIALOG_CSS = """
.dialog {
    width: 72;
    max-width: 90%;
    height: auto;
    max-height: 90%;
    border: thick $primary 60%;
    background: $panel;
    padding: 1 2;
}
.dialog.-danger { border: thick $error 70%; }
.dialog .title { text-style: bold; margin-bottom: 1; }
.dialog .body { margin-bottom: 1; }
.dialog .buttons { height: auto; align-horizontal: right; }
.dialog .buttons Button { margin-left: 1; }
"""


class ConfirmDialog(ModalScreen[bool]):
    DEFAULT_CSS = "ConfirmDialog { align: center middle; }" + DIALOG_CSS
    BINDINGS = [
        Binding("escape", "dismiss(False)", "Cancel"),
        Binding("y", "dismiss(True)", "Yes", show=False),
        Binding("n", "dismiss(False)", "No", show=False),
    ]

    def __init__(self, title: str, body: str, danger: bool) -> None:
        super().__init__()
        self.title_text, self.body, self.danger = title, body, danger

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog" + (" -danger" if self.danger else "")):
            yield Label(self.title_text, classes="title")
            if self.body:
                yield Static(self.body, classes="body", markup=False)
            with Horizontal(classes="buttons"):
                yield Button("Cancel", id="no")
                yield Button("OK", id="yes", variant="error" if self.danger else "primary")

    def on_mount(self) -> None:
        self.query_one("#yes", Button).focus()

    @on(Button.Pressed)
    def pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "yes")


class PromptDialog(ModalScreen[str | None]):
    DEFAULT_CSS = "PromptDialog { align: center middle; }" + DIALOG_CSS
    BINDINGS = [Binding("escape", "dismiss(None)", "Cancel")]

    def __init__(self, title: str, default: str, placeholder: str) -> None:
        super().__init__()
        self.title_text, self.default, self.placeholder = title, default, placeholder

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Label(self.title_text, classes="title")
            yield Input(self.default, placeholder=self.placeholder, id="value")
            with Horizontal(classes="buttons"):
                yield Button("Cancel", id="no")
                yield Button("OK", id="yes", variant="primary")

    def on_mount(self) -> None:
        self.query_one(Input).focus()

    @on(Input.Submitted)
    def submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value)

    @on(Button.Pressed)
    def pressed(self, event: Button.Pressed) -> None:
        self.dismiss(self.query_one(Input).value if event.button.id == "yes" else None)


class TextDialog(ModalScreen[str | None]):
    """Multi-line prompt (commit messages). ctrl+s / ctrl+enter accept."""

    DEFAULT_CSS = (
        "TextDialog { align: center middle; } TextDialog TextArea { height: 12; }" + DIALOG_CSS
    )
    BINDINGS = [
        Binding("escape", "dismiss(None)", "Cancel"),
        Binding("ctrl+s,ctrl+enter", "accept", "OK", priority=True),
    ]

    def __init__(self, title: str, default: str) -> None:
        super().__init__()
        self.title_text, self.default = title, default

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Label(self.title_text + "  (ctrl+s to accept)", classes="title")
            yield TextArea(self.default, id="value", soft_wrap=True)
            with Horizontal(classes="buttons"):
                yield Button("Cancel", id="no")
                yield Button("OK", id="yes", variant="primary")

    def on_mount(self) -> None:
        self.query_one(TextArea).focus()

    def action_accept(self) -> None:
        self.dismiss(self.query_one(TextArea).text)

    @on(Button.Pressed)
    def pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "yes":
            self.action_accept()
        else:
            self.dismiss(None)


class ChooseDialog(ModalScreen[str | None]):
    DEFAULT_CSS = (
        "ChooseDialog { align: center middle; }"
        "ChooseDialog OptionList { height: auto; max-height: 20; }" + DIALOG_CSS
    )
    BINDINGS = [Binding("escape", "dismiss(None)", "Cancel")]

    def __init__(self, title: str, options: Sequence[tuple[str, str]]) -> None:
        super().__init__()
        self.title_text, self.options = title, list(options)

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Label(self.title_text, classes="title")
            yield OptionList(*[Option(label, id=value) for value, label in self.options])

    def on_mount(self) -> None:
        self.query_one(OptionList).focus()

    @on(OptionList.OptionSelected)
    def selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(event.option.id)


class ErrorDialog(ModalScreen[None]):
    DEFAULT_CSS = (
        "ErrorDialog { align: center middle; }"
        "ErrorDialog RichLog { height: auto; max-height: 24; }" + DIALOG_CSS
    )
    BINDINGS = [Binding("escape,enter", "dismiss(None)", "Close")]

    def __init__(self, title: str, details: str) -> None:
        super().__init__()
        self.title_text, self.details = title, details

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog -danger"):
            yield Label(f"{self.title_text} failed", classes="title")
            log = RichLog(wrap=True, markup=False, highlight=False)
            yield log
            with Horizontal(classes="buttons"):
                yield Button("Close", id="close", variant="primary")

    def on_mount(self) -> None:
        self.query_one(RichLog).write(self.details)
        self.query_one(Button).focus()

    @on(Button.Pressed)
    def pressed(self) -> None:
        self.dismiss(None)


class OutputDialog(ModalScreen[int]):
    """Streams command output; closes with the exit code when the user dismisses it."""

    DEFAULT_CSS = (
        "OutputDialog { align: center middle; } OutputDialog RichLog { height: 20; }" + DIALOG_CSS
    )
    BINDINGS = [Binding("escape", "close", "Close")]

    def __init__(self, title: str, stream: AsyncIterator[str]) -> None:
        super().__init__()
        self.title_text, self.stream = title, stream
        self.code = 0
        self.done = False

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Label(self.title_text, classes="title")
            yield RichLog(wrap=True, markup=False)
            with Horizontal(classes="buttons"):
                yield Button("Close", id="close", variant="primary", disabled=True)

    def on_mount(self) -> None:
        self.pump()

    @work
    async def pump(self) -> None:
        log = self.query_one(RichLog)
        try:
            async for line in self.stream:
                log.write(line)
        except Exception as e:  # show, don't crash the app
            log.write(f"error: {e}")
            self.code = 1
        self.done = True
        self.query_one(Button).disabled = False
        self.query_one(Button).focus()

    def action_close(self) -> None:
        if self.done:
            self.dismiss(self.code)

    @on(Button.Pressed)
    def pressed(self) -> None:
        self.action_close()
