"""Timeline widget — visual representation of the mix with waveforms."""

from textual.message import Message
from textual.widgets import Static
from textual.reactive import reactive
from textual.events import Click
from rich.text import Text

from pymixter.core.project import Project, to_camelot


_BLOCKS = " ▁▂▃▄▅▆▇"
_SPARK = "▁▂▃▄▅▆▇█"


class TimelineView(Static):
    """Renders the timeline with waveform blocks, transitions, and energy arc."""

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
        self._block_ranges: list[tuple[int, int, int]] = []

    def render(self) -> Text:
        if not self.project.timeline:
            return Text("  Timeline is empty. Select a track and press 'a' to add.",
                        style="dim")

        text = Text()
        width = self.size.width - 4 if self.size.width > 4 else 40

        tracks = []
        total_dur = 0.0
        for tidx in self.project.timeline:
            if tidx < len(self.project.library):
                t = self.project.library[tidx]
                tracks.append(t)
                total_dur += t.duration or 0
            else:
                tracks.append(None)
        total_dur = total_dur or 1.0

        # Calculate bar widths
        bar_widths = []
        for t in tracks:
            if t and t.duration:
                frac = t.duration / total_dur
            else:
                frac = 1 / len(tracks)
            bar_widths.append(max(4, int(frac * width)))

        # ── Row 1: Track title blocks ──
        self._block_ranges.clear()
        x = 0
        for pos, (t, bw) in enumerate(zip(tracks, bar_widths)):
            color = self.COLORS[pos % len(self.COLORS)]
            if t:
                label = f" {t.title[:bw - 2]} "
                bar = label.ljust(bw, "░")[:bw]
            else:
                bar = "?" * bw
            text.append(bar, style=f"bold #2a2d2a on {color}")
            self._block_ranges.append((x, x + bw, pos))
            x += bw
        text.append("\n")

        # ── Row 2: Mini waveforms inside blocks ──
        for pos, (t, bw) in enumerate(zip(tracks, bar_widths)):
            color = self.COLORS[pos % len(self.COLORS)]
            if t and t.waveform:
                wf_text = _mini_waveform(t.waveform, bw, color)
                text.append_text(wf_text)
            elif t and t.energy:
                en_text = _mini_waveform(t.energy, bw, color)
                text.append_text(en_text)
            else:
                text.append("░" * bw, style=f"dim {color}")
        text.append("\n")

        # ── Row 3: BPM + Key labels ──
        for pos, (t, bw) in enumerate(zip(tracks, bar_widths)):
            if t:
                bpm_str = f"{t.bpm:g}" if t.bpm else "?"
                camelot = to_camelot(t.key)
                label = f"{bpm_str} {camelot}"
                padded = label.center(bw)[:bw]
                text.append(padded, style="dim")
            else:
                text.append(" " * bw, style="dim")
        text.append("\n")

        # ── Row 4: Transition markers ──
        tr_lookup = {tr.from_track: tr for tr in self.project.transitions}
        has_transitions = False
        x = 0
        for pos in range(len(tracks)):
            bw = bar_widths[pos]
            tr = tr_lookup.get(pos)
            if tr:
                has_transitions = True
                sym = {
                    "crossfade": "╲╱",
                    "eq_fade": "≋≋",
                    "cut": "┃┃",
                    "echo_out": "»»",
                    "filter_sweep": "∿∿",
                }.get(tr.type, "??")
                label = f"{sym}{tr.length_bars}b"
                # Position at the end of the block (transition zone)
                padding = bw - len(label)
                text.append(" " * max(0, padding), style="")
                text.append(label, style="#c8a848")
            else:
                text.append(" " * bw, style="")
            x += bw
        if has_transitions:
            text.append("\n")

        # ── Row 5: Combined energy arc across entire mix ──
        all_energy = _combine_energy(tracks, bar_widths, width)
        if all_energy:
            text.append("\n")
            text.append("  ", style="")
            arc_text = _render_energy_arc(all_energy, width)
            text.append_text(arc_text)
            text.append("\n")

        # ── Row 6: Time ruler ──
        text.append("  ", style="")
        markers = min(8, max(4, width // 12))
        step = total_dur / markers if total_dur else 1.0
        for i in range(markers + 1):
            t_sec = step * i
            m, s = divmod(int(t_sec), 60)
            label = f"{m}:{s:02d}"
            target_col = int(i * width / markers)
            padding = target_col - (text.cell_len - 2)  # -2 for leading spaces
            if padding > 0:
                text.append(" " * padding, style="")
            text.append(label, style="dim")

        return text

    def on_click(self, event: Click) -> None:
        for x_start, x_end, pos in self._block_ranges:
            if x_start <= event.x < x_end:
                lib_idx = self.project.timeline[pos]
                self.post_message(self.TrackClicked(pos, lib_idx))
                break

    def refresh_timeline(self, project: Project):
        self.project = project
        self.project_version = project.get_version()


def _mini_waveform(data: list[float], width: int, base_color: str) -> Text:
    """Render a mini waveform that fits inside a timeline block."""
    n = len(data)
    text = Text()
    peak = max(data) if data else 1.0
    if peak <= 0:
        peak = 1.0

    for i in range(width):
        src_start = int(i * n / width)
        src_end = max(src_start + 1, int((i + 1) * n / width))
        chunk = data[src_start:src_end]
        val = (max(chunk) / peak) if chunk else 0.0
        idx = min(int(val * (len(_BLOCKS) - 1)), len(_BLOCKS) - 1)
        text.append(_BLOCKS[idx], style=base_color)
    return text


def _combine_energy(tracks, bar_widths, total_width) -> list[float]:
    """Combine per-track energy into one continuous profile."""
    result = []
    for t, bw in zip(tracks, bar_widths):
        if t and t.energy:
            n = len(t.energy)
            for i in range(bw):
                src = int(i * n / bw)
                result.append(t.energy[min(src, n - 1)])
        elif t and t.waveform:
            n = len(t.waveform)
            for i in range(bw):
                src = int(i * n / bw)
                result.append(t.waveform[min(src, n - 1)])
        else:
            result.extend([0.0] * bw)
    return result


def _render_energy_arc(energy: list[float], width: int) -> Text:
    """Render the overall mix energy arc as a sparkline."""
    n = len(energy)
    text = Text()
    peak = max(energy) if energy else 1.0
    if peak <= 0:
        peak = 1.0

    for i in range(width):
        src_start = int(i * n / width)
        src_end = max(src_start + 1, int((i + 1) * n / width))
        chunk = energy[src_start:src_end]
        val = (sum(chunk) / len(chunk) / peak) if chunk else 0.0
        idx = min(int(val * (len(_SPARK) - 1)), len(_SPARK) - 1)

        if val < 0.35:
            color = "#7a8a50"
        elif val < 0.6:
            color = "#a8b060"
        elif val < 0.8:
            color = "#c8a848"
        else:
            color = "#c87848"

        text.append(_SPARK[idx], style=color)
    return text
