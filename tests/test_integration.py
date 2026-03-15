"""Integration test: analyze → automix → render with synthetic audio."""

import tempfile
from pathlib import Path

import numpy as np
import pytest

from pymixter.core.project import Project
from pymixter.core.automix import (
    automix, find_best_order, _pair_score, _two_opt, _chord_distance,
)


# ── Synthetic audio generation ─────────────────────────────────

def _generate_tone(freq: float, duration: float = 5.0, sr: int = 44100) -> np.ndarray:
    """Generate a sine wave tone."""
    t = np.linspace(0, duration, int(sr * duration), dtype=np.float32)
    return 0.5 * np.sin(2 * np.pi * freq * t)


def _save_wav(audio: np.ndarray, path: str, sr: int = 44100):
    """Save mono audio as WAV."""
    import soundfile as sf
    sf.write(path, audio, sr)


@pytest.fixture
def synth_tracks(tmp_path):
    """Create synthetic WAV files with different frequencies."""
    tracks = []
    # A4=440Hz (Am), C5=523Hz (C), E5=659Hz (Em), G4=392Hz (G)
    for i, (freq, name) in enumerate([
        (440.0, "track_a"),
        (523.0, "track_c"),
        (659.0, "track_e"),
        (392.0, "track_g"),
    ]):
        path = tmp_path / f"{name}.wav"
        audio = _generate_tone(freq, duration=3.0)
        _save_wav(audio, str(path))
        tracks.append(str(path))
    return tracks


# ── Unit tests for new automix features ────────────────────────

class TestChordDistance:
    def test_same_root(self):
        assert _chord_distance("C", "C") == 0
        assert _chord_distance("Am", "Am") == 0

    def test_fifth(self):
        assert _chord_distance("C", "G") == 1
        assert _chord_distance("Am", "Em") == 1

    def test_distant(self):
        assert _chord_distance("C", "F#") == 6
        assert _chord_distance("C", "Gb") == 6

    def test_unknown_root(self):
        assert _chord_distance("X", "C") == 6


class TestTwoOpt:
    def test_improves_bad_order(self):
        """2-opt should improve a deliberately bad ordering."""
        proj = Project(name="Test")
        # Create tracks with ascending BPM — optimal order is sequential
        for bpm in [120, 122, 124, 126, 128, 130]:
            proj.add_track(f"/fake/{bpm}.mp3", bpm=float(bpm), key="Am", duration=180.0)

        tracks = [(i, t) for i, t in enumerate(proj.library)]
        track_map = {i: t for i, t in tracks}

        # Deliberately scramble: 0, 4, 2, 5, 1, 3
        bad_order = [0, 4, 2, 5, 1, 3]
        from pymixter.core.automix import _route_score
        bad_score = _route_score(bad_order, track_map)

        improved = _two_opt(bad_order, track_map, fixed_start=False)
        improved_score = _route_score(improved, track_map)

        assert improved_score >= bad_score

    def test_preserves_fixed_start(self):
        """With fixed_start=True, first element should not change."""
        proj = Project(name="Test")
        for bpm in [130, 120, 125]:
            proj.add_track(f"/fake/{bpm}.mp3", bpm=float(bpm), key="Am", duration=180.0)

        track_map = {i: t for i, t in enumerate(proj.library)}
        order = [0, 2, 1]
        result = _two_opt(order, track_map, fixed_start=True)
        assert result[0] == 0


class TestPairScoreChords:
    def test_compatible_chords_bonus(self):
        """Tracks with compatible chords should score higher."""
        proj = Project(name="Test")
        t1 = proj.add_track("/fake/a.mp3", bpm=128.0, key="Am", duration=180.0)
        t2 = proj.add_track("/fake/b.mp3", bpm=128.0, key="Am", duration=180.0)
        t3 = proj.add_track("/fake/c.mp3", bpm=128.0, key="Am", duration=180.0)

        # t1 ends on Am, t2 starts on Em (close), t3 starts on F# (distant)
        t1.chords = [(0.0, "C"), (10.0, "Am")]
        t2.chords = [(0.0, "Em"), (10.0, "Am")]
        t3.chords = [(0.0, "F#"), (10.0, "Db")]

        score_good = _pair_score(t1, t2)  # Am → Em = distance 1
        score_bad = _pair_score(t1, t3)   # Am → F# = distance 5

        assert score_good > score_bad


# ── Integration test with real audio analysis ──────────────────

@pytest.mark.skipif(
    not pytest.importorskip("essentia", reason="essentia not installed"),
    reason="essentia not installed",
)
class TestAnalyzeAutomixIntegration:
    def test_analyze_synthetic(self, synth_tracks):
        """Analyze synthetic WAV files and verify basic results."""
        from pymixter.core.analysis import analyze_track

        for path in synth_tracks:
            result = analyze_track(path, full=True)
            assert result["bpm"] > 0
            assert result["key"]
            assert result["duration"] > 0
            assert result["waveform"]
            assert result["replay_gain"] is not None
            assert result.get("energy")

    def test_analyze_error_on_missing_file(self):
        """AnalysisError should be raised for missing files."""
        from pymixter.core.analysis import analyze_track, AnalysisError

        with pytest.raises(AnalysisError):
            analyze_track("/nonexistent/file.wav")

    def test_analyze_error_on_corrupt_file(self, tmp_path):
        """AnalysisError should be raised for corrupt files."""
        from pymixter.core.analysis import analyze_track, AnalysisError

        corrupt = tmp_path / "corrupt.wav"
        corrupt.write_bytes(b"this is not audio")
        with pytest.raises(AnalysisError):
            analyze_track(str(corrupt))

    def test_full_pipeline(self, synth_tracks):
        """Full pipeline: import → analyze → automix → verify transitions."""
        from pymixter.core.analysis import analyze_track

        proj = Project(name="Integration Test")
        for path in synth_tracks:
            analysis = analyze_track(path, full=True)
            proj.add_track(path, **analysis)

        assert len(proj.library) == 4

        # Automix
        order = automix(proj)
        assert len(order) == 4
        assert len(proj.timeline) == 4
        assert len(proj.transitions) == 3  # n-1 transitions

        # Each transition should have a valid type
        valid_types = {"crossfade", "eq_fade", "filter_sweep", "echo_out", "cut", "stem_swap"}
        for tr in proj.transitions:
            assert tr.type in valid_types
            assert tr.length_bars > 0

    def test_render_pipeline(self, synth_tracks):
        """Full pipeline including render to WAV."""
        from pymixter.core.analysis import analyze_track
        from pymixter.core.mixer import render_timeline

        proj = Project(name="Render Test")
        for path in synth_tracks:
            analysis = analyze_track(path, full=False)
            proj.add_track(path, **analysis)

        automix(proj)

        audio, sr = render_timeline(proj)
        assert audio.shape[0] == 2  # stereo
        assert audio.shape[1] > 0
        assert sr == 44100
