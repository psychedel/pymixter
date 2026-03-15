"""Audio analysis: BPM, key detection, beat grid, energy, waveform, spectral descriptors.

Uses essentia for all audio analysis — key, BPM, spectral features,
silence detection, tuning, pitch, and harmonic content.
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
    fade detection, chord progression, spectral descriptors, silence rate,
    tuning frequency, inharmonicity, pitch, and tempo stability.

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

    # BPM + beat positions (keep full result for tempogram)
    rhythm_result = es.RhythmExtractor2013()(audio)
    bpm_val, beat_positions = rhythm_result[0], rhythm_result[1]
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

        # Spectral descriptors + pitch (single combined frame pass)
        spectral = _compute_spectral_and_pitch(audio, sr)
        result.update(spectral)

        # Silence rate via essentia SilenceRate
        result["silence_rate"] = _compute_silence_rate(audio)

        # Tuning frequency (detects microtonality / A=440 deviation)
        result["tuning_frequency"] = _compute_tuning_frequency(audio, sr)

        # Inharmonicity (harmonic content analysis)
        result["inharmonicity"] = _compute_inharmonicity(audio, sr)

        # Tempogram ratio from existing rhythm result (no re-computation)
        result["tempogram_ratio"] = _tempogram_ratio_from_rhythm(rhythm_result)

        # Refine cue points using silence rate
        if result["silence_rate"] is not None and result["silence_rate"] > 0.3:
            result["cue_in"], result["cue_out"] = _detect_cue_points(
                audio, sr, beat_times, rms_factor=0.15,
            )

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
                       beat_times: list[float],
                       rms_factor: float = 0.1) -> tuple[float, float]:
    """Find musically meaningful start/end points.

    Cue-in: first beat where RMS exceeds rms_factor of track peak.
    Cue-out: last beat where RMS exceeds rms_factor of track peak.

    A higher rms_factor (e.g. 0.15) is used for silence-heavy tracks
    to skip past quiet intros/outros more aggressively.
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
    rms_threshold = rms_factor * np.sqrt(np.mean(y ** 2))

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


# ── Spectral features + pitch (combined single pass) ─────────

def _compute_spectral_and_pitch(audio: np.ndarray, sr: int) -> dict:
    """Compute spectral descriptors and pitch in a single frame-by-frame pass.

    Returns dict with: spectral_centroid, spectral_rolloff, spectral_flux,
    mfcc (13 mean coefficients), mel_bands (40 mean band energies),
    pitch_mean, pitch_std.
    """
    import essentia.standard as es

    frame_size = 2048
    hop_size = 1024
    spec_size = frame_size // 2 + 1

    w = es.Windowing(type='hann')
    spectrum = es.Spectrum(size=frame_size)
    centroid = es.Centroid(range=sr / 2)
    rolloff = es.RollOff()
    flux = es.Flux()
    mfcc_algo = es.MFCC(inputSize=spec_size, numberCoefficients=13)
    mel_algo = es.MelBands(inputSize=spec_size, numberBands=40)
    pitch_algo = es.PitchYinFFT(frameSize=frame_size)

    centroids = []
    rolloffs = []
    fluxes = []
    mfccs = []
    mel_list = []
    pitches = []

    for frame in es.FrameGenerator(audio, frameSize=frame_size, hopSize=hop_size):
        windowed = w(frame)
        spec = spectrum(windowed)

        centroids.append(float(centroid(spec)))
        rolloffs.append(float(rolloff(spec)))
        fluxes.append(float(flux(spec)))

        bands, coeffs = mfcc_algo(spec)
        mfccs.append(coeffs)

        mel_b = mel_algo(spec)
        mel_list.append(mel_b)

        pitch, confidence = pitch_algo(spec)
        if confidence > 0.5 and pitch > 20:
            pitches.append(float(pitch))

    if not centroids:
        return {
            "spectral_centroid": None, "spectral_rolloff": None,
            "spectral_flux": None, "mfcc": [], "mel_bands": [],
            "pitch_mean": None, "pitch_std": None,
        }

    result = {
        "spectral_centroid": round(float(np.mean(centroids)), 2),
        "spectral_rolloff": round(float(np.mean(rolloffs)), 2),
        "spectral_flux": round(float(np.mean(fluxes)), 4),
        "mfcc": [round(float(x), 4) for x in np.mean(mfccs, axis=0)],
        "mel_bands": [round(float(x), 4) for x in np.mean(mel_list, axis=0)],
    }

    if pitches:
        result["pitch_mean"] = round(float(np.mean(pitches)), 2)
        result["pitch_std"] = round(float(np.std(pitches)), 2)
    else:
        result["pitch_mean"] = None
        result["pitch_std"] = None

    return result


