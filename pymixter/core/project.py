"""Shared project state — the single source of truth for both TUI and CLI."""

from __future__ import annotations

import base64
import json
import shutil
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Literal

def _pack_floats(values: list[float]) -> str:
    """Quantize 0–1 floats to uint8 and encode as base85 string."""
    if not values:
        return ""
    data = bytes(min(255, max(0, int(v * 255))) for v in values)
    return base64.b85encode(data).decode("ascii")


def _unpack_floats(encoded: str) -> list[float]:
    """Decode base85 string back to 0–1 float list."""
    if not encoded:
        return []
    data = base64.b85decode(encoded)
    return [b / 255.0 for b in data]


def parse_time(s: str) -> float:
    """Parse time string to seconds. Supports: '90', '1:30', '1:30.5', '90.5'."""
    s = s.strip()
    if ":" in s:
        parts = s.split(":")
        minutes = int(parts[0])
        seconds = float(parts[1])
        return minutes * 60 + seconds
    return float(s)


AUDIO_EXTENSIONS = frozenset({
    ".mp3", ".flac", ".wav", ".ogg", ".m4a", ".aac", ".opus", ".wma",
})

NOTES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


@dataclass
class Track:
    path: str
    title: str = ""
    bpm: float | None = None
    key: str | None = None
    duration: float = 0.0  # seconds
    beats: list[float] = field(default_factory=list)  # beat positions in seconds
    cue_in: float | None = None  # musically meaningful start point
    cue_out: float | None = None  # musically meaningful end point
    energy: list[float] = field(default_factory=list)  # normalized RMS profile
    waveform: list[float] = field(default_factory=list)  # peak envelope for display
    stems: dict[str, str] = field(default_factory=dict)  # stem_name -> path

    def __post_init__(self):
        if not self.title:
            self.title = Path(self.path).stem

    @property
    def bars(self) -> int:
        """Approximate number of 4-beat bars."""
        return len(self.beats) // 4 if self.beats else 0

    @property
    def playable_duration(self) -> float:
        """Duration between cue points (or full duration)."""
        if self.cue_in is not None and self.cue_out is not None:
            return self.cue_out - self.cue_in
        return self.duration

    def snap_to_beat(self, time_sec: float) -> float:
        """Find the nearest beat position to given time."""
        if not self.beats:
            return time_sec
        closest = min(self.beats, key=lambda b: abs(b - time_sec))
        return closest

    def snap_to_bar(self, time_sec: float) -> float:
        """Find the nearest bar boundary (every 4th beat) to given time."""
        if not self.beats:
            return time_sec
        bar_beats = [self.beats[i] for i in range(0, len(self.beats), 4)]
        return min(bar_beats, key=lambda b: abs(b - time_sec))

    def snap_to_phrase(self, time_sec: float) -> float:
        """Find the nearest phrase boundary (every 16th beat) to given time."""
        if not self.beats:
            return time_sec
        phrase_beats = [self.beats[i] for i in range(0, len(self.beats), 16)]
        return min(phrase_beats, key=lambda b: abs(b - time_sec))

    def nudge_grid(self, offset_sec: float):
        """Shift entire beat grid by offset (positive = later, negative = earlier)."""
        if self.beats:
            self.beats = [round(b + offset_sec, 4) for b in self.beats]

    def stretch_grid(self, anchor_a: int, time_a: float,
                     anchor_b: int, time_b: float):
        """Stretch beat grid so that beat anchor_a lands at time_a and anchor_b at time_b.

        Recalculates BPM from the two anchors and rebuilds a uniform grid.
        """
        if not self.beats or anchor_a == anchor_b:
            return
        if anchor_a < 0 or anchor_b < 0:
            return
        n_beats_between = abs(anchor_b - anchor_a)
        time_span = abs(time_b - time_a)
        if time_span <= 0 or n_beats_between == 0:
            return
        new_interval = time_span / n_beats_between
        new_bpm = round(60.0 / new_interval, 1)
        # Rebuild uniform grid from anchor_a
        first_beat_time = time_a - anchor_a * new_interval
        self.beats = [round(first_beat_time + i * new_interval, 4)
                      for i in range(len(self.beats))]
        self.bpm = new_bpm

    def beat_at(self, time_sec: float) -> int | None:
        """Return the beat index at or just before the given time."""
        if not self.beats:
            return None
        for i, b in enumerate(self.beats):
            if b > time_sec:
                return max(0, i - 1)
        return len(self.beats) - 1

    def bar_at(self, time_sec: float) -> int | None:
        """Return the bar number (0-based) at the given time."""
        beat = self.beat_at(time_sec)
        return beat // 4 if beat is not None else None


