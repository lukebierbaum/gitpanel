"""Banner for an in-progress rebase/merge/cherry-pick/revert/bisect."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.message import Message
from textual.widgets import Button, Label

from ...git.operations import OperationState


class OperationBanner(Horizontal):
    DEFAULT_CSS = """
    OperationBanner {
        height: auto;
        background: $warning 25%;
        padding: 0 1;
        display: none;
    }
    OperationBanner.-active { display: block; }
    OperationBanner Label { width: 1fr; padding: 1 0 0 0; }
    OperationBanner Button { min-width: 10; margin-left: 1; }
    """

    class Requested(Message):
        def __init__(self, action_id: str) -> None:
            super().__init__()
            self.action_id = action_id

    def compose(self) -> ComposeResult:
        yield Label("", id="op-label")
        yield Button("Continue", id="op-continue", variant="primary", compact=True)
        yield Button("Skip", id="op-skip", compact=True)
        yield Button("Abort", id="op-abort", variant="error", compact=True)

    def show_state(self, state: OperationState | None) -> None:
        self.set_class(state is not None, "-active")
        if state is None:
            return
        text = state.label
        if state.conflicts:
            text += f" — {state.conflicts} conflict{'s' if state.conflicts != 1 else ''} to resolve"
        elif state.kind.value != "bisect":
            text += " — no conflicts left, ready to continue"
        self.query_one("#op-label", Label).update(text)
        self.query_one("#op-continue", Button).disabled = bool(state.conflicts) or (
            state.kind.value == "bisect"
        )
        self.query_one("#op-skip", Button).display = state.kind.value in (
            "rebase",
            "cherry-pick",
            "revert",
            "am",
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        mapping = {"op-continue": "op.continue", "op-skip": "op.skip", "op-abort": "op.abort"}
        if event.button.id in mapping:
            self.post_message(self.Requested(mapping[event.button.id]))
