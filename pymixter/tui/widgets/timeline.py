"""Timeline widget — visual representation of the mix timeline."""

from textual.message import Message
from textual.widgets import Static
from textual.reactive import reactive
from textual.events import Click
from rich.text import Text

from pymixter.core.project import Project


class TimelineView(Static):
    """Renders the timeline as colored blocks. Click a track to select it."""

    project_version: reactive[int] = reactive(0)

    COLORS = ["#a8b060", "#c8cc6e", "#7a8a50", "#c8a848", "#8a9a60", "#606830"]

    class TrackClicked(Message):
        """Emitted when a track in the timeline is clicked."""
        def __init__(self, timeline_pos: int, library_idx: int):
            super().__init__()
            self.timeline_pos = timeline_pos
            self.library_idx = library_idx

    def __init__(self, project: Project, **kwargs):
        super().__init__(**kwargs)
        self.project = project
        self._block_ranges: list[tuple[int, int, int]] = []  # (x_start, x_end, timeline_pos)

    def render(self) -> Text:
        if not self.project.timeline:
            return Text("  Timeline is empty. Select a track and press 'a' to add.",
                        style="dim")

        text = Text()
        width = self.size.width - 4 if self.size.width > 4 else 40

        total_dur = sum(
            self.project.library[i].duration
            for i in self.project.timeline
            if i < len(self.project.library) and self.project.library[i].duration
        ) or 1.0

        # Track blocks
        self._block_ranges.clear()
        x = 0
        for pos, tidx in enumerate(self.project.timeline):
            track = self.project.library[tidx]
            color = self.COLORS[pos % len(self.COLORS)]
            frac = (track.duration / total_dur) if track.duration else (1 / len(self.project.timeline))
            bar_len = max(3, int(frac * width))

            label = f" {track.title[:bar_len - 2]} "
            bar = label.ljust(bar_len, "░")[:bar_len]
            text.append(bar, style=f"bold #2a2d2a on {color}")

            self._block_ranges.append((x, x + bar_len, pos))
            x += bar_len

        text.append("\n")

        # Transition markers
        tr_lookup = {tr.from_track: tr for tr in self.project.transitions}
        for pos in range(len(self.project.timeline) - 1):
            tr = tr_lookup.get(pos)
            if tr:
                sym = {"crossfade": "~", "eq_fade": "=", "cut": "|", "echo_out": ">"}.get(tr.type, "?")
                text.append(f" {sym}{tr.length_bars}b", style="dim")

        if self.project.transitions:
            text.append("\n")

        # Time markers
        markers = 5
        step = total_dur / markers
        for i in range(markers + 1):
            t = step * i
            m, s = divmod(int(t), 60)
            text.append(f" {m}:{s:02d}".ljust(width // markers), style="dim")

        return text

    def on_click(self, event: Click) -> None:
        """Handle click on timeline to select a track."""
        for x_start, x_end, pos in self._block_ranges:
            if x_start <= event.x < x_end:
                lib_idx = self.project.timeline[pos]
                self.post_message(self.TrackClicked(pos, lib_idx))
                break

    def refresh_timeline(self, project: Project):
        self.project = project
        self.project_version = project.get_version()
