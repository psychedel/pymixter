"""Command console — fuzzy-searchable command palette with descriptions."""

from __future__ import annotations

from dataclasses import dataclass

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, OptionList
from textual.widgets.option_list import Option

from pymixter.tui.widgets.fuzzy_finder import fuzzy_match


@dataclass
class Command:
    name: str
    description: str
    usage: str  # empty = no args needed
    category: str


COMMANDS: list[Command] = [
    # ── Playback ──
    Command("play", "Play track by index", "<index>", "playback"),
    Command("stop", "Stop playback", "", "playback"),
    Command("seek", "Seek to position in seconds", "<seconds>", "playback"),
    Command("playmix", "Render & play the full mix", "", "playback"),
    Command("preview", "Preview transition between tracks", "[index]", "playback"),
    Command("xfader", "Set crossfader position (0.0–1.0)", "<value>", "playback"),
    Command("deckb", "Load track into deck B", "<index>", "playback"),
    # ── Library ──
    Command("add", "Add audio file to library", "<path>", "library"),
    Command("scan", "Scan directory for audio files", "<dir>", "library"),
    Command("analyze", "Run audio analysis on track(s)", "[index|all]", "library"),
    Command("bpm", "Edit BPM of selected track", "<value>", "library"),
    Command("gain", "Adjust track gain in dB", "<index> <dB>", "library"),
    Command("stems", "Separate track into stems", "[index]", "library"),
    Command("import", "Import Rekordbox/Traktor XML", "<file>", "library"),
    Command("open", "Open a project file", "<file>", "library"),
    # ── Timeline ──
    Command("timeline", "Timeline operations (add/remove/move)", "<sub> [args]", "timeline"),
    Command("transition", "Set transition type/length", "<from> <type> [bars]", "timeline"),
    Command("cue", "Set cue point on a track", "<index> <time>", "timeline"),
    Command("grid", "Adjust beat grid offset", "<index> <offset>", "timeline"),
    Command("automix", "Auto-arrange tracks optimally", "[strategy]", "timeline"),
    Command("zoom", "Zoom into transition detail", "[index]", "timeline"),
    # ── DSP / EQ ──
    Command("eq", "Set EQ band value", "<band> <value>", "dsp"),
    # ── Render / Export ──
    Command("render", "Render mix to audio file", "[output]", "render"),
    Command("validate", "Check mix for issues", "", "render"),
    Command("export", "Export project as JSON/XML", "[path]", "render"),
    # ── General ──
    Command("save", "Save project", "", "general"),
    Command("suggest", "Suggest next track to add", "", "general"),
    Command("info", "Show project info", "", "general"),
    Command("undo", "Undo last action", "", "general"),
    Command("redo", "Redo last undone action", "", "general"),
    Command("quit", "Exit PyMixter", "", "general"),
]

_CATEGORY_LABELS = {
    "playback": "▶ Playback",
    "library": "♫ Library",
    "timeline": "◆ Timeline",
    "dsp": "◈ DSP / EQ",
    "render": "⬡ Render / Export",
    "general": "⚙ General",
}

_CATEGORY_ORDER = list(_CATEGORY_LABELS.keys())


