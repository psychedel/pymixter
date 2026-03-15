"""Tests for precision alignment features: time parsing, grid manipulation, snap, key matching."""

import pytest
from pymixter.core.project import (
    Track, Transition, parse_time,
    key_semitone_distance, key_compatibility, get_compatible_keys,
)


class TestParseTime:
    def test_seconds(self):
        assert parse_time("90") == 90.0

    def test_seconds_float(self):
        assert parse_time("90.5") == 90.5

    def test_minutes_seconds(self):
        assert parse_time("1:30") == 90.0

    def test_minutes_seconds_ms(self):
        assert parse_time("1:30.5") == 90.5

    def test_zero(self):
        assert parse_time("0:00") == 0.0

    def test_large(self):
        assert parse_time("5:00") == 300.0

    def test_whitespace(self):
        assert parse_time("  1:30  ") == 90.0

    def test_invalid(self):
        with pytest.raises(ValueError):
            parse_time("abc")


class TestTrackSnap:
    def _make_track(self):
        """Create a track with a regular beat grid at 120 BPM (0.5s per beat)."""
        beats = [i * 0.5 for i in range(64)]  # 16 bars
        return Track(
            path="/test.mp3", bpm=120, duration=32.0,
            beats=beats, cue_in=2.0, cue_out=30.0,
        )

    def test_snap_to_beat(self):
        t = self._make_track()
        # 2.3s should snap to 2.5s (beat 5)
        assert t.snap_to_beat(2.3) == 2.5
        # 2.1s should snap to 2.0s (beat 4)
        assert t.snap_to_beat(2.1) == 2.0

    def test_snap_to_bar(self):
        t = self._make_track()
        # Bar boundaries at 0, 2.0, 4.0, 6.0, ...
        assert t.snap_to_bar(2.8) == 2.0
        assert t.snap_to_bar(3.5) == 4.0

    def test_snap_to_phrase(self):
        t = self._make_track()
        # Phrase boundaries at 0, 8.0, 16.0, 24.0
        # 5.0 is equidistant between 0 and 8 — min() picks the first (closer by index)
        # Actually 5.0 is closer to 8.0 (distance 3) than 0.0 (distance 5)
        assert t.snap_to_phrase(5.0) == 8.0
        assert t.snap_to_phrase(10.0) == 8.0

    def test_snap_no_beats(self):
        t = Track(path="/test.mp3", duration=30.0)
        assert t.snap_to_beat(5.0) == 5.0
        assert t.snap_to_bar(5.0) == 5.0

    def test_nudge_grid(self):
        t = self._make_track()
        original_first = t.beats[0]
        t.nudge_grid(0.05)  # +50ms
        assert t.beats[0] == pytest.approx(original_first + 0.05, abs=0.001)
        assert t.beats[1] == pytest.approx(0.55, abs=0.001)

    def test_nudge_grid_negative(self):
        t = self._make_track()
        t.nudge_grid(-0.1)
        assert t.beats[0] == pytest.approx(-0.1, abs=0.001)

    def test_beat_at(self):
        t = self._make_track()
        assert t.beat_at(0.0) == 0
        assert t.beat_at(2.3) == 4  # just past beat 4 (2.0s)
        assert t.beat_at(2.5) == 5  # exactly on beat 5

    def test_beat_at_no_beats(self):
        t = Track(path="/test.mp3")
        assert t.beat_at(5.0) is None

    def test_bar_at(self):
        t = self._make_track()
        assert t.bar_at(0.0) == 0
        assert t.bar_at(2.3) == 1  # beat 4 = bar 1
        assert t.bar_at(8.5) == 4  # beat 17 = bar 4


class TestStretchGrid:
    def test_stretch_corrects_bpm(self):
        """If detected BPM is 127.8 but actual is 128, stretch fixes it."""
        # Create grid at 127.8 BPM (0.46948 sec/beat)
        interval = 60.0 / 127.8
        beats = [i * interval for i in range(64)]
        t = Track(path="/test.mp3", bpm=127.8, beats=beats, duration=32.0)

        # Anchor: beat 0 at 0.0, beat 63 at exactly 63 * 60/128
        target_end = 63 * 60.0 / 128.0  # 29.53125
        t.stretch_grid(0, 0.0, 63, target_end)

        assert t.bpm == 128.0
        assert t.beats[0] == pytest.approx(0.0, abs=0.001)
        assert t.beats[63] == pytest.approx(target_end, abs=0.001)

    def test_stretch_preserves_count(self):
        interval = 0.5
        beats = [i * interval for i in range(32)]
        t = Track(path="/test.mp3", bpm=120, beats=beats)
        t.stretch_grid(0, 0.0, 31, 31 * 60.0 / 125.0)
        assert len(t.beats) == 32
        assert t.bpm == 125.0

    def test_stretch_same_beat_noop(self):
        beats = [i * 0.5 for i in range(16)]
        t = Track(path="/test.mp3", bpm=120, beats=beats)
        t.stretch_grid(5, 2.5, 5, 2.5)  # same beat
        assert t.bpm == 120  # unchanged


class TestTransitionOffset:
    def test_default_offset(self):
        tr = Transition(from_track=0, to_track=1)
        assert tr.offset_beats == 0

    def test_set_offset(self):
        tr = Transition(from_track=0, to_track=1, offset_beats=4)
        assert tr.offset_beats == 4

    def test_negative_offset(self):
        tr = Transition(from_track=0, to_track=1, offset_beats=-2)
        assert tr.offset_beats == -2


class TestKeySemitoneDistance:
    def test_compatible_keys_zero(self):
        """Compatible keys need no shift."""
        assert key_semitone_distance("Am", "Dm") == 0  # fifth down
        assert key_semitone_distance("Am", "Em") == 0  # fifth up
        assert key_semitone_distance("Am", "C") == 0   # relative major
        assert key_semitone_distance("Am", "Am") == 0   # same key

    def test_close_clash_small_shift(self):
        """Keys that are just one semitone away from being compatible."""
        shift = key_semitone_distance("Am", "Bbm")
        assert shift is not None
        assert abs(shift) <= 2

    def test_none_for_unknown(self):
        assert key_semitone_distance(None, "Am") is None
        assert key_semitone_distance("Am", None) is None

    def test_distant_key(self):
        """Distant keys need larger shifts."""
        shift = key_semitone_distance("Am", "Fm")
        assert shift is not None
        # Fm is 4 semitones away from Am — shift should move it closer
        assert abs(shift) <= 6

    def test_shift_produces_compatible_key(self):
        """Verify that applying the shift actually produces a compatible key."""
        from pymixter.core.project import NOTES
        key_a = "Am"
        key_b = "Cm"
        shift = key_semitone_distance(key_a, key_b)
        if shift and shift != 0:
            root_b = key_b.rstrip("m")
            is_minor = key_b.endswith("m")
            idx = NOTES.index(root_b)
            new_root = NOTES[(idx + shift) % 12]
            new_key = new_root + ("m" if is_minor else "")
            assert new_key in get_compatible_keys(key_a)