@dataclass
class Transition:
    from_track: int  # index in timeline
    to_track: int
    type: Literal["crossfade", "eq_fade", "cut", "echo_out", "filter_sweep"] = "crossfade"
    length_bars: int = 16
    offset_beats: int = 0  # shift transition start by N beats (+ = later)
    tempo_sync: bool = True
    beat_aligned: bool = True
    eq_curves: dict[str, list[float]] = field(default_factory=dict)


@dataclass
class Project:
    name: str = "Untitled Mix"
    library: list[Track] = field(default_factory=list)
    timeline: list[int] = field(default_factory=list)  # indices into library
    transitions: list[Transition] = field(default_factory=list)
    _path: str | None = field(default=None, repr=False)
    _version: int = field(default=0, repr=False)  # bumped on every save

    def save(self, path: str | None = None):
        save_path = Path(path or self._path or "project.json")
        self._path = str(save_path)
        self._version += 1
        lib_data = []
        for t in self.library:
            d = asdict(t)
            # Pack large float arrays as compact base85
            if d.get("waveform"):
                d["waveform"] = _pack_floats(d["waveform"])
            if d.get("energy"):
                d["energy"] = _pack_floats(d["energy"])
            lib_data.append(d)
        data = {
            "name": self.name,
            "version": self._version,
            "saved_at": time.time(),
            "library": lib_data,
            "timeline": self.timeline,
            "transitions": [asdict(t) for t in self.transitions],
        }
        save_path.write_text(json.dumps(data, indent=2, ensure_ascii=False))

    @classmethod
    def load(cls, path: str) -> Project:
        data = json.loads(Path(path).read_text())
        tracks = []
        for t in data.get("library", []):
            # Unpack base85-encoded float arrays
            if isinstance(t.get("waveform"), str):
                t["waveform"] = _unpack_floats(t["waveform"])
            if isinstance(t.get("energy"), str):
                t["energy"] = _unpack_floats(t["energy"])
            tracks.append(Track(**t))
        proj = cls(
            name=data.get("name", "Untitled Mix"),
            library=tracks,
            timeline=data.get("timeline", []),
            transitions=[Transition(**t) for t in data.get("transitions", [])],
        )
        proj._path = path
        proj._version = data.get("version", 0)
        return proj

    def set_bpm(self, track_index: int, new_bpm: float):
        """Set BPM and proportionally rescale beat grid."""
        track = self.library[track_index]
        old_bpm = track.bpm
        track.bpm = round(new_bpm, 1)
        if track.beats and old_bpm and old_bpm > 0:
            ratio = old_bpm / new_bpm
            track.beats = [round(b * ratio, 4) for b in track.beats]

    def add_track(self, path: str, **analysis) -> Track:
        track = Track(path=path, **analysis)
        self.library.append(track)
        return track

    def import_track(self, source_path: str, **analysis) -> Track:
        """Copy audio file into project's tracks/ directory, then add to library."""
        src = Path(source_path).resolve()
        if not src.exists():
            raise FileNotFoundError(f"Audio file not found: {src}")

        tracks_dir = self.project_dir / "tracks"
        tracks_dir.mkdir(exist_ok=True)

        dest = tracks_dir / src.name
        # Avoid overwriting — add suffix if file exists
        if dest.exists() and dest != src:
            stem, suffix = dest.stem, dest.suffix
            n = 1
            while dest.exists():
                dest = tracks_dir / f"{stem}_{n}{suffix}"
                n += 1

        shutil.copy2(str(src), str(dest))
        return self.add_track(str(dest), **analysis)

    def append_to_timeline(self, track_index: int):
        if track_index < 0 or track_index >= len(self.library):
            raise IndexError(f"Track index {track_index} out of range")
        self.timeline.append(track_index)

    def move_timeline_track(self, from_pos: int, to_pos: int):
        """Move a track in the timeline from one position to another."""
        if from_pos < 0 or from_pos >= len(self.timeline):
            raise IndexError(f"Position {from_pos} out of range")
        to_pos = max(0, min(to_pos, len(self.timeline) - 1))
        if from_pos == to_pos:
            return

        # Build position mapping: old_pos -> new_pos
        n = len(self.timeline)
        pos_map: dict[int, int] = {}
        if from_pos < to_pos:
            for i in range(n):
                if i == from_pos:
                    pos_map[i] = to_pos
                elif from_pos < i <= to_pos:
                    pos_map[i] = i - 1
                else:
                    pos_map[i] = i
        else:
            for i in range(n):
                if i == from_pos:
                    pos_map[i] = to_pos
                elif to_pos <= i < from_pos:
                    pos_map[i] = i + 1
                else:
                    pos_map[i] = i

        item = self.timeline.pop(from_pos)
        self.timeline.insert(to_pos, item)
        self._reindex_transitions(pos_map)

    def remove_from_timeline(self, pos: int):
        """Remove a track from the timeline by position."""
        if pos < 0 or pos >= len(self.timeline):
            raise IndexError(f"Position {pos} out of range")

        # Build position mapping: old_pos -> new_pos (removed pos maps to -1)
        n = len(self.timeline)
        pos_map: dict[int, int] = {}
        for i in range(n):
            if i == pos:
                pos_map[i] = -1
            elif i > pos:
                pos_map[i] = i - 1
            else:
                pos_map[i] = i

        self.timeline.pop(pos)
        self._reindex_transitions(pos_map)

    def _reindex_transitions(self, pos_map: dict[int, int]):
        """Reindex transitions after timeline reorder/removal.

        pos_map maps old position -> new position (-1 means removed).
        """
        max_pos = len(self.timeline) - 1
        updated = []
        for t in self.transitions:
            new_from = pos_map.get(t.from_track, -1)
            new_to = pos_map.get(t.to_track, -1)
            if new_from < 0 or new_to < 0:
                continue
            if new_from >= max_pos or new_to > max_pos:
                continue
            # Only keep if they're still adjacent
            if new_to == new_from + 1:
                t.from_track = new_from
                t.to_track = new_to
                updated.append(t)
        self.transitions = updated

    def set_transition(self, from_pos: int, tr_type: str = "crossfade",
                       length_bars: int = 16, **kwargs) -> Transition:
        """Set or update transition between timeline positions."""
        if from_pos < 0 or from_pos >= len(self.timeline) - 1:
            raise IndexError(f"No transition possible at position {from_pos}")
        # Remove existing transition at this position
        self.transitions = [t for t in self.transitions if t.from_track != from_pos]
        t = Transition(from_track=from_pos, to_track=from_pos + 1,
                       type=tr_type, length_bars=length_bars, **kwargs)
        self.transitions.append(t)
        return t

    def get_transition(self, from_pos: int) -> Transition | None:
        """Get transition at timeline position, or None."""
        for t in self.transitions:
            if t.from_track == from_pos:
                return t
        return None

    def add_transition(self, from_idx: int, to_idx: int, **kwargs) -> Transition:
        t = Transition(from_track=from_idx, to_track=to_idx, **kwargs)
        self.transitions.append(t)
        return t

    def get_version(self) -> int:
        return self._version

    def get_path(self) -> str | None:
        return self._path

    @property
    def project_dir(self) -> Path:
        return Path(self._path or "project.json").parent

    def suggest_next(self, limit: int = 5) -> list[tuple[int, Track, float, bool]]:
        """Suggest tracks compatible with the last timeline entry.

        Returns list of (index, track, score, key_compatible).
        """
        if not self.timeline:
            return []
        last = self.library[self.timeline[-1]]
        if not last.bpm or not last.key:
            return []

        compatible = get_compatible_keys(last.key)
        candidates = []
        timeline_set = set(self.timeline)
        for i, t in enumerate(self.library):
            if i in timeline_set or not t.bpm or not t.key:
                continue
            bpm_diff = abs(t.bpm - last.bpm)
            key_ok = t.key in compatible
            score = (10 if key_ok else 0) - bpm_diff * 0.5
            candidates.append((i, t, score, key_ok))

        candidates.sort(key=lambda x: -x[2])
        return candidates[:limit]


