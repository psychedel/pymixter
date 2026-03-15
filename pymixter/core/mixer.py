"""Mix renderer — renders timeline transitions into continuous audio.

Takes a Project with timeline + transitions and produces a mixed audio
stream, either as a numpy array or written to a WAV file.
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
from pedalboard import (
    Pedalboard, Gain, Delay, Reverb,
    HighpassFilter, LowpassFilter, LowShelfFilter,
)
from pedalboard.io import AudioFile, WriteableAudioFile

from pymixter.core.project import Project, Track, Transition

log = logging.getLogger(__name__)

# Target sample rate for mixing
MIX_SR = 44100
MIX_CHANNELS = 2


def _load_track_audio(track: Track) -> tuple[np.ndarray, int]:
    """Load track audio, trimmed to cue points.

    Returns (audio, sr) where audio is (channels, frames).
    """
    with AudioFile(track.path) as f:
        sr = f.samplerate

        # Determine playable region
        start_frame = int((track.cue_in or 0) * sr)
        end_frame = int((track.cue_out or track.duration or f.frames / sr) * sr)
        end_frame = min(end_frame, f.frames)

        if start_frame > 0:
            f.seek(start_frame)

        frames_to_read = end_frame - start_frame
        audio = f.read(frames_to_read)

    # Ensure stereo
    if audio.shape[0] == 1:
        audio = np.vstack([audio, audio])

    return audio, sr


def _transition_frames(transition: Transition, track_a: Track, sr: int) -> int:
    """Calculate transition length in frames from bars + BPM."""
    bpm = track_a.bpm or 120.0
    beats = transition.length_bars * 4
    seconds = beats / bpm * 60.0
    return int(seconds * sr)


def _make_fade(length: int, direction: str = "in") -> np.ndarray:
    """Create a linear fade curve (0→1 for 'in', 1→0 for 'out')."""
    fade = np.linspace(0.0, 1.0, length, dtype=np.float32)
    if direction == "out":
        fade = 1.0 - fade
    return fade


def render_crossfade(a_tail: np.ndarray, b_head: np.ndarray,
                     sr: int) -> np.ndarray:
    """Simple linear crossfade between two overlapping segments."""
    length = min(a_tail.shape[1], b_head.shape[1])
    a = a_tail[:, :length].copy()
    b = b_head[:, :length].copy()

    fade = _make_fade(length, "in")  # 0→1
    a *= (1.0 - fade)
    b *= fade
    return a + b


def render_eq_fade(a_tail: np.ndarray, b_head: np.ndarray,
                   sr: int) -> np.ndarray:
    """EQ fade (bass swap) — gradually cut bass on A, bring in bass on B."""
    length = min(a_tail.shape[1], b_head.shape[1])
    a = a_tail[:, :length].copy()
    b = b_head[:, :length].copy()

    # Process in chunks — gradually increase highpass on A, decrease on B
    n_steps = 8
    chunk_size = length // n_steps
    result = np.zeros_like(a[:, :length])

    for i in range(n_steps):
        start = i * chunk_size
        end = start + chunk_size if i < n_steps - 1 else length
        t = i / (n_steps - 1)  # 0.0 → 1.0

        # A: progressively cut bass (highpass cutoff rises 30→800Hz)
        cutoff_a = 30.0 + t * 770.0
        board_a = Pedalboard([
            HighpassFilter(cutoff_frequency_hz=cutoff_a),
            Gain(gain_db=-t * 6),  # also fade volume
        ])
        a_chunk = board_a(a[:, start:end], sr)

        # B: progressively bring in (lowpass cutoff rises 200→20000Hz)
        cutoff_b = 200.0 + t * 19800.0
        board_b = Pedalboard([
            LowpassFilter(cutoff_frequency_hz=cutoff_b),
            Gain(gain_db=-(1.0 - t) * 6),
        ])
        b_chunk = board_b(b[:, start:end], sr)

        result[:, start:end] = a_chunk + b_chunk

    return result


def render_cut(a_tail: np.ndarray, b_head: np.ndarray,
               sr: int) -> np.ndarray:
    """Hard cut with micro-crossfade to avoid clicks."""
    # 50ms micro-fade
    micro = min(int(0.05 * sr), a_tail.shape[1], b_head.shape[1])
    fade = _make_fade(micro, "in")
    result = np.zeros((2, micro), dtype=np.float32)
    result += a_tail[:, :micro] * (1.0 - fade)
    result += b_head[:, :micro] * fade
    return result


def render_echo_out(a_tail: np.ndarray, b_head: np.ndarray,
                    sr: int) -> np.ndarray:
    """Echo/reverb fadeout on A, crossfade into B."""
    length = min(a_tail.shape[1], b_head.shape[1])
    a = a_tail[:, :length].copy()
    b = b_head[:, :length].copy()

    # Apply delay + reverb to A
    board = Pedalboard([
        Delay(delay_seconds=0.375, feedback=0.4, mix=0.5),
        Reverb(room_size=0.7, wet_level=0.4, dry_level=0.6),
    ])
    a_wet = board(a, sr)

    # Crossfade wet A → B
    fade = _make_fade(length, "in")
    return a_wet * (1.0 - fade) + b * fade


# Dispatch table for transition renderers
TRANSITION_RENDERERS = {
    "crossfade": render_crossfade,
    "eq_fade": render_eq_fade,
    "cut": render_cut,
    "echo_out": render_echo_out,
}


def render_timeline(project: Project,
                    on_progress: callable | None = None) -> tuple[np.ndarray, int]:
    """Render the entire timeline into a single audio array.

    Args:
        project: Project with timeline and transitions
        on_progress: callback(current_track_idx, total_tracks, message)

    Returns:
        (audio, sample_rate) where audio is (channels, frames)
    """
    if not project.timeline:
        return np.zeros((2, 0), dtype=np.float32), MIX_SR

    # Build transition lookup: (timeline_pos) -> Transition
    tr_lookup: dict[int, Transition] = {}
    for tr in project.transitions:
        tr_lookup[tr.from_track] = tr

    # Load all tracks
    tracks_audio: list[tuple[np.ndarray, int]] = []
    for pos, lib_idx in enumerate(project.timeline):
        track = project.library[lib_idx]
        if on_progress:
            on_progress(pos, len(project.timeline), f"Loading {track.title}")
        audio, sr = _load_track_audio(track)
        tracks_audio.append((audio, sr))

    if not tracks_audio:
        return np.zeros((2, 0), dtype=np.float32), MIX_SR

    sr = tracks_audio[0][1]

    # Build the mix by concatenating tracks with overlap zones
    segments: list[np.ndarray] = []
    prev_overlap = 0  # how many frames of the current track are used in the previous transition

    for pos in range(len(tracks_audio)):
        audio, _ = tracks_audio[pos]
        track = project.library[project.timeline[pos]]

        if on_progress:
            on_progress(pos, len(project.timeline), f"Mixing {track.title}")

        # Determine transition INTO this track (from previous)
        # prev_overlap tells us how many frames at the start were consumed by transition
        solo_start = prev_overlap

        # Determine transition OUT of this track (to next)
        tr = tr_lookup.get(pos)
        if tr is not None and pos + 1 < len(tracks_audio):
            next_track = project.library[project.timeline[pos + 1]]
            overlap_frames = _transition_frames(tr, track, sr)
            overlap_frames = min(overlap_frames, audio.shape[1] - solo_start)

            # Solo section: from solo_start to (end - overlap)
            solo_end = audio.shape[1] - overlap_frames
            if solo_end > solo_start:
                segments.append(audio[:, solo_start:solo_end])

            # Render transition
            a_tail = audio[:, solo_end:]
            next_audio, _ = tracks_audio[pos + 1]
            b_head = next_audio[:, :overlap_frames]

            renderer = TRANSITION_RENDERERS.get(tr.type, render_crossfade)
            transition_audio = renderer(a_tail, b_head, sr)
            segments.append(transition_audio)

            prev_overlap = min(overlap_frames, next_audio.shape[1])
        else:
            # Last track or no transition — just append the rest
            if solo_start < audio.shape[1]:
                segments.append(audio[:, solo_start:])
            prev_overlap = 0

    # Concatenate all segments
    if not segments:
        return np.zeros((2, 0), dtype=np.float32), sr

    result = np.concatenate(segments, axis=1)
    return result, sr


def render_to_file(project: Project, output_path: str,
                   on_progress: callable | None = None) -> str:
    """Render timeline to a WAV file.

    Returns the output file path.
    """
    audio, sr = render_timeline(project, on_progress=on_progress)

    if audio.shape[1] == 0:
        raise ValueError("Nothing to render — timeline is empty")

    out = Path(output_path)
    with AudioFile(str(out), "w", samplerate=sr,
                   num_channels=audio.shape[0]) as f:
        f.write(audio)

    duration = audio.shape[1] / sr
    log.info("Rendered %.1fs to %s", duration, out)
    return str(out)


def validate_timeline(project: Project) -> list[str]:
    """Check timeline for issues. Returns list of warning messages."""
    warnings = []

    if not project.timeline:
        warnings.append("Timeline is empty")
        return warnings

    from pymixter.core.project import get_compatible_keys

    for pos in range(len(project.timeline)):
        lib_idx = project.timeline[pos]
        track = project.library[lib_idx]

        if not track.bpm:
            warnings.append(f"[{pos}] {track.title}: no BPM data")
        if not track.key:
            warnings.append(f"[{pos}] {track.title}: no key data")
        if not track.cue_in and not track.cue_out and not track.beats:
            warnings.append(f"[{pos}] {track.title}: not analyzed (no cue points)")

        # Check transition to next track
        if pos + 1 < len(project.timeline):
            next_idx = project.timeline[pos + 1]
            next_track = project.library[next_idx]

            if track.bpm and next_track.bpm:
                bpm_diff = abs(track.bpm - next_track.bpm)
                if bpm_diff > 8:
                    warnings.append(
                        f"[{pos}→{pos+1}] BPM jump: {track.bpm}→{next_track.bpm} "
                        f"(diff {bpm_diff:.1f})"
                    )

            if track.key and next_track.key:
                compatible = get_compatible_keys(track.key)
                if next_track.key not in compatible:
                    warnings.append(
                        f"[{pos}→{pos+1}] Key clash: {track.key}→{next_track.key}"
                    )

            # Check for potential silence gap
            if track.cue_out and track.duration:
                tail_silence = track.duration - track.cue_out
                if tail_silence > 5.0:
                    warnings.append(
                        f"[{pos}] {track.title}: {tail_silence:.0f}s silence after cue_out"
                    )

    return warnings
