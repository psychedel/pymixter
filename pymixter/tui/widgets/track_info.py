"""Track info panel — detailed visualization of the selected track."""

from textual.widgets import Static
from textual.reactive import reactive
from rich.panel import Panel
from rich.text import Text

from pymixter.core.project import Track, to_camelot


class TrackInfo(Static):
    """Displays details about the currently selected track."""

    playback_progress: reactive[float | None] = reactive(None)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._track: Track | None = None

    def show_track(self, track: Track):
        self._track = track
        self.refresh(layout=True)

    def watch_playback_progress(self, value: float | None) -> None:
        """Re-render when playback position changes."""
        self.refresh(layout=False)

    def render(self):
        if not self._track:
            return Panel("No track selected", title="Track Info")

        t = self._track
        dur_m, dur_s = divmod(int(t.duration), 60) if t.duration else (0, 0)
        wf_width = max(40, self.size.width - 6) if self.size.width > 10 else 60

        lines = Text()

        # ── Header line: title ──
        lines.append(f"  {t.title}\n", style="bold")
        lines.append("\n")

        # ── Key metrics in a compact row ──
        lines.append("  BPM ", style="dim")
        lines.append(f"{t.bpm or '—'}", style="bold #c8cc6e")
        lines.append("   Key ", style="dim")
        camelot = to_camelot(t.key)
        lines.append(f"{t.key or '—'}", style="bold #a8b060")
        if t.key:
            lines.append(f" {camelot}", style="#7a8a50")
        lines.append("   Dur ", style="dim")
        lines.append(f"{dur_m}:{dur_s:02d}", style="bold")
        if t.bars:
            lines.append("   Bars ", style="dim")
            lines.append(f"{t.bars}", style="bold #c8cc6e")
        lines.append("\n")

        # ── Cue points ──
        if t.cue_in is not None and t.cue_out is not None:
            ci_m, ci_s = divmod(int(t.cue_in), 60)
            co_m, co_s = divmod(int(t.cue_out), 60)
            play_dur = t.cue_out - t.cue_in
            pd_m, pd_s = divmod(int(play_dur), 60)
            lines.append("  Cue ", style="dim")
            lines.append(f"{ci_m}:{ci_s:02d}", style="#7a8a50")
            lines.append(" → ", style="dim")
            lines.append(f"{co_m}:{co_s:02d}", style="#7a8a50")
            lines.append(f"  ({pd_m}:{pd_s:02d} playable)", style="dim")
            lines.append("\n")

        # ── Waveform visualization ──
        if t.waveform:
            lines.append("\n")
            # Waveform bar with playback position
            wf_text = _render_waveform(
                t.waveform, wf_width,
                t.cue_in, t.cue_out, t.duration,
                playback_progress=self.playback_progress,
            )
            lines.append("  ")
            lines.append_text(wf_text)
            lines.append("\n")

            # Beat grid ticks below waveform
            if t.beats and t.duration:
                lines.append("  ")
                beat_text = _render_beat_grid(t.beats, wf_width, t.duration)
                lines.append_text(beat_text)
                lines.append("\n")

            # Time ruler below
            lines.append("  ")
            ruler = _render_time_ruler(t.duration, wf_width, t.cue_in, t.cue_out)
            lines.append_text(ruler)
            lines.append("\n")

            # Section labels based on energy profile
            if t.energy and len(t.energy) > 8:
                lines.append("  ")
                sections = _render_sections(t.energy, wf_width)
                lines.append_text(sections)
                lines.append("\n")

        elif t.energy:
            lines.append("\n  Energy  ", style="dim")
            blocks = "".join(_energy_char(e) for e in t.energy)
            lines.append(blocks, style="#c8a848")
            lines.append("\n")

        # ── Stems ──
        if t.stems:
            lines.append("\n  Stems  ", style="dim")
            for stem in t.stems:
                lines.append(f" {stem}", style="bold #c8a848")
            lines.append("\n")

        # ── File path ──
        lines.append("\n  ", style="")
        lines.append(f"{t.path}", style="dim italic")

        return Panel(lines, title="Track Info", border_style="#7a8a50")


# ── Waveform rendering ──────────────────────────────────────

_BLOCKS = " ▁▂▃▄▅▆▇█"


