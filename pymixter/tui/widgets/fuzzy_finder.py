"""Fuzzy finder overlay — fzf-style search over library or files."""

from __future__ import annotations

from pathlib import Path

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, Static, OptionList
from textual.widgets.option_list import Option

from pymixter.core.project import AUDIO_EXTENSIONS


def fuzzy_match(query: str, text: str) -> tuple[bool, int]:
    """Check if query fuzzy-matches text. Returns (matched, score)."""
    query = query.lower()
    text_lower = text.lower()

    if query in text_lower:
        return True, 100 - text_lower.index(query)

    qi = 0
    score = 0
    for c in text_lower:
        if qi < len(query) and c == query[qi]:
            qi += 1
            score += 1
    return (True, score) if qi == len(query) else (False, 0)


class FuzzyFinder(ModalScreen[int | None]):
    """Modal fuzzy search over indexed items. Returns selected index or None."""

    BINDINGS = [Binding("escape", "cancel", show=False)]

    CSS = """
    FuzzyFinder {
        align: center middle;
    }
    #fuzzy-container {
        width: 90%;
        max-width: 80;
        height: 80%;
        max-height: 25;
        background: $surface;
        border: solid $primary;
    }
    #fuzzy-input {
        dock: top;
        border: none;
        background: $panel;
    }
    #fuzzy-list {
        height: 1fr;
        background: $surface;
    }
    """

    def __init__(self, items: list[str]):
        super().__init__()
        self._all_items = items
        self._indices: list[int] = list(range(len(items)))

    def compose(self) -> ComposeResult:
        with Vertical(id="fuzzy-container"):
            yield Input(placeholder="Search...", id="fuzzy-input")
            yield OptionList(id="fuzzy-list")

    def on_mount(self):
        self._update_list(self._all_items, self._indices)
        self.query_one("#fuzzy-input", Input).focus()

    def _update_list(self, labels: list[str], indices: list[int]):
        option_list = self.query_one("#fuzzy-list", OptionList)
        option_list.clear_options()
        self._indices = indices
        for label in labels:
            option_list.add_option(Option(label))

    def on_input_changed(self, event: Input.Changed):
        query = event.value.strip()
        if not query:
            self._update_list(self._all_items, list(range(len(self._all_items))))
            return

        scored = []
        for i, item in enumerate(self._all_items):
            matched, score = fuzzy_match(query, item)
            if matched:
                scored.append((score, i, item))

        scored.sort(key=lambda x: -x[0])
        self._update_list(
            [item for _, _, item in scored],
            [i for _, i, _ in scored],
        )

    def _select_highlighted(self):
        option_list = self.query_one("#fuzzy-list", OptionList)
        if option_list.option_count > 0:
            hi = option_list.highlighted or 0
            self.dismiss(self._indices[hi])
        else:
            self.dismiss(None)

    def on_input_submitted(self, _event: Input.Submitted):
        self._select_highlighted()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected):
        idx = event.option_index
        self.dismiss(self._indices[idx])

    def action_cancel(self):
        self.dismiss(None)


class FileBrowser(ModalScreen[str | None]):
    """File browser for selecting audio files. Returns path or None."""

    BINDINGS = [Binding("escape", "cancel", show=False)]

    CSS = """
    FileBrowser {
        align: center middle;
    }
    #browser-container {
        width: 90%;
        max-width: 80;
        height: 85%;
        max-height: 28;
        background: $surface;
        border: solid $primary;
    }
    #browser-path {
        dock: top;
        height: 1;
        background: $panel;
        padding: 0 1;
    }
    #browser-filter {
        dock: top;
        border: none;
        background: $panel;
    }
    #browser-list {
        height: 1fr;
        background: $surface;
    }
    """

    def __init__(self, start_path: str | None = None):
        super().__init__()
        self._cwd = Path(start_path or Path.home()).resolve()
        self._entry_paths: list[Path | None] = []

    def compose(self) -> ComposeResult:
        with Vertical(id="browser-container"):
            yield Static(str(self._cwd), id="browser-path")
            yield Input(placeholder="filter...", id="browser-filter")
            yield OptionList(id="browser-list")

    def on_mount(self):
        self._refresh_listing()
        self.query_one("#browser-filter", Input).focus()

    def _refresh_listing(self, filter_text: str = ""):
        self.query_one("#browser-path", Static).update(str(self._cwd))
        option_list = self.query_one("#browser-list", OptionList)
        option_list.clear_options()
        self._entry_paths = [None]  # index 0 = parent
        option_list.add_option(Option(".. (up)"))

        try:
            entries = sorted(
                self._cwd.iterdir(),
                key=lambda p: (not p.is_dir(), p.name.lower()),
            )
        except PermissionError:
            return

        filter_lower = filter_text.lower()
        for entry in entries:
            if entry.name.startswith("."):
                continue
            is_audio = entry.suffix.lower() in AUDIO_EXTENSIONS
            if not entry.is_dir() and not is_audio:
                continue
            if filter_lower and filter_lower not in entry.name.lower():
                continue
            label = f"[dir] {entry.name}/" if entry.is_dir() else f"  {entry.name}"
            option_list.add_option(Option(label))
            self._entry_paths.append(entry)

    def _navigate(self, idx: int):
        if idx == 0:
            path = self._cwd.parent
        else:
            path = self._entry_paths[idx]
            if path is None:
                return

        if path.is_dir():
            self._cwd = path
            self.query_one("#browser-filter", Input).value = ""
            self._refresh_listing()
        else:
            self.dismiss(str(path))

    def on_input_changed(self, event: Input.Changed):
        self._refresh_listing(event.value.strip())

    def on_option_list_option_selected(self, event: OptionList.OptionSelected):
        self._navigate(event.option_index)

    def on_input_submitted(self, _event: Input.Submitted):
        option_list = self.query_one("#browser-list", OptionList)
        if option_list.option_count > 0:
            self._navigate(option_list.highlighted or 0)

    def action_cancel(self):
        self.dismiss(None)