class CommandConsole(ModalScreen[str | None]):
    """Fuzzy command palette. Returns the command string or None."""

    BINDINGS = [Binding("escape", "cancel", show=False)]

    CSS = """
    CommandConsole {
        align: center middle;
    }
    #cmd-container {
        width: 90%;
        max-width: 72;
        height: 80%;
        max-height: 24;
        background: $surface;
        border: solid $primary;
    }
    #cmd-input {
        dock: top;
        border: none;
        background: $panel;
    }
    #cmd-list {
        height: 1fr;
        background: $surface;
    }
    """

    def __init__(self):
        super().__init__()
        self._filtered: list[Command] = list(COMMANDS)

    def compose(self) -> ComposeResult:
        with Vertical(id="cmd-container"):
            yield Input(placeholder="Type command…", id="cmd-input")
            yield OptionList(id="cmd-list")

    def on_mount(self):
        self._show_grouped()
        self.query_one("#cmd-input", Input).focus()

    def _show_grouped(self):
        """Show all commands grouped by category."""
        ol = self.query_one("#cmd-list", OptionList)
        ol.clear_options()
        self._filtered = []
        by_cat: dict[str, list[Command]] = {}
        for cmd in COMMANDS:
            by_cat.setdefault(cmd.category, []).append(cmd)
        for cat in _CATEGORY_ORDER:
            cmds = by_cat.get(cat, [])
            if not cmds:
                continue
            ol.add_option(Option(f"── {_CATEGORY_LABELS[cat]} ──", disabled=True))
            for cmd in cmds:
                usage = f" {cmd.usage}" if cmd.usage else ""
                label = f"  :{cmd.name}{usage}  — {cmd.description}"
                ol.add_option(Option(label))
                self._filtered.append(cmd)

    def _show_filtered(self, query: str):
        """Show fuzzy-matched commands."""
        ol = self.query_one("#cmd-list", OptionList)
        ol.clear_options()
        scored: list[tuple[int, Command]] = []
        for cmd in COMMANDS:
            # Match against name, description, and category
            text = f"{cmd.name} {cmd.description} {cmd.category}"
            matched, score = fuzzy_match(query, text)
            if matched:
                scored.append((score, cmd))
        scored.sort(key=lambda x: -x[0])
        self._filtered = [cmd for _, cmd in scored]
        for cmd in self._filtered:
            usage = f" {cmd.usage}" if cmd.usage else ""
            label = f"  :{cmd.name}{usage}  — {cmd.description}"
            ol.add_option(Option(label))

    def on_input_changed(self, event: Input.Changed):
        query = event.value.strip().lstrip(":")
        if not query:
            self._show_grouped()
        else:
            self._show_filtered(query)

    def _select_current(self):
        """Select the highlighted command."""
        inp = self.query_one("#cmd-input", Input).value.strip().lstrip(":")
        ol = self.query_one("#cmd-list", OptionList)

        # If input already has args after a known command, send as-is
        parts = inp.split(None, 1)
        if parts:
            for cmd in COMMANDS:
                if cmd.name == parts[0] and len(parts) > 1:
                    self.dismiss(inp)
                    return

        # Otherwise pick from the list
        if ol.option_count > 0 and self._filtered:
            hi = ol.highlighted
            # Skip separator/disabled options
            if hi is not None:
                # Map visible index to filtered index (account for separators)
                cmd = self._get_command_at(hi)
            else:
                cmd = self._filtered[0] if self._filtered else None

            if cmd:
                if not cmd.usage:
                    # No args needed — execute immediately
                    self.dismiss(cmd.name)
                else:
                    # Needs args — fill input so user can type args
                    inp_widget = self.query_one("#cmd-input", Input)
                    inp_widget.value = f"{cmd.name} "
                    inp_widget.cursor_position = len(inp_widget.value)
                    return
        elif inp:
            # User typed something not matching any command — send raw
            self.dismiss(inp)
            return

        # Nothing selected
        if not self._filtered and not inp:
            self.dismiss(None)

    def _get_command_at(self, highlighted: int) -> Command | None:
        """Map OptionList highlighted index to a Command, skipping separators."""
        ol = self.query_one("#cmd-list", OptionList)
        cmd_idx = 0
        for i in range(ol.option_count):
            opt = ol.get_option_at_index(i)
            if opt.disabled:
                continue
            if i == highlighted:
                return self._filtered[cmd_idx] if cmd_idx < len(self._filtered) else None
            cmd_idx += 1
        return None

    def on_input_submitted(self, _event: Input.Submitted):
        self._select_current()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected):
        cmd = self._get_command_at(event.option_index)
        if cmd:
            if not cmd.usage:
                self.dismiss(cmd.name)
            else:
                inp = self.query_one("#cmd-input", Input)
                inp.value = f"{cmd.name} "
                inp.cursor_position = len(inp.value)
                inp.focus()

    def action_cancel(self):
        self.dismiss(None)
