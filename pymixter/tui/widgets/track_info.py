"""Track info panel — shows details of the selected track."""

from textual.widgets import Static
from rich.panel import Panel
from rich.text import Text

from pymixter.core.project import Track


class TrackInfo(Static):
    """Displays details about the currently selected track."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._track: Track | None = None

    def show_track(self, track: Track):
        self._track = track
        self.refresh(layout=True)

    def render(self):
        if not self._track:
            return Panel("No track selected", title="Track Info")

        t = self._track
        dur_m, dur_s = divmod(int(t.duration), 60) if t.duration else (0, 0)

        lines = Text()
        lines.append("Title:    ", style="dim")
        lines.append(f"{t.title}\n", style="bold")
        lines.append("BPM:      ", style="dim")
        lines.append(f"{t.bpm or '—'}\n", style="bold #c8cc6e")
        lines.append("Key:      ", style="dim")
        lines.append(f"{t.key or '—'}\n", style="bold #a8b060")
        lines.append("Duration: ", style="dim")
        lines.append(f"{dur_m}:{dur_s:02d}\n", style="bold")

        if t.bars:
            lines.append("Bars:     ", style="dim")
            lines.append(f"{t.bars}\n", style="bold #c8cc6e")

        if t.cue_in is not None and t.cue_out is not None:
            ci_m, ci_s = divmod(int(t.cue_in), 60)
            co_m, co_s = divmod(int(t.cue_out), 60)
            lines.append("Cue:      ", style="dim")
            lines.append(f"{ci_m}:{ci_s:02d} → {co_m}:{co_s:02d}\n",
                         style="bold #7a8a50")

        if t.waveform:
            lines.append("\n")
            wf_width = 60
            wf_text = _render_waveform(t.waveform, wf_width, t.cue_in, t.cue_out, t.duration)
            lines.append_text(wf_text)
            lines.append("\n\n")
        elif t.energy:
            lines.append("Energy:   ", style="dim")
            blocks = "".join(_energy_char(e) for e in t.energy)
            lines.append(blocks + "\n", style="#c8a848")

        lines.append("Path:     ", style="dim")
        lines.append(f"{t.path}\n", style="italic")

        if t.stems:
            lines.append("Stems:    ", style="dim")
            lines.append(" ".join(f"+{s}" for s in t.stems), style="bold #c8a848")

        return Panel(lines, title="Track Info")


def _energy_char(val: float) -> str:
    """Map 0–1 energy to a unicode bar character."""
    bars = " _.-:=+*#@"
    idx = min(int(val * (len(bars) - 1)), len(bars) - 1)
    return bars[idx]


_BLOCKS = " \u2581\u2582\u2583\u2584\u2585\u2586\u2587\u2588"


def _render_waveform(waveform: list[float], width: int,
                     cue_in: float | None = None,
                     cue_out: float | None = None,
                     duration: float = 0) -> Text:
    """Render waveform as unicode block characters with color gradient."""
    if not waveform:
        return Text("")

    # Resample to width
    n = len(waveform)
    resampled = []
    for i in range(width):
        src_start = int(i * n / width)
        src_end = max(src_start + 1, int((i + 1) * n / width))
        chunk = waveform[src_start:src_end]
        resampled.append(max(chunk) if chunk else 0.0)

    # Normalize
    peak = max(resampled) if resampled else 1.0
    if peak > 0:
        resampled = [v / peak for v in resampled]

    # Cue positions mapped to width
    cue_in_col = int(cue_in / duration * width) if cue_in and duration else 0
    cue_out_col = int(cue_out / duration * width) if cue_out and duration else width

    text = Text()
    for i, val in enumerate(resampled):
        idx = min(int(val * (len(_BLOCKS) - 1)), len(_BLOCKS) - 1)
        char = _BLOCKS[idx]

        # Color by amplitude: green < yellow < red
        if val < 0.4:
            color = "#7a8a50"
        elif val < 0.7:
            color = "#c8a848"
        else:
            color = "#c87848"

        # Dim outside cue region
        if i < cue_in_col or i >= cue_out_col:
            color = "#4a4d4a"

        text.append(char, style=color)

    return text
