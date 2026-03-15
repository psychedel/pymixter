"""Audio analysis: BPM, key detection, beat grid, energy, waveform."""

from __future__ import annotations

from pathlib import Path

import librosa
import numpy as np


def analyze_track(path: str, full: bool = False) -> dict:
    """Analyze a track and return BPM, key, duration, and waveform overview.

    With full=True, also computes beat grid, cue points, and energy profile.
    Returns a dict suitable for passing to Project.add_track(**analysis).
    """
    y, sr = librosa.load(path, sr=None, mono=True)

    duration = librosa.get_duration(y=y, sr=sr)

    # BPM + beat positions
    tempo, beat_frames = librosa.beat.beat_track(y=y, sr=sr)
    if hasattr(tempo, "__len__"):
        tempo = float(tempo[0])
    bpm = round(float(tempo), 1)
    beat_times = librosa.frames_to_time(beat_frames, sr=sr).tolist()

    # Key detection via chroma
    key = _detect_key(y, sr)

    # Waveform overview (downsample to ~1000 points for display)
    waveform = _compute_waveform_overview(y, n_points=1000)

    result = {
        "title": Path(path).stem,
        "bpm": bpm,
        "key": key,
        "duration": round(duration, 2),
        "_waveform": waveform.tolist(),
    }

    if full:
        result["beats"] = [round(b, 4) for b in beat_times]
        result["cue_in"], result["cue_out"] = _detect_cue_points(
            y, sr, beat_times,
        )
        result["energy"] = _compute_energy_profile(y, sr)

    return result


def analyze_beats(path: str) -> dict:
    """Lightweight analysis: just beats, cue points, energy."""
    y, sr = librosa.load(path, sr=None, mono=True)
    _tempo, beat_frames = librosa.beat.beat_track(y=y, sr=sr)
    beat_times = librosa.frames_to_time(beat_frames, sr=sr).tolist()

    cue_in, cue_out = _detect_cue_points(y, sr, beat_times)
    energy = _compute_energy_profile(y, sr)

    return {
        "beats": [round(b, 4) for b in beat_times],
        "cue_in": cue_in,
        "cue_out": cue_out,
        "energy": energy,
    }


# ── Key detection ────────────────────────────────────────────

def _detect_key(y: np.ndarray, sr: int) -> str:
    """Simple key detection using chroma correlation with Krumhansl profiles."""
    chroma = librosa.feature.chroma_cqt(y=y, sr=sr)
    chroma_mean = chroma.mean(axis=1)

    # Krumhansl-Kessler major/minor profiles
    major_profile = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09,
                              2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
    minor_profile = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53,
                              2.54, 4.75, 3.98, 2.69, 3.34, 3.17])

    note_names = ["C", "C#", "D", "D#", "E", "F",
                  "F#", "G", "G#", "A", "A#", "B"]

    best_corr = -1.0
    best_key = "C"

    for shift in range(12):
        shifted = np.roll(chroma_mean, -shift)
        corr_major = float(np.corrcoef(shifted, major_profile)[0, 1])
        corr_minor = float(np.corrcoef(shifted, minor_profile)[0, 1])

        if corr_major > best_corr:
            best_corr = corr_major
            best_key = note_names[shift]
        if corr_minor > best_corr:
            best_corr = corr_minor
            best_key = note_names[shift] + "m"

    return best_key


# ── Beat grid & cue points ──────────────────────────────────

def _detect_cue_points(y: np.ndarray, sr: int,
                       beat_times: list[float]) -> tuple[float, float]:
    """Find musically meaningful start/end points.

    Cue-in: first beat where RMS exceeds 10% of track peak.
    Cue-out: last beat where RMS exceeds 10% of track peak.
    """
    if not beat_times:
        duration = librosa.get_duration(y=y, sr=sr)
        return 0.0, round(duration, 4)

    # RMS per beat segment
    rms_threshold = 0.1 * np.sqrt(np.mean(y ** 2))
    hop = 512
    rms = librosa.feature.rms(y=y, hop_length=hop)[0]
    rms_times = librosa.frames_to_time(np.arange(len(rms)), sr=sr, hop_length=hop)

    def rms_at(t: float) -> float:
        idx = np.searchsorted(rms_times, t)
        idx = min(idx, len(rms) - 1)
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


# ── Waveform ─────────────────────────────────────────────────

def _compute_waveform_overview(y: np.ndarray, n_points: int = 1000) -> np.ndarray:
    """Downsample waveform to n_points using peak envelope."""
    chunk_size = max(1, len(y) // n_points)
    n_chunks = len(y) // chunk_size
    trimmed = y[:n_chunks * chunk_size].reshape(n_chunks, chunk_size)
    return np.abs(trimmed).max(axis=1).astype(np.float32)