def _render_waveform(waveform: list[float], width: int,
                     cue_in: float | None = None,
                     cue_out: float | None = None,
                     duration: float = 0,
                     playback_progress: float | None = None) -> Text:
    """Render waveform with amplitude coloring, cue dimming, and playback cursor."""
    if not waveform:
        return Text("")

    resampled = _resample(waveform, width)

    # Normalize
    peak = max(resampled) if resampled else 1.0
    if peak > 0:
        resampled = [v / peak for v in resampled]

    cue_in_col = int(cue_in / duration * width) if cue_in and duration else 0
    cue_out_col = int(cue_out / duration * width) if cue_out and duration else width

    # Playback cursor position
    play_col = int(playback_progress * width) if playback_progress is not None else -1

    text = Text()
    for i, val in enumerate(resampled):
        idx = min(int(val * (len(_BLOCKS) - 1)), len(_BLOCKS) - 1)
        char = _BLOCKS[idx]

        # Playback cursor: bright white column
        if i == play_col:
            text.append(char, style="bold white on #555555")
            continue

        if i < cue_in_col or i >= cue_out_col:
            color = "#4a4d4a"
        elif val < 0.35:
            color = "#7a8a50"
        elif val < 0.65:
            color = "#a8b060"
        elif val < 0.85:
            color = "#c8a848"
        else:
            color = "#c87848"

        # Dim past the playback position
        if play_col >= 0 and i < play_col:
            color = "#5a5d5a"

        text.append(char, style=color)

    return text


def _render_beat_grid(beats: list[float], width: int, duration: float) -> Text:
    """Render beat positions as tick marks. Bar starts (every 4th) are brighter."""
    grid = [" "] * width
    for i, beat in enumerate(beats):
        col = int(beat / duration * width) if duration else 0
        if 0 <= col < width:
            if i % 16 == 0:
                grid[col] = "┃"  # Phrase boundary (every 16 beats = 4 bars)
            elif i % 4 == 0:
                grid[col] = "│"  # Bar boundary
            elif grid[col] == " ":
                grid[col] = "·"  # Beat

    text = Text()
    for ch in grid:
        if ch == "┃":
            text.append(ch, style="bold #c8cc6e")
        elif ch == "│":
            text.append(ch, style="#7a8a50")
        elif ch == "·":
            text.append(ch, style="#4a4d4a")
        else:
            text.append(ch, style="")
    return text


def _render_time_ruler(duration: float, width: int,
                       cue_in: float | None = None,
                       cue_out: float | None = None) -> Text:
    """Render time markers below the waveform."""
    text = Text()
    markers = min(8, max(4, width // 12))
    step = duration / markers if duration else 1.0

    for i in range(markers + 1):
        t = step * i
        m, s = divmod(int(t), 60)
        label = f"{m}:{s:02d}"
        col = int(i * width / markers) if markers else 0

        # Don't overflow past waveform width
        if col + len(label) > width:
            break

        # Position the label
        padding = col - text.cell_len
        if padding > 0:
            text.append(" " * padding)

        # Highlight cue in/out positions
        style = "dim"
        if cue_in and abs(t - cue_in) < step * 0.3:
            style = "bold #7a8a50"
        elif cue_out and abs(t - cue_out) < step * 0.3:
            style = "bold #7a8a50"

        text.append(label, style=style)

    return text


def _render_sections(energy: list[float], width: int) -> Text:
    """Detect and label track sections based on energy profile."""
    n = len(energy)
    num_sections = min(8, max(3, width // 15))
    section_size = n // num_sections

    sections = []
    for i in range(num_sections):
        start = i * section_size
        end = min(start + section_size, n)
        chunk = energy[start:end]
        avg = sum(chunk) / len(chunk) if chunk else 0
        sections.append(avg)

    # Label sections by energy character
    labels = []
    for i, avg in enumerate(sections):
        prev_avg = sections[i - 1] if i > 0 else avg
        if avg < 0.25:
            labels.append(("intro" if i == 0 else "break", "#7a8a50"))
        elif avg < 0.45:
            if avg > prev_avg + 0.08:
                labels.append(("build", "#c8a848"))
            else:
                labels.append(("verse", "#a8b060"))
        elif avg < 0.7:
            if avg > prev_avg + 0.08:
                labels.append(("build", "#c8a848"))
            else:
                labels.append(("chorus", "#c8cc6e"))
        else:
            labels.append(("drop", "#c87848"))

    # Last section heuristic
    if len(labels) > 1 and sections[-1] < 0.3:
        labels[-1] = ("outro", "#7a8a50")

    text = Text()
    col_width = width // num_sections
    for label, color in labels:
        text.append(label.center(col_width), style=f"dim {color}")
    return text


def _resample(data: list[float], width: int) -> list[float]:
    """Resample data to target width using max pooling."""
    n = len(data)
    result = []
    for i in range(width):
        src_start = int(i * n / width)
        src_end = max(src_start + 1, int((i + 1) * n / width))
        chunk = data[src_start:src_end]
        result.append(max(chunk) if chunk else 0.0)
    return result


def _energy_char(val: float) -> str:
    bars = " _.-:=+*#@"
    idx = min(int(val * (len(bars) - 1)), len(bars) - 1)
    return bars[idx]