# Camelot wheel: maps (root_note, is_minor) -> camelot code
_CAMELOT = {
    ("Ab", True): "1A", ("B", False): "1B",
    ("Eb", True): "2A", ("F#", False): "2B",
    ("Bb", True): "3A", ("Db", False): "3B",
    ("F", True): "4A", ("Ab", False): "4B",
    ("C", True): "5A", ("Eb", False): "5B",
    ("G", True): "6A", ("Bb", False): "6B",
    ("D", True): "7A", ("F", False): "7B",
    ("A", True): "8A", ("C", False): "8B",
    ("E", True): "9A", ("G", False): "9B",
    ("B", True): "10A", ("D", False): "10B",
    ("F#", True): "11A", ("A", False): "11B",
    ("C#", True): "12A", ("E", False): "12B",
}

# Enharmonic aliases for sharp/flat equivalents
_ENHARMONIC = {
    "Db": "C#", "D#": "Eb", "Gb": "F#", "G#": "Ab", "A#": "Bb",
}


def to_camelot(key: str | None) -> str:
    """Convert standard key notation (e.g. 'Am', 'C') to Camelot (e.g. '8A', '8B')."""
    if not key:
        return "—"
    is_minor = key.endswith("m")
    root = key.rstrip("m")
    # Try direct, then enharmonic
    code = _CAMELOT.get((root, is_minor))
    if not code:
        alt = _ENHARMONIC.get(root)
        if alt:
            code = _CAMELOT.get((alt, is_minor))
    return code or "?"


