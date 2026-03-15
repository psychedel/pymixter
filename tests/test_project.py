"""Tests for core project state management."""

import json
import tempfile
from pathlib import Path

from pymixter.core.project import Project, Track, Transition


def test_create_and_save():
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name

    proj = Project(name="Test Mix")
    proj.save(path)

    data = json.loads(Path(path).read_text())
    assert data["name"] == "Test Mix"
    assert data["version"] == 1
    Path(path).unlink()


def test_add_track():
    proj = Project(name="Test")
    track = proj.add_track("/fake/track.mp3", bpm=128.0, key="Am", duration=300.0)
    assert track.title == "track"
    assert track.bpm == 128.0
    assert len(proj.library) == 1


def test_timeline():
    proj = Project(name="Test")
    proj.add_track("/fake/a.mp3")
    proj.add_track("/fake/b.mp3")
    proj.append_to_timeline(0)
    proj.append_to_timeline(1)
    assert proj.timeline == [0, 1]


def test_save_load_roundtrip():
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name

    proj = Project(name="Roundtrip")
    proj.add_track("/fake/a.mp3", bpm=125.0, key="Cm", duration=200.0)
    proj.add_track("/fake/b.mp3", bpm=126.0, key="Gm", duration=250.0)
    proj.append_to_timeline(0)
    proj.append_to_timeline(1)
    proj.add_transition(0, 1, type="eq_fade", length_bars=32)
    proj.save(path)

    loaded = Project.load(path)
    assert loaded.name == "Roundtrip"
    assert len(loaded.library) == 2
    assert loaded.library[0].bpm == 125.0
    assert loaded.timeline == [0, 1]
    assert loaded.transitions[0].type == "eq_fade"
    assert loaded.transitions[0].length_bars == 32
    Path(path).unlink()


def test_version_increments():
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name

    proj = Project(name="V")
    proj.save(path)
    assert proj.get_version() == 1

    proj.save(path)
    assert proj.get_version() == 2
    Path(path).unlink()


def test_transition():
    proj = Project(name="T")
    proj.add_track("/fake/a.mp3")
    proj.add_track("/fake/b.mp3")
    t = proj.add_transition(0, 1, type="echo_out", length_bars=8)
    assert t.type == "echo_out"
    assert t.length_bars == 8
    assert len(proj.transitions) == 1
