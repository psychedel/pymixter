"""Tests for BPM/beat grid editing."""

from pymixter.core.project import Project


def _make_project():
    proj = Project(name="Test")
    proj.add_track("/fake/a.mp3", bpm=128.0, key="Am", duration=300.0,
                   beats=[0.0, 0.469, 0.938, 1.406, 1.875])
    return proj


def test_bpm_set_rescales_beats():
    proj = _make_project()
    track = proj.library[0]
    original_beats = track.beats.copy()

    # Change BPM from 128 to 64 (halve) — beats should double in time
    old_bpm = track.bpm
    track.bpm = 64.0
    ratio = old_bpm / 64.0  # 2.0
    track.beats = [round(b * ratio, 4) for b in track.beats]

    assert track.bpm == 64.0
    assert abs(track.beats[1] - original_beats[1] * 2) < 0.001


def test_bpm_halve():
    proj = _make_project()
    track = proj.library[0]
    track.bpm = round(track.bpm / 2, 1)
    track.beats = [round(b * 2, 4) for b in track.beats]
    assert track.bpm == 64.0
    assert track.beats[0] == 0.0


def test_bpm_double():
    proj = _make_project()
    track = proj.library[0]
    track.bpm = round(track.bpm * 2, 1)
    track.beats = [round(b / 2, 4) for b in track.beats]
    assert track.bpm == 256.0


def test_bpm_nudge():
    proj = _make_project()
    track = proj.library[0]
    old = track.bpm
    track.bpm = round(track.bpm + 0.5, 1)
    assert track.bpm == 128.5
    # Beats should be slightly rescaled
    ratio = old / track.bpm
    new_beats = [round(b * ratio, 4) for b in [0.0, 0.469, 0.938, 1.406, 1.875]]
    assert abs(new_beats[1] - 0.469 * ratio) < 0.001


def test_key_set():
    proj = _make_project()
    track = proj.library[0]
    track.key = "Cm"
    assert track.key == "Cm"
