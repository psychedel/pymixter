"""Tests for waveform/energy compact serialization."""

import json
import tempfile
from pathlib import Path

from pymixter.core.project import Project, _pack_floats, _unpack_floats


def test_pack_unpack_roundtrip():
    original = [i / 100.0 for i in range(101)]
    packed = _pack_floats(original)
    assert isinstance(packed, str)
    assert len(packed) < len(str(original))

    unpacked = _unpack_floats(packed)
    assert len(unpacked) == len(original)
    for a, b in zip(original, unpacked):
        assert abs(a - b) < 0.005  # 8-bit quantization tolerance


def test_pack_empty():
    assert _pack_floats([]) == ""
    assert _unpack_floats("") == []


def test_save_load_with_waveform():
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name

    proj = Project(name="Waveform Test")
    wf = [i / 1000.0 for i in range(1000)]
    proj.add_track("/fake/a.mp3", waveform=wf, energy=[0.5, 0.6, 0.7])
    proj.save(path)

    # Check that waveform is stored as string, not array
    data = json.loads(Path(path).read_text())
    assert isinstance(data["library"][0]["waveform"], str)
    assert isinstance(data["library"][0]["energy"], str)

    # Check it's much smaller than raw JSON array
    raw_size = len(json.dumps(wf))
    packed_size = len(data["library"][0]["waveform"])
    assert packed_size < raw_size / 3

    # Roundtrip
    loaded = Project.load(path)
    assert len(loaded.library[0].waveform) == 1000
    assert isinstance(loaded.library[0].waveform[0], float)
    for a, b in zip(wf, loaded.library[0].waveform):
        assert abs(a - b) < 0.005

    Path(path).unlink()


def test_load_legacy_array_format():
    """Ensure we can still load old project files with raw float arrays."""
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w") as f:
        json.dump({
            "name": "Legacy",
            "version": 1,
            "library": [{
                "path": "/fake/a.mp3",
                "title": "a",
                "waveform": [0.1, 0.2, 0.3],
                "energy": [0.5, 0.6],
                "bpm": None, "key": None, "duration": 0,
                "beats": [], "cue_in": None, "cue_out": None,
                "stems": {},
            }],
            "timeline": [],
            "transitions": [],
        }, f)
        path = f.name

    loaded = Project.load(path)
    assert loaded.library[0].waveform == [0.1, 0.2, 0.3]
    assert loaded.library[0].energy == [0.5, 0.6]
    Path(path).unlink()