# ── Silence detection ─────────────────────────────────────────

def _compute_silence_rate(audio: np.ndarray) -> float:
    """Compute fraction of silent frames using essentia SilenceRate.

    Uses multiple thresholds (-50dB, -40dB, -30dB) and returns
    the rate at -50dB (most sensitive). Useful for detecting tracks
    with long intros/outros or ambient sections.
    """
    import essentia.standard as es

    frame_size = 2048
    hop_size = 1024
    # Thresholds in linear amplitude
    thresholds = [
        10 ** (-50 / 20),  # -50 dB
        10 ** (-40 / 20),  # -40 dB
        10 ** (-30 / 20),  # -30 dB
    ]
    silence_rate = es.SilenceRate(thresholds=thresholds)

    rates = []
    for frame in es.FrameGenerator(audio, frameSize=frame_size, hopSize=hop_size):
        r = silence_rate(frame)
        rates.append(r)

    if not rates:
        return 1.0

    # Average silence rate at -50dB threshold (first threshold)
    avg = float(np.mean([r[0] for r in rates]))
    return round(avg, 3)


# ── Tuning & inharmonicity ────────────────────────────────────

def _compute_tuning_frequency(audio: np.ndarray, sr: int) -> float:
    """Detect tuning reference frequency (deviation from A=440Hz).

    Returns the estimated tuning frequency in Hz.
    """
    import essentia.standard as es

    frame_size = 4096
    hop_size = 2048
    w = es.Windowing(type='blackmanharris62')
    spectrum = es.Spectrum(size=frame_size)
    peaks = es.SpectralPeaks(orderBy='magnitude', magnitudeThreshold=0.00001,
                              minFrequency=20, maxFrequency=3500)
    tuning = es.TuningFrequency()

    frequencies = []
    for frame in es.FrameGenerator(audio, frameSize=frame_size, hopSize=hop_size):
        windowed = w(frame)
        spec = spectrum(windowed)
        freqs, mags = peaks(spec)
        if len(freqs) > 0:
            tf, _ = tuning(freqs, mags)
            frequencies.append(float(tf))

    return round(float(np.median(frequencies)), 2) if frequencies else 440.0


def _compute_inharmonicity(audio: np.ndarray, sr: int) -> float:
    """Compute mean inharmonicity across frames.

    Inharmonicity measures deviation from harmonic series —
    low for pitched/tonal content, high for percussive/noise.
    """
    import essentia.standard as es

    frame_size = 4096
    hop_size = 2048
    w = es.Windowing(type='blackmanharris62')
    spectrum = es.Spectrum(size=frame_size)
    peaks = es.SpectralPeaks(orderBy='frequency', magnitudeThreshold=0.00001,
                              minFrequency=20, maxFrequency=5000)
    pitch_algo = es.PitchYinFFT(frameSize=frame_size)
    inharm = es.Inharmonicity()

    values = []
    for frame in es.FrameGenerator(audio, frameSize=frame_size, hopSize=hop_size):
        windowed = w(frame)
        spec = spectrum(windowed)
        freqs, mags = peaks(spec)
        pitch, confidence = pitch_algo(spec)
        if confidence > 0.5 and len(freqs) >= 2 and pitch > 20:
            try:
                val = inharm(freqs, mags)
                values.append(float(val))
            except Exception:
                pass

    return round(float(np.mean(values)), 4) if values else 0.0


# ── Tempogram ─────────────────────────────────────────────────

def _tempogram_ratio_from_rhythm(rhythm_result: tuple) -> float:
    """Extract tempo stability from an existing RhythmExtractor2013 result.

    Takes the full tuple returned by RhythmExtractor2013 and computes
    the ratio of secondary to primary tempo hypothesis confidence.
    A ratio near 1.0 suggests tempo ambiguity (e.g. half/double time),
    near 0.0 suggests very stable tempo.
    """
    try:
        bpm_ests = rhythm_result[4]
        if len(bpm_ests) >= 2 and bpm_ests[0][1] > 0:
            ratio = float(bpm_ests[1][1] / bpm_ests[0][1])
            return round(min(ratio, 1.0), 3)
    except Exception:
        pass
    return 0.0


# ── Waveform ─────────────────────────────────────────────────

def _compute_waveform_overview(y: np.ndarray, n_points: int = 1000) -> np.ndarray:
    """Downsample waveform to n_points using peak envelope."""
    chunk_size = max(1, len(y) // n_points)
    n_chunks = len(y) // chunk_size
    trimmed = y[:n_chunks * chunk_size].reshape(n_chunks, chunk_size)
    return np.abs(trimmed).max(axis=1).astype(np.float32)
