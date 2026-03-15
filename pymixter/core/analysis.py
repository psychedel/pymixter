"""Audio analysis: BPM, key detection, beat grid, energy, waveform.

Uses essentia for key detection and BPM (more accurate than librosa chroma),
with librosa as fallback for beat grid, cue points, and energy.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np


class AnalysisError(Exception):
    """Raised when audio analysis fails on a file."""


def analyze_track(path: str, full: bool = False) -> dict:
    """Analyze a track and return BPM, key, duration, and waveform overview.

    With full=True, also computes beat grid, cue points, energy profile,
    loudness (LUFS/ReplayGain), danceability, dynamic complexity, onsets,
    fade detection, and chord progression.
    Returns a dict suitable for passing to Project.add_track(**analysis).

    Raises AnalysisError with a human-readable message if the file cannot
    be loaded or analyzed.
    """
    import essentia.standard as es

    # Load mono for most algorithms
    try:
        audio = es.MonoLoader(filename=path)()
    except RuntimeError as exc:
        raise AnalysisError(f"Cannot load audio file: {exc}") from exc
    except Exception as exc:
        raise AnalysisError(f"Failed to read '{path}': {exc}") from exc

    sr = 44100  # MonoLoader resamples to 44100
    duration = len(audio) / sr

    if duration < 1.0:
        raise AnalysisError(f"File too short ({duration:.1f}s) — need at least 1 second")

    # Key detection
    key_name, scale, _strength = es.KeyExtractor()(audio)
    key = key_name + ("m" if scale == "minor" else "")

    # BPM + beat positions
    bpm_val, beat_positions, *_ = es.RhythmExtractor2013()(audio)
    bpm = round(float(bpm_val), 1)
    beat_times = [round(float(b), 4) for b in beat_positions]

    # Loudness: ReplayGain (works on mono)
    replay_gain = round(float(es.ReplayGain()(audio)), 2)

    # Waveform overview
    waveform = _compute_waveform_overview(audio, n_points=1000)

    result = {
        "title": Path(path).stem,
        "bpm": bpm,
        "key": key,
        "duration": round(duration, 2),
        "waveform": waveform.tolist(),
        "replay_gain": replay_gain,
    }

    if full:
        result["beats"] = beat_times
        result["cue_in"], result["cue_out"] = _detect_cue_points(
            audio, sr, beat_times,
        )
        result["energy"] = _compute_energy_profile(audio, sr)

        # LUFS (needs stereo)
        try:
            audio_stereo = es.AudioLoader(filename=path)()[0]
            _mom, _short, integrated, _loudrange = es.LoudnessEBUR128()(audio_stereo)
            result["lufs"] = round(float(integrated), 1)
        except Exception:
            result["lufs"] = None

        # Danceability
        d_val, _ = es.Danceability()(audio)
        result["danceability"] = round(float(d_val), 3)

        # Dynamic complexity
        complexity, _ = es.DynamicComplexity()(audio)
        result["dynamic_complexity"] = round(float(complexity), 2)

        # Onset positions (convert from sample rate)
        onset_times, _ = es.OnsetRate()(audio)
        result["onsets"] = [round(float(o), 4) for o in onset_times]

        # Fade detection (find macro fade-in/fade-out from energy profile)
        result["fade_in_end"], result["fade_out_start"] = _detect_fades(
            result["energy"], duration,
        )

        # Chord detection
        result["chords"] = _detect_chords(audio, sr, beat_times)

    return result


def analyze_beats(path: str) -> dict:
    """Lightweight analysis: just beats, cue points, energy."""
    import essentia.standard as es

    audio = es.MonoLoader(filename=path)()
    _bpm, beat_positions, *_ = es.RhythmExtractor2013()(audio)
    beat_times = [round(float(b), 4) for b in beat_positions]

    cue_in, cue_out = _detect_cue_points(audio, 44100, beat_times)
    energy = _compute_energy_profile(audio, 44100)

    return {
        "beats": beat_times,
        "cue_in": cue_in,
        "cue_out": cue_out,
        "energy": energy,
    }


# ── Beat grid & cue points ──────────────────────────────────

def _detect_cue_points(y: np.ndarray, sr: int,
                       beat_times: list[float]) -> tuple[float, float]:
    """Find musically meaningful start/end points.

    Cue-in: first beat where RMS exceeds 10% of track peak.
    Cue-out: last beat where RMS exceeds 10% of track peak.
    """
    if not beat_times:
        duration = len(y) / sr
        return 0.0, round(duration, 4)

    # Compute RMS in windows
    hop = 512
    n_frames = len(y) // hop
    rms = np.array([
        np.sqrt(np.mean(y[i * hop:(i + 1) * hop] ** 2))
        for i in range(n_frames)
    ])
    rms_threshold = 0.1 * np.sqrt(np.mean(y ** 2))

    def rms_at(t: float) -> float:
        idx = min(int(t * sr / hop), len(rms) - 1)
        return float(rms[idx])

    # Find first/last beats above threshold
    cue_in = beat_times[0]
    for bt in beat_times:
        if rms_at(bt) > rms_threshold:
            cue_in = bt
            break

    cue_out = beat_times[-1]
    for bt in reversed(beat_times):
        if rms_at(bt) > rms_threshold:
            cue_out = bt
            break

    return round(cue_in, 4), round(cue_out, 4)


# ── Energy profile ───────────────────────────────────────────

def _compute_energy_profile(y: np.ndarray, sr: int,
                            n_segments: int = 64) -> list[float]:
    """Compute RMS energy profile split into n_segments.

    Returns list of normalized (0–1) energy values.
    """
    segment_len = max(1, len(y) // n_segments)
    energies = []
    for i in range(n_segments):
        start = i * segment_len
        end = min(start + segment_len, len(y))
        chunk = y[start:end]
        energies.append(float(np.sqrt(np.mean(chunk ** 2))))

    peak = max(energies) if energies else 1.0
    if peak > 0:
        energies = [e / peak for e in energies]
    return [round(e, 3) for e in energies]


# ── Fade detection ──────────────────────────────────────────

def _detect_fades(energy: list[float], duration: float) -> tuple[float | None, float | None]:
    """Detect macro fade-in/fade-out from energy profile.

    Looks for a sustained rise at the start (fade-in) or sustained
    drop at the end (fade-out) in the energy profile.
    Returns (fade_in_end_sec, fade_out_start_sec) or None if not detected.
    """
    if not energy or len(energy) < 8:
        return None, None

    seg_dur = duration / len(energy)
    threshold = 0.3  # energy must rise above this to end fade-in

    # Fade-in: find first segment where energy exceeds threshold
    # Only count as fade if it starts below threshold
    fade_in_end = None
    if energy[0] < threshold:
        for i, e in enumerate(energy):
            if e >= threshold:
                if i >= 2:  # at least 2 segments of fade
                    fade_in_end = round(i * seg_dur, 2)
                break

    # Fade-out: find last segment where energy drops below threshold
    fade_out_start = None
    if energy[-1] < threshold:
        for i in range(len(energy) - 1, -1, -1):
            if energy[i] >= threshold:
                if (len(energy) - 1 - i) >= 2:  # at least 2 segments of fade
                    fade_out_start = round((i + 1) * seg_dur, 2)
                break

    return fade_in_end, fade_out_start


# ── Chord detection ─────────────────────────────────────────

def _detect_chords(audio: np.ndarray, sr: int,
                   beat_times: list[float]) -> list[tuple[float, str]]:
    """Detect chords at beat positions using essentia ChordsDetection."""
    import essentia.standard as es

    if not beat_times or len(beat_times) < 2:
        return []

    # Compute HPCP (Harmonic Pitch Class Profile) frame by frame
    frame_size = 4096
    hop_size = 2048
    w = es.Windowing(type='blackmanharris62')
    spectrum = es.Spectrum()
    peaks = es.SpectralPeaks(orderBy='magnitude', magnitudeThreshold=0.00001,
                              minFrequency=20, maxFrequency=3500)
    hpcp = es.HPCP()

    hpcp_frames = []
    for frame in es.FrameGenerator(audio, frameSize=frame_size, hopSize=hop_size):
        windowed = w(frame)
        spec = spectrum(windowed)
        freqs, mags = peaks(spec)
        h = hpcp(freqs, mags)
        hpcp_frames.append(h)

    if not hpcp_frames:
        return []

    hpcp_array = np.array(hpcp_frames)

    # ChordsDetection on HPCP
    chords_det = es.ChordsDetection(hopSize=hop_size, sampleRate=sr)
    chord_labels, chord_strengths = chords_det(hpcp_array)

    # Sample chords at beat positions (deduplicate consecutive same chords)
    frame_dur = hop_size / sr
    result = []
    prev_chord = None
    for bt in beat_times[::4]:  # sample every 4 beats (per bar)
        frame_idx = min(int(bt / frame_dur), len(chord_labels) - 1)
        chord = chord_labels[frame_idx]
        if chord != prev_chord and chord != "N":  # skip "N" (no chord)
            result.append((round(bt, 2), chord))
            prev_chord = chord

    return result


# ── Waveform ─────────────────────────────────────────────────

def _compute_waveform_overview(y: np.ndarray, n_points: int = 1000) -> np.ndarray:
    """Downsample waveform to n_points using peak envelope."""
    chunk_size = max(1, len(y) // n_points)
    n_chunks = len(y) // chunk_size
    trimmed = y[:n_chunks * chunk_size].reshape(n_chunks, chunk_size)
    return np.abs(trimmed).max(axis=1).astype(np.float32)
