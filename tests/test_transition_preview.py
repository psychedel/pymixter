"""Tests for transition preview rendering."""

import numpy as np
import pytest

from pymixter.core.project import Project
from pymixter.core.mixer import render_transition_preview


def _make_project_with_audio(tmp_path):
    """Create a project with two short WAV files in timeline."""
    from pedalboard.io import AudioFile

    proj = Project(name="Preview Test")

    # Create two 5-second stereo WAV files
    sr = 44100
    for name in ("a", "b"):
        path = str(tmp_path / f"{name}.wav")
        audio = np.random.randn(2, sr * 5).astype(np.float32) * 0.1
        with AudioFile(path, "w", samplerate=sr, num_channels=2) as f:
            f.write(audio)
        proj.add_track(path, bpm=128.0, key="Am", duration=5.0)

    proj.append_to_timeline(0)
    proj.append_to_timeline(1)
    proj.set_transition(0, "crossfade", 4)
    return proj


def test_preview_renders_audio(tmp_path):
    proj = _make_project_with_audio(tmp_path)
    audio, sr = render_transition_preview(proj, 0)
    assert audio.shape[0] == 2  # stereo
    assert audio.shape[1] > 0  # has audio
    assert sr == 44100


def test_preview_invalid_position():
    proj = Project(name="Empty")
    proj.add_track("/fake/a.mp3")
    proj.append_to_timeline(0)
    with pytest.raises(IndexError):
        render_transition_preview(proj, 0)  # only 1 track, no transition possible


def test_preview_without_transition(tmp_path):
    """Preview between tracks with no explicit transition defined."""
    proj = _make_project_with_audio(tmp_path)
    proj.transitions = []  # Remove all transitions
    audio, sr = render_transition_preview(proj, 0)
    assert audio.shape[0] == 2
    assert audio.shape[1] > 0