def _normalize_key(key: str) -> str:
    """Normalize a key to sharps-only notation (e.g. 'Bbm' → 'A#m')."""
    is_minor = key.endswith("m")
    root = _normalize_root(key.rstrip("m"))
    if root is None:
        return key
    return root + ("m" if is_minor else "")


def key_compatibility(key_a: str | None, key_b: str | None) -> str:
    """Return compatibility level: 'perfect', 'compatible', 'clash', or 'unknown'.

    Handles enharmonic equivalents (Bb = A#, etc.).
    """
    if not key_a or not key_b:
        return "unknown"
    compatible = get_compatible_keys(key_a)
    # Check both original and normalized form
    norm_b = _normalize_key(key_b)
    if key_b in compatible or norm_b in compatible:
        if key_a == key_b or _normalize_key(key_a) == norm_b:
            return "perfect"
        return "compatible"
    return "clash"


def bpm_compatibility(bpm_a: float | None, bpm_b: float | None) -> str:
    """Return BPM compatibility: 'perfect', 'close', 'far', or 'unknown'."""
    if not bpm_a or not bpm_b:
        return "unknown"
    diff = abs(bpm_a - bpm_b)
    if diff <= 1.0:
        return "perfect"
    if diff <= 5.0:
        return "close"
    return "far"


def get_compatible_keys(key: str) -> set[str]:
    """Return harmonically compatible keys (Camelot wheel neighbors).

    Handles enharmonic equivalents (Bb = A#, etc.).
    """
    is_minor = key.endswith("m")
    root = key.rstrip("m")

    # Normalize enharmonic (flat → sharp)
    if root not in NOTES:
        root = _FLAT_TO_SHARP.get(root) or _ENHARMONIC.get(root)
        if not root or root not in NOTES:
            return {key}

    idx = NOTES.index(root)
    suffix = "m" if is_minor else ""
    # Relative major/minor: minor → +3 semitones (no suffix), major → -3 semitones + "m"
    if is_minor:
        relative = NOTES[(idx + 3) % 12]          # Am → C (relative major)
    else:
        relative = NOTES[(idx - 3) % 12] + "m"    # C → Am (relative minor)
    return {
        key,
        relative,
        NOTES[(idx + 7) % 12] + suffix,           # fifth up
        NOTES[(idx + 5) % 12] + suffix,           # fifth down
    }


# Flat → sharp mapping (NOTES uses sharps)
_FLAT_TO_SHARP = {"Db": "C#", "Eb": "D#", "Gb": "F#", "Ab": "G#", "Bb": "A#"}


def _normalize_root(root: str) -> str | None:
    """Normalize enharmonic root to NOTES spelling (sharps only)."""
    if root in NOTES:
        return root
    return _FLAT_TO_SHARP.get(root)


def key_semitone_distance(key_a: str | None, key_b: str | None) -> int | None:
    """How many semitones to shift key_b so it's compatible with key_a.

    Returns 0 if already compatible, None if keys are unknown,
    or the smallest shift in [-6, +6] to reach the nearest compatible key.
    Handles enharmonic equivalents (Bb = A#, etc.).
    """
    if not key_a or not key_b:
        return None
    if key_b in get_compatible_keys(key_a):
        return 0

    is_minor_b = key_b.endswith("m")
    root_b = _normalize_root(key_b.rstrip("m"))

    if root_b is None:
        return None

    # Try shifting key_b by ±1..6 semitones, find smallest that's compatible
    idx_b = NOTES.index(root_b)
    suffix_b = "m" if is_minor_b else ""

    best_shift = None
    for shift in range(1, 7):
        for sign in (1, -1):
            s = sign * shift
            new_root = NOTES[(idx_b + s) % 12]
            new_key = new_root + suffix_b
            if new_key in get_compatible_keys(key_a):
                if best_shift is None or abs(s) < abs(best_shift):
                    best_shift = s
        if best_shift is not None:
            break
    return best_shift


def find_audio_files(directory: str | Path) -> list[Path]:
    """Recursively find audio files in directory, sorted by name."""
    scan_dir = Path(directory).resolve()
    if not scan_dir.is_dir():
        return []
    return sorted(
        f for f in scan_dir.rglob("*")
        if f.is_file() and f.suffix.lower() in AUDIO_EXTENSIONS
    )
