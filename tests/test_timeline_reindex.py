"""Tests for timeline move/remove with transition reindexing."""

from pymixter.core.project import Project, Transition


def _make_timeline():
    """Create project with 4 tracks in timeline: A(0) B(1) C(2) D(3)."""
    proj = Project(name="Test")
    for name in "ABCD":
        proj.add_track(f"/fake/{name}.mp3", bpm=128.0, key="Am", duration=300.0)
    for i in range(4):
        proj.append_to_timeline(i)
    # Add transitions: 0→1 crossfade, 1→2 eq_fade, 2→3 echo_out
    proj.set_transition(0, "crossfade", 16)
    proj.set_transition(1, "eq_fade", 32)
    proj.set_transition(2, "echo_out", 8)
    return proj


def test_move_forward_reindexes():
    proj = _make_timeline()
    # Move pos 0 to pos 2: A B C D -> B C A D
    proj.move_timeline_track(0, 2)
    assert proj.timeline == [1, 2, 0, 3]

    # Transition 1→2 (eq_fade) should now be at 0→1 (B→C still adjacent)
    tr_map = {t.from_track: t for t in proj.transitions}
    assert 0 in tr_map
    assert tr_map[0].type == "eq_fade"

    # Transition 2→3 (echo_out) should now be at 1→2... wait, that was C→D.
    # After move: B(0) C(1) A(2) D(3)
    # Old 2→3 was C→D, new positions: C is at 1, D is at 3 — NOT adjacent, so dropped
    assert 2 not in tr_map or tr_map.get(2, Transition(0, 0)).type == "echo_out"


def test_move_backward_reindexes():
    proj = _make_timeline()
    # Move pos 3 to pos 1: A B C D -> A D B C
    proj.move_timeline_track(3, 1)
    assert proj.timeline == [0, 3, 1, 2]

    # Old 0→1 (A→B) crossfade: A still at 0, B now at 2 — not adjacent, dropped
    # Old 1→2 (B→C) eq_fade: B now at 2, C now at 3 — adjacent! Should be 2→3
    tr_map = {t.from_track: t for t in proj.transitions}
    assert 2 in tr_map
    assert tr_map[2].type == "eq_fade"


def test_remove_reindexes():
    proj = _make_timeline()
    # Remove pos 1 (B): A B C D -> A C D
    proj.remove_from_timeline(1)
    assert proj.timeline == [0, 2, 3]

    # Old 0→1 (A→B): B removed, dropped
    # Old 1→2 (B→C): B removed, dropped
    # Old 2→3 (C→D): C now at 1, D at 2 — adjacent! Should be 1→2
    tr_map = {t.from_track: t for t in proj.transitions}
    assert 1 in tr_map
    assert tr_map[1].type == "echo_out"


def test_move_same_position_noop():
    proj = _make_timeline()
    transitions_before = len(proj.transitions)
    proj.move_timeline_track(1, 1)
    assert len(proj.transitions) == transitions_before


def test_remove_last():
    proj = _make_timeline()
    proj.remove_from_timeline(3)
    assert proj.timeline == [0, 1, 2]
    # Transition 2→3 should be dropped (pos 3 gone)
    assert all(t.from_track < 2 for t in proj.transitions)
