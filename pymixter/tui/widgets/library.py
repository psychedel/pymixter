"""Library panel — browse and select tracks."""

from textual.widgets import DataTable
from textual.message import Message

from pymixter.core.project import Project


class LibraryTable(DataTable):
    """Displays the track library with BPM, key, duration."""

    class TrackSelected(Message):
        def __init__(self, track_index: int):
            super().__init__()
            self.track_index = track_index

    def on_mount(self):
        self.add_column("Title", key="title")
        self.add_column("BPM", key="bpm", width=6)
        self.add_column("Key", key="key", width=4)
        self.add_column("Dur", key="dur", width=5)
        self.cursor_type = "row"

    def refresh_library(self, project: Project):
        self.clear()
        for i, t in enumerate(project.library):
            bpm = str(t.bpm) if t.bpm else "—"
            key = t.key or "—"
            dur = f"{int(t.duration // 60)}:{int(t.duration % 60):02d}" if t.duration else "—"
            self.add_row(t.title, bpm, key, dur, key=str(i))

    def on_data_table_row_selected(self, event: DataTable.RowSelected):
        try:
            idx = int(str(event.row_key.value))
            self.post_message(self.TrackSelected(idx))
        except (ValueError, AttributeError):
            pass
