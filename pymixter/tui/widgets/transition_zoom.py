"""Transition zoom view — detailed visualization of the overlap zone between two tracks."""

from textual.widgets import Static
from rich.panel import Panel
from rich.text import Text

from pymixter.core.project import Project, Track, Transition, to_camelot


_BLOCKS = " ▁▂▃▄▅▆▇█"


class TransitionZoom(Static):
    """Shows a zoomed-in view of the transition zone between two tracks."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._project: Project | None = None
        self._position: int | None = None  # timeline position of from_track

    def show_transition(self, project: Project, position: int):
        self._project = project
        self._position = position
        self.refresh(layout=True)

    def clear_zoom(self):
        self._project = None
        self._position = None
        self.refresh(layout=True)

    def render(self):
        if not self._project or self._position is None:
            return Panel("No transition selected. Use :zoom <pos>", title="Transition Zoom")

        proj = self._project
        pos = self._position
        if pos < 0 or pos >= len(proj.timeline) - 1:
            return Panel("Invalid position", title="Transition Zoom")

        idx_a = proj.timeline[pos]
        idx_b = proj.timeline[pos + 1]
        if idx_a >= len(proj.library) or idx_b >= len(proj.library):
            return Panel("Track not found", title="Transition Zoom")

        track_a = proj.library[idx_a]
        track_b = proj.library[idx_b]

        # Find transition details
        tr = proj.get_transition(pos)

        width = max(40, self.size.width - 6) if self.size.width > 10 else 60
        wf_width = width - 4  # subtract "  A " prefix

        lines = Text()

        # ── Header: Track A → Track B ──
        lines.append(f"  {track_a.title}", style="bold #a8b060")
        lines.append("  →  ", style="dim")
        lines.append(f"{track_b.title}", style="bold #c8cc6e")
        lines.append("\n")

        # ── BPM / Key comparison ──
        bpm_a = f"{track_a.bpm:g}" if track_a.bpm else "?"
        bpm_b = f"{track_b.bpm:g}" if track_b.bpm else "?"
        key_a = f"{track_a.key or '?'} {to_camelot(track_a.key)}"
        key_b = f"{track_b.key or '?'} {to_camelot(track_b.key)}"

        bpm_diff = abs(track_a.bpm - track_b.bpm) if track_a.bpm and track_b.bpm else 0
        bpm_color = "#a8b060" if bpm_diff <= 1 else "#c8a848" if bpm_diff <= 5 else "#c87848"

        lines.append(f"  BPM {bpm_a}", style="bold")
        lines.append(f" → {bpm_b}", style=f"bold {bpm_color}")
        if bpm_diff > 0:
            lines.append(f"  (Δ{bpm_diff:.1f})", style="dim")
        lines.append(f"    Key {key_a}", style="bold")
        lines.append(f" → {key_b}", style="bold")
        lines.append("\n")

        # ── Transition info ──
        if tr:
            sym = {
                "crossfade": "╲╱ Crossfade",
                "eq_fade": "≋≋ EQ Fade",
                "cut": "┃┃ Cut",
                "echo_out": "»» Echo Out",
                "filter_sweep": "∿∿ Filter Sweep",
            }.get(tr.type, tr.type)
            lines.append(f"  {sym}", style="#c8a848")
            lines.append(f"  {tr.length_bars} bars", style="bold")
            if tr.offset_beats:
                direction = "later" if tr.offset_beats > 0 else "earlier"
                lines.append(f"  offset {abs(tr.offset_beats)}b {direction}", style="dim")
            lines.append("\n")
        else:
            lines.append("  No transition set — use :transition edit ", style="dim")
            lines.append(f"{pos}", style="bold dim")
            lines.append("\n")

        # ── Shared time axis ──
        # The view covers zoom_sec of shared "mix time".
        # A's cue_out aligns to B's cue_in at the transition point.
        # We show context before and after the transition point.
        lines.append("\n")

        zoom_sec = _transition_zone_seconds(track_a, track_b, tr)
        tr_sec = 0.0
        offset_sec = 0.0
        if tr and track_a.bpm:
            sec_per_beat = 60.0 / track_a.bpm
            tr_sec = tr.length_bars * 4 * sec_per_beat
            offset_sec = tr.offset_beats * sec_per_beat

        # Transition point (where A ends / B enters) is at the center of the view
        # A plays from [transition_point - zoom_sec/2 .. transition_point]
        # B plays from [transition_point - tr_sec .. transition_point + zoom_sec/2]
        a_end = track_a.cue_out or track_a.duration
        b_start = track_b.cue_in or 0

        # Map: column position in the view → seconds relative to transition point
        # Left edge = -zoom_sec/2, right edge = +zoom_sec/2
        a_zoom_start = a_end - zoom_sec / 2
        a_zoom_end = a_end + zoom_sec / 2
        b_zoom_start = b_start - tr_sec + offset_sec - (zoom_sec / 2 - tr_sec + offset_sec)
        b_zoom_end = b_zoom_start + zoom_sec

        # Track A waveform
        lines.append("  A ", style="dim #a8b060")
        wf_a = _render_zoom_waveform(
            track_a, wf_width, zoom_start=a_zoom_start,
            zoom_end=a_zoom_end,
            color="#a8b060", fade_out=tr is not None,
        )
        lines.append_text(wf_a)
        lines.append("\n")

        # Track A beat grid
        lines.append("  A ", style="dim #a8b060")
        grid_a = _render_zoom_beats(
            track_a, wf_width, zoom_start=a_zoom_start, zoom_end=a_zoom_end,
        )
        lines.append_text(grid_a)
        lines.append("\n")

        # Overlap zone indicator on shared axis
        if tr and tr_sec > 0:
            lines.append("    ")
            overlap = _render_overlap_bar(wf_width, zoom_sec, tr_sec, offset_sec)
            lines.append_text(overlap)
            lines.append("\n")

        # Track B beat grid (same time axis as A)
        lines.append("  B ", style="dim #c8cc6e")
        grid_b = _render_zoom_beats(
            track_b, wf_width, zoom_start=b_zoom_start, zoom_end=b_zoom_end,
        )
        lines.append_text(grid_b)
        lines.append("\n")

        # Track B waveform
        lines.append("  B ", style="dim #c8cc6e")
        wf_b = _render_zoom_waveform(
            track_b, wf_width, zoom_start=b_zoom_start,
            zoom_end=b_zoom_end,
            color="#c8cc6e", fade_in=tr is not None,
        )
        lines.append_text(wf_b)
        lines.append("\n")

        # Time ruler (relative to transition point)
        lines.append("\n    ")
        ruler = _render_zoom_ruler(zoom_sec, wf_width)
        lines.append_text(ruler)
        lines.append("\n")

        title = f"Transition Zoom [{pos}→{pos+1}]"
        return Panel(lines, title=title, border_style="#c8a848")


def _transition_zone_seconds(track_a: Track, track_b: Track,
                              tr: Transition | None) -> float:
    """Calculate how many seconds to show in the zoom view."""
    if tr and track_a.bpm:
        # Show transition length + 4 extra bars of context
        beats = (tr.length_bars + 4) * 4
        sec_per_beat = 60.0 / track_a.bpm
        return beats * sec_per_beat
    # Default: 30 seconds
    return 30.0


def _render_zoom_waveform(track: Track, width: int,
                           zoom_start: float, zoom_end: float,
                           color: str, fade_in: bool = False,
                           fade_out: bool = False) -> Text:
    """Render waveform for a specific time range."""
    text = Text()
    if not track.waveform or not track.duration:
        text.append("░" * width, style=f"dim {color}")
        return text

    wf = track.waveform
    n = len(wf)
    duration = track.duration

    # Map zoom range to waveform indices
    start_idx = int(zoom_start / duration * n)
    end_idx = int(zoom_end / duration * n)
    start_idx = max(0, min(start_idx, n - 1))
    end_idx = max(start_idx + 1, min(end_idx, n))

    segment = wf[start_idx:end_idx]
    if not segment:
        text.append("░" * width, style=f"dim {color}")
        return text

    # Resample segment to width
    resampled = _resample(segment, width)
    peak = max(resampled) if resampled else 1.0
    if peak <= 0:
        peak = 1.0
    resampled = [v / peak for v in resampled]

    for i, val in enumerate(resampled):
        idx = min(int(val * (len(_BLOCKS) - 1)), len(_BLOCKS) - 1)
        char = _BLOCKS[idx]

        # Apply fade envelope
        fade = 1.0
        if fade_out and width > 0:
            fade = 1.0 - (i / width)
        elif fade_in and width > 0:
            fade = i / width

        if val < 0.35:
            c = color
        elif val < 0.65:
            c = "#c8a848" if fade > 0.5 else color
        elif val < 0.85:
            c = "#c8a848"
        else:
            c = "#c87848"

        # Dim faded regions
        if fade < 0.3:
            c = "#4a4d4a"

        text.append(char, style=c)

    return text


def _render_zoom_beats(track: Track, width: int,
                        zoom_start: float, zoom_end: float) -> Text:
    """Render beat grid for a specific time range."""
    text = Text()
    if not track.beats:
        text.append(" " * width)
        return text

    zoom_dur = zoom_end - zoom_start
    if zoom_dur <= 0:
        text.append(" " * width)
        return text

    grid = [" "] * width
    for i, beat in enumerate(track.beats):
        if beat < zoom_start or beat >= zoom_end:
            continue
        col = int((beat - zoom_start) / zoom_dur * width)
        if 0 <= col < width:
            if i % 16 == 0:
                grid[col] = "┃"
            elif i % 4 == 0:
                grid[col] = "│"
            elif grid[col] == " ":
                grid[col] = "·"

    for ch in grid:
        if ch == "┃":
            text.append(ch, style="bold #c8cc6e")
        elif ch == "│":
            text.append(ch, style="#7a8a50")
        elif ch == "·":
            text.append(ch, style="#4a4d4a")
        else:
            text.append(ch)
    return text


def _render_zoom_ruler(zoom_sec: float, width: int) -> Text:
    """Render time markers for the zoom region."""
    text = Text()
    markers = min(6, max(3, width // 10))
    step = zoom_sec / markers

    for i in range(markers + 1):
        t = step * i
        if t < 60:
            label = f"{t:.1f}s"
        else:
            m, s = divmod(int(t), 60)
            label = f"{m}:{s:02d}"
        col = int(i * width / markers) if markers else 0
        if col + len(label) > width:
            break
        padding = col - text.cell_len
        if padding > 0:
            text.append(" " * padding)
        text.append(label, style="dim")

    return text


def _render_overlap_bar(width: int, zoom_sec: float,
                        tr_sec: float, offset_sec: float) -> Text:
    """Show the overlap zone on the shared time axis.

    The transition point is at the center of the view.
    Overlap runs from (center - tr_sec + offset) to center.
    """
    text = Text()
    center = zoom_sec / 2
    overlap_start_sec = center - tr_sec + offset_sec
    overlap_end_sec = center

    col_start = max(0, int(overlap_start_sec / zoom_sec * width))
    col_end = min(width, int(overlap_end_sec / zoom_sec * width))

    for i in range(width):
        if i == col_start:
            text.append("╠", style="bold #c8a848")
        elif i == col_end - 1:
            text.append("╣", style="bold #c8a848")
        elif col_start < i < col_end - 1:
            text.append("═", style="#c8a848")
        elif i == width // 2:
            text.append("┊", style="dim #c8a848")  # transition point marker
        else:
            text.append("─", style="#4a4d4a")

    text.append(f"  {tr_sec:.0f}s overlap", style="dim")
    return text


def _resample(data: list[float], width: int) -> list[float]:
    """Resample data to target width using max pooling."""
    n = len(data)
    if n == 0 or width == 0:
        return []
    result = []
    for i in range(width):
        src_start = int(i * n / width)
        src_end = max(src_start + 1, int((i + 1) * n / width))
        chunk = data[src_start:src_end]
        result.append(max(chunk) if chunk else 0.0)
    return result
