"""Automix engine — automatic track ordering and transition generation.

Finds the best harmonic path through a set of tracks by scoring
key compatibility (Camelot wheel), BPM proximity, energy flow,
danceability, and chord compatibility.  Uses greedy nearest-neighbor
seeding followed by 2-opt local search for improved global ordering.
"""

from __future__ import annotations

from pymixter.core.project import Project, Track, Transition, get_compatible_keys

# Chord root circle-of-fifths distance for compatibility scoring
_CHORD_ROOTS = ["C", "G", "D", "A", "E", "B", "F#", "Db", "Ab", "Eb", "Bb", "F"]
_ROOT_INDEX = {r: i for i, r in enumerate(_CHORD_ROOTS)}
# Enharmonic aliases
_ROOT_INDEX.update({"C#": 6, "Gb": 6, "D#": 10, "G#": 4, "A#": 10})


def _chord_distance(chord_a: str, chord_b: str) -> int:
    """Circle-of-fifths distance between two chord roots (0–6). Lower = more compatible."""
    # Extract root from chord label like "C", "Am", "F#m", "Dbmaj"
    root_a = chord_a.rstrip("m").rstrip("aj").rstrip("in")
    root_b = chord_b.rstrip("m").rstrip("aj").rstrip("in")
    if root_a not in _ROOT_INDEX or root_b not in _ROOT_INDEX:
        return 6  # unknown → max distance
    diff = abs(_ROOT_INDEX[root_a] - _ROOT_INDEX[root_b])
    return min(diff, 12 - diff)


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

    # Danceability flow — prefer similar danceability
    if a.danceability is not None and b.danceability is not None:
        dance_diff = abs(a.danceability - b.danceability)
        score -= dance_diff * 2

    # Chord compatibility — penalize dissonant chord transitions
    if a.chords and b.chords:
        # Compare last chord of a with first chord of b
        last_chord = a.chords[-1][1]
        first_chord = b.chords[0][1]
        dist = _chord_distance(last_chord, first_chord)
        # 0 distance = same root (+2), 1-2 = close (0 to +1), 3+ = penalty
        if dist <= 1:
            score += 2 - dist
        else:
            score -= (dist - 1) * 0.5

    return score


def _route_score(order: list[int], track_map: dict[int, Track]) -> float:
    """Total score for a full ordering."""
    total = 0.0
    for i in range(len(order) - 1):
        total += _pair_score(track_map[order[i]], track_map[order[i + 1]])
    return total


def _two_opt(order: list[int], track_map: dict[int, Track],
             fixed_start: bool = True) -> list[int]:
    """Improve ordering with 2-opt local search.

    Repeatedly reverses sub-segments to find a better total score.
    If fixed_start is True, the first element stays in place.
    """
    n = len(order)
    if n < 4:
        return order

    best = list(order)
    best_score = _route_score(best, track_map)
    improved = True

    start = 1 if fixed_start else 0

    while improved:
        improved = False
        for i in range(start, n - 1):
            for j in range(i + 2, n):
                # Reverse segment [i, j]
                candidate = best[:i] + best[i:j + 1][::-1] + best[j + 1:]
                s = _route_score(candidate, track_map)
                if s > best_score:
                    best = candidate
                    best_score = s
                    improved = True

    return best


def find_best_order(tracks: list[tuple[int, Track]],
                    start_idx: int | None = None) -> list[int]:
    """Find a good track ordering using greedy nearest-neighbor + 2-opt refinement.

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

    track_map = {i: t for i, t in analyzed}

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

    # 2-opt refinement — improve greedy solution by swapping segments
    order = _two_opt(order, track_map, fixed_start=(start_idx is not None))

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

    # If outgoing track has a natural fade-out, use longer crossfade
    if a.fade_out_start is not None and key_ok and bpm_diff < 3:
        return "crossfade", 32

    # Both tracks have stems, close BPM, compatible key → stem swap
    if key_ok and bpm_diff < 3 and a.stems and b.stems:
        return "stem_swap", 32

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
