"""Mix renderer — renders timeline transitions into continuous audio.

Takes a Project with timeline + transitions and produces a mixed audio
stream, either as a numpy array or written to a WAV file.
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
from pedalboard import (
    Pedalboard, Gain, Delay, Reverb, Compressor,
    HighpassFilter, LowpassFilter, LadderFilter, PitchShift,
    time_stretch,
)
from pedalboard.io import AudioFile

from pymixter.core.project import Project, Track, Transition, key_semitone_distance

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

    # Apply ReplayGain normalization (loudness matching between tracks)
    if track.replay_gain is not None:
        gain_linear = 10.0 ** (track.replay_gain / 20.0)
        audio = audio * gain_linear

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


def _snap_to_beat(frame: int, beats: list[float], sr: int,
                  direction: str = "before") -> int:
    """Snap a frame position to the nearest beat boundary.

    Args:
        frame: position in sample frames
        beats: list of beat positions in seconds
        sr: sample rate
        direction: "before" snaps to beat at or before, "nearest" to closest
    """
    if not beats:
        return frame
    t = frame / sr
    if direction == "before":
        candidates = [b for b in beats if b <= t + 0.01]
        if candidates:
            return int(candidates[-1] * sr)
        return frame
    else:
        best = min(beats, key=lambda b: abs(b - t))
        return int(best * sr)


def _tempo_match(audio: np.ndarray, source_bpm: float, target_bpm: float,
                 sr: int) -> np.ndarray:
    """Time-stretch audio to match target BPM.

    Returns stretched audio (channels, frames). Clamps ratio to 0.5x–2.0x.
    """
    if not source_bpm or not target_bpm:
        return audio
    ratio = source_bpm / target_bpm
    ratio = max(0.5, min(2.0, ratio))
    if abs(ratio - 1.0) < 0.01:
        return audio
    return time_stretch(audio, sr, stretch_factor=ratio)


def _key_match(audio: np.ndarray, semitones: int, sr: int) -> np.ndarray:
    """Pitch-shift audio by N semitones for harmonic key matching.

    Uses pedalboard PitchShift — preserves tempo while changing pitch.
    Clamps to ±6 semitones (half an octave).
    """
    if semitones == 0:
        return audio
    semitones = max(-6, min(6, semitones))
    board = Pedalboard([PitchShift(semitones=float(semitones))])
    return board(audio, sr)


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


def render_filter_sweep(a_tail: np.ndarray, b_head: np.ndarray,
                        sr: int) -> np.ndarray:
    """Filter sweep transition — classic DJ low-pass filter close on A,
    high-pass filter open on B, with resonance peak.

    Uses LadderFilter for analog-style resonant sweep.
    """
    length = min(a_tail.shape[1], b_head.shape[1])
    a = a_tail[:, :length].copy()
    b = b_head[:, :length].copy()

    n_steps = 16
    chunk_size = length // n_steps
    result = np.zeros_like(a[:, :length])

    for i in range(n_steps):
        start = i * chunk_size
        end = start + chunk_size if i < n_steps - 1 else length
        t = i / (n_steps - 1)  # 0.0 → 1.0

        # A: lowpass sweep down 20kHz → 200Hz with resonance
        cutoff_a = 20000.0 * (1.0 - t * 0.99)
        board_a = Pedalboard([
            LadderFilter(
                mode=LadderFilter.Mode.LPF24,
                cutoff_hz=cutoff_a,
                resonance=0.3 + t * 0.4,  # resonance rises as filter closes
            ),
            Gain(gain_db=-t * 3),
        ])
        a_chunk = board_a(a[:, start:end], sr)

        # B: highpass sweep down 8kHz → 20Hz
        cutoff_b = 8000.0 * (1.0 - t) + 20.0
        board_b = Pedalboard([
            LadderFilter(
                mode=LadderFilter.Mode.HPF24,
                cutoff_hz=cutoff_b,
                resonance=0.3 + (1.0 - t) * 0.4,
            ),
            Gain(gain_db=-(1.0 - t) * 3),
        ])
        b_chunk = board_b(b[:, start:end], sr)

        result[:, start:end] = a_chunk + b_chunk

    return result


# Dispatch table for transition renderers
TRANSITION_RENDERERS = {
    "crossfade": render_crossfade,
    "eq_fade": render_eq_fade,
    "cut": render_cut,
    "echo_out": render_echo_out,
    "filter_sweep": render_filter_sweep,
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

            # Beat-align: snap solo_end to nearest beat boundary
            if tr.beat_aligned and track.beats:
                solo_end = _snap_to_beat(solo_end, track.beats, sr, "before")
                overlap_frames = audio.shape[1] - solo_end

            if solo_end > solo_start:
                segments.append(audio[:, solo_start:solo_end])

            # Render transition
            a_tail = audio[:, solo_end:]
            next_audio, _ = tracks_audio[pos + 1]
            b_head = next_audio[:, :overlap_frames]

            # Tempo sync: time-stretch b_head to match track A's BPM
            if tr.tempo_sync and track.bpm and next_track.bpm and tr.type != "cut":
                b_head = _tempo_match(b_head, next_track.bpm, track.bpm, sr)
                # Re-align lengths after stretch
                min_len = min(a_tail.shape[1], b_head.shape[1])
                a_tail = a_tail[:, :min_len]
                b_head = b_head[:, :min_len]

            # Key matching: pitch-shift b_head if keys clash
            shift = key_semitone_distance(track.key, next_track.key)
            if shift and tr.type not in ("cut", "echo_out"):
                b_head = _key_match(b_head, shift, sr)

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

    # Master chain: gentle compression + limiter for cohesive loudness
    master = Pedalboard([
        Compressor(threshold_db=-12.0, ratio=3.0,
                   attack_ms=10.0, release_ms=100.0),
        Gain(gain_db=2.0),  # makeup gain
    ])
    result = master(result, sr)

    return result, sr


def render_to_file(project: Project, output_path: str,
                   on_progress: callable | None = None,
                   quality: str | None = None) -> str:
    """Render timeline to an audio file (WAV, MP3, or FLAC).

    Format is inferred from file extension. Returns the output file path.
    """
    audio, sr = render_timeline(project, on_progress=on_progress)

    if audio.shape[1] == 0:
        raise ValueError("Nothing to render — timeline is empty")

    out = Path(output_path)
    ext = out.suffix.lower()

    # Build kwargs for AudioFile writer
    write_kwargs = {"samplerate": sr, "num_channels": audio.shape[0]}
    if ext == ".mp3" and quality:
        write_kwargs["quality"] = quality

    with AudioFile(str(out), "w", **write_kwargs) as f:
        # Write in chunks to avoid memory issues with large mixes
        chunk_size = sr * 10  # 10 seconds
        for i in range(0, audio.shape[1], chunk_size):
            f.write(audio[:, i:i + chunk_size])

    duration = audio.shape[1] / sr
    log.info("Rendered %.1fs to %s (%s)", duration, out, ext)
    return str(out)


def render_transition_preview(project: Project, pos: int,
                              context_seconds: float = 10.0,
                              ) -> tuple[np.ndarray, int]:
    """Render just the transition zone between timeline[pos] and timeline[pos+1].

    Includes context_seconds of solo audio on each side of the transition
    so the DJ can hear the approach and exit.

    Returns (audio, sample_rate).
    """
    if pos < 0 or pos >= len(project.timeline) - 1:
        raise IndexError(f"No transition at position {pos}")

    tr_lookup = {tr.from_track: tr for tr in project.transitions}
    tr = tr_lookup.get(pos)

    track_a = project.library[project.timeline[pos]]
    track_b = project.library[project.timeline[pos + 1]]

    audio_a, sr_a = _load_track_audio(track_a)
    audio_b, sr_b = _load_track_audio(track_b)
    sr = sr_a

    if tr is None:
        # No transition defined — just play last N seconds of A + first N of B
        ctx_frames = int(context_seconds * sr)
        tail = audio_a[:, max(0, audio_a.shape[1] - ctx_frames):]
        head = audio_b[:, :min(ctx_frames, audio_b.shape[1])]
        return np.concatenate([tail, head], axis=1), sr

    overlap_frames = _transition_frames(tr, track_a, sr)
    overlap_frames = min(overlap_frames, audio_a.shape[1], audio_b.shape[1])

    solo_end = audio_a.shape[1] - overlap_frames
    if tr.beat_aligned and track_a.beats:
        solo_end = _snap_to_beat(solo_end, track_a.beats, sr, "before")
        overlap_frames = audio_a.shape[1] - solo_end

    a_tail = audio_a[:, solo_end:]
    b_head = audio_b[:, :overlap_frames]

    if tr.tempo_sync and track_a.bpm and track_b.bpm and tr.type != "cut":
        b_head = _tempo_match(b_head, track_b.bpm, track_a.bpm, sr)
        min_len = min(a_tail.shape[1], b_head.shape[1])
        a_tail = a_tail[:, :min_len]
        b_head = b_head[:, :min_len]

    # Key matching: pitch-shift b_head if keys clash
    shift = key_semitone_distance(track_a.key, track_b.key)
    if shift and tr.type not in ("cut", "echo_out"):
        b_head = _key_match(b_head, shift, sr)

    renderer = TRANSITION_RENDERERS.get(tr.type, render_crossfade)
    transition_audio = renderer(a_tail, b_head, sr)

    # Add context before and after the transition
    ctx_frames = int(context_seconds * sr)
    pre_start = max(0, solo_end - ctx_frames)
    pre_context = audio_a[:, pre_start:solo_end]

    post_end = min(overlap_frames + ctx_frames, audio_b.shape[1])
    post_context = audio_b[:, overlap_frames:post_end]

    parts = [pre_context, transition_audio, post_context]
    return np.concatenate(parts, axis=1), sr


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
