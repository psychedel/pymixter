"""Automix engine — automatic track ordering and transition generation.

Finds the best harmonic path through a set of tracks by scoring
key compatibility (Camelot wheel), BPM proximity, and energy flow.
Assigns transition types based on the musical relationship between
adjacent tracks.
"""

from __future__ import annotations

from pymixter.core.project import Project, Track, Transition, get_compatible_keys


def _pair_score(a: Track, b: Track) -> float:
    """Score how well track b follows track a. Higher = better."""
    if not a.bpm or not b.bpm or not a.key or not b.key:
        return -100.0

    score = 0.0

    # Key compatibility (Camelot wheel)
    compatible = get_compatible_keys(a.key)
    if b.key == a.key:
        score += 15  # same key is best
    elif b.key in compatible:
        score += 10  # compatible key

    # BPM proximity — penalize big jumps
    bpm_diff = abs(a.bpm - b.bpm)
    score -= bpm_diff * 0.8

    # Energy flow — prefer gradual changes
    if a.energy and b.energy:
        a_end = sum(a.energy[-8:]) / min(8, len(a.energy))
        b_start = sum(b.energy[:8]) / min(8, len(b.energy))
        energy_diff = abs(a_end - b_start)
        score -= energy_diff * 5

    return score


def find_best_order(tracks: list[tuple[int, Track]],
                    start_idx: int | None = None) -> list[int]:
    """Find a good track ordering using greedy nearest-neighbor on pair scores.

    Args:
        tracks: list of (library_index, Track) pairs
        start_idx: library index to start from (None = auto-pick)

    Returns:
        Ordered list of library indices.
    """
    if not tracks:
        return []
    if len(tracks) == 1:
        return [tracks[0][0]]

    # Filter to tracks with analysis data
    analyzed = [(i, t) for i, t in tracks if t.bpm and t.key]
    unanalyzed = [i for i, t in tracks if not t.bpm or not t.key]

    if not analyzed:
        # No analysis — just return original order
        return [i for i, _ in tracks]

    # Pick starting track
    if start_idx is not None:
        current = next(((i, t) for i, t in analyzed if i == start_idx), None)
        if current:
            remaining = [(i, t) for i, t in analyzed if i != start_idx]
        else:
            current = analyzed[0]
            remaining = analyzed[1:]
    else:
        # Start with the track closest to median BPM (good center point)
        bpms = [t.bpm for _, t in analyzed]
        median_bpm = sorted(bpms)[len(bpms) // 2]
        analyzed_by_bpm = sorted(analyzed, key=lambda x: abs(x[1].bpm - median_bpm))
        current = analyzed_by_bpm[0]
        remaining = [x for x in analyzed if x[0] != current[0]]

    # Greedy path: always pick the best next track
    order = [current[0]]
    while remaining:
        best_score = -999.0
        best_idx = 0
        for j, (lib_idx, candidate) in enumerate(remaining):
            s = _pair_score(current[1], candidate)
            if s > best_score:
                best_score = s
                best_idx = j

        current = remaining.pop(best_idx)
        order.append(current[0])

    # Append unanalyzed tracks at the end
    order.extend(unanalyzed)
    return order


def pick_transition_type(a: Track, b: Track) -> tuple[str, int]:
    """Choose transition type and length based on track relationship.

    Returns (type, length_bars).
    """
    if not a.bpm or not b.bpm:
        return "crossfade", 16

    bpm_diff = abs(a.bpm - b.bpm)
    compatible = get_compatible_keys(a.key) if a.key else set()
    key_ok = b.key in compatible if b.key else False

    # Big BPM difference → short cut
    if bpm_diff > 8:
        return "cut", 4

    # Same key, close BPM → long EQ fade (bass swap)
    if key_ok and bpm_diff < 3:
        return "eq_fade", 32

    # Compatible key, moderate BPM diff → filter sweep
    if key_ok:
        return "filter_sweep", 16

    # Incompatible key → echo out to mask the clash
    return "echo_out", 8


def automix(project: Project,
            track_indices: list[int] | None = None,
            start_idx: int | None = None,
            clear_timeline: bool = True) -> list[int]:
    """Auto-arrange tracks and generate transitions.

    Args:
        project: the project to modify
        track_indices: which library tracks to include (None = all analyzed)
        start_idx: library index to start from
        clear_timeline: whether to clear existing timeline first

    Returns:
        The new timeline order (list of library indices).
    """
    # Select tracks
    if track_indices is not None:
        tracks = [(i, project.library[i]) for i in track_indices
                  if i < len(project.library)]
    else:
        tracks = [(i, t) for i, t in enumerate(project.library)
                  if t.bpm and t.key]

    if not tracks:
        return []

    # Find optimal order
    order = find_best_order(tracks, start_idx=start_idx)

    # Apply to project
    if clear_timeline:
        project.timeline.clear()
        project.transitions.clear()

    for lib_idx in order:
        project.timeline.append(lib_idx)

    # Generate transitions between consecutive tracks
    for pos in range(len(order) - 1):
        a = project.library[order[pos]]
        b = project.library[order[pos + 1]]
        tr_type, tr_bars = pick_transition_type(a, b)
        project.add_transition(pos, pos + 1, type=tr_type, length_bars=tr_bars)

    return order
