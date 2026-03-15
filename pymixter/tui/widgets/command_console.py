"""Command console — vim-style ':' command input."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import ModalScreen
from textual.widgets import Input


class CommandConsole(ModalScreen[str | None]):
    """Modal command input. Returns the command string or None."""

    BINDINGS = [Binding("escape", "cancel", show=False)]

    CSS = """
    CommandConsole {
        align: center bottom;
    }
    #cmd-input {
        dock: bottom;
        width: 100%;
        height: 1;
        border: none;
        background: $panel;
    }
    """

    def compose(self) -> ComposeResult:
        yield Input(placeholder=":", id="cmd-input")

    def on_mount(self):
        self.query_one("#cmd-input", Input).focus()

    def on_input_submitted(self, event: Input.Submitted):
        cmd = event.value.strip()
        self.dismiss(cmd or None)

    def action_cancel(self):
        self.dismiss(None)
