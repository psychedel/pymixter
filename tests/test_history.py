"""Tests for undo/redo history system."""

from pymixter.core.project import Project
from pymixter.core.history import History


def _make_project():
    proj = Project(name="Test")
    proj.add_track("/fake/a.mp3", bpm=128.0, key="Am", duration=300.0)
    proj.add_track("/fake/b.mp3", bpm=126.0, key="Cm", duration=250.0)
    return proj


def test_undo_redo_basic():
    proj = _make_project()
    h = History()

    h.checkpoint(proj, "add track c")
    proj.add_track("/fake/c.mp3", bpm=130.0)
    assert len(proj.library) == 3

    desc = h.undo(proj)
    assert desc == "add track c"
    assert len(proj.library) == 2

    desc = h.redo(proj)
    assert desc == "add track c"
    assert len(proj.library) == 3


def test_undo_empty():
    proj = _make_project()
    h = History()
    assert h.undo(proj) is None


def test_redo_empty():
    proj = _make_project()
    h = History()
    assert h.redo(proj) is None


def test_redo_cleared_on_new_checkpoint():
    proj = _make_project()
    h = History()

    h.checkpoint(proj, "step 1")
    proj.add_track("/fake/c.mp3")

    h.undo(proj)
    assert h.can_redo

    h.checkpoint(proj, "step 2")
    assert not h.can_redo


def test_max_undo_limit():
    proj = _make_project()
    h = History()
    h.MAX_UNDO = 3

    for i in range(5):
        h.checkpoint(proj, f"step {i}")
        proj.name = f"Version {i}"

    assert len(h._undo_stack) == 3


def test_undo_restores_timeline():
    proj = _make_project()
    h = History()

    h.checkpoint(proj, "add to timeline")
    proj.append_to_timeline(0)
    proj.append_to_timeline(1)
    assert proj.timeline == [0, 1]

    h.undo(proj)
    assert proj.timeline == []
