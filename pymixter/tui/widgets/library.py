"""Library panel — browse and select tracks with compatibility indicators."""

from rich.text import Text
from textual.widgets import DataTable
from textual.message import Message

from pymixter.core.project import (
    Project, to_camelot, key_compatibility, bpm_compatibility,
)


# Colors for compatibility levels
_COMPAT_COLORS = {
    "perfect": "#a8b060",    # green — same key / near-identical BPM
    "compatible": "#c8cc6e", # light green — harmonic neighbor
    "close": "#c8a848",      # yellow — small BPM difference
    "clash": "#c87848",      # red — key clash
    "far": "#c87848",        # red — large BPM gap
    "unknown": "default",    # no data
}


class LibraryTable(DataTable):
    """Displays the track library with BPM, key, duration, and compatibility."""

    class TrackSelected(Message):
        def __init__(self, track_index: int):
            super().__init__()
            self.track_index = track_index

    def on_mount(self):
        self.add_column("Title", key="title")
        self.add_column("BPM", key="bpm", width=7)
        self.add_column("Key", key="key", width=8)
        self.add_column("Dur", key="dur", width=5)
        self.add_column("E", key="energy", width=6)
        self.cursor_type = "row"

    def refresh_library(self, project: Project, reference_idx: int | None = None):
        """Refresh library table. If reference_idx given, color by compatibility."""
        self.clear()

        # Determine reference track for compatibility coloring
        ref = None
        if reference_idx is not None and reference_idx < len(project.library):
            ref = project.library[reference_idx]
        elif project.timeline:
            last_idx = project.timeline[-1]
            if last_idx < len(project.library):
                ref = project.library[last_idx]

        for i, t in enumerate(project.library):
            # BPM cell with compatibility color
            if t.bpm:
                bpm_str = f"{t.bpm:g}"
                if ref and ref.bpm and ref is not t:
                    bc = bpm_compatibility(ref.bpm, t.bpm)
                    bpm_cell = Text(bpm_str, style=_COMPAT_COLORS[bc])
                else:
                    bpm_cell = Text(bpm_str)
            else:
                bpm_cell = Text("—", style="dim")

            # Key cell: standard + Camelot, with compatibility color
            if t.key:
                camelot = to_camelot(t.key)
                key_str = f"{t.key} {camelot}"
                if ref and ref.key and ref is not t:
                    kc = key_compatibility(ref.key, t.key)
                    key_cell = Text(key_str, style=_COMPAT_COLORS[kc])
                else:
                    key_cell = Text(key_str)
            else:
                key_cell = Text("—", style="dim")

            # Duration
            if t.duration:
                dur = f"{int(t.duration // 60)}:{int(t.duration % 60):02d}"
            else:
                dur = "—"

            # Mini energy sparkline (6 chars)
            energy_cell = _mini_energy(t.energy) if t.energy else Text("", style="dim")

            self.add_row(t.title, bpm_cell, key_cell, dur, energy_cell, key=str(i))

    def on_data_table_row_selected(self, event: DataTable.RowSelected):
        try:
            idx = int(str(event.row_key.value))
            self.post_message(self.TrackSelected(idx))
        except (ValueError, AttributeError):
            pass


_SPARK = "▁▂▃▄▅▆▇█"


def _mini_energy(energy: list[float], width: int = 6) -> Text:
    """Render a tiny energy sparkline."""
    if not energy:
        return Text("")
    n = len(energy)
    text = Text()
    for i in range(width):
        src_start = int(i * n / width)
        src_end = max(src_start + 1, int((i + 1) * n / width))
        chunk = energy[src_start:src_end]
        val = max(chunk) if chunk else 0.0
        idx = min(int(val * (len(_SPARK) - 1)), len(_SPARK) - 1)
        # Color gradient
        if val < 0.4:
            color = "#7a8a50"
        elif val < 0.7:
            color = "#c8a848"
        else:
            color = "#c87848"
        text.append(_SPARK[idx], style=color)
    return text
