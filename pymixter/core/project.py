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


@dataclass
class Transition:
    from_track: int  # index in timeline
    to_track: int
    type: Literal["crossfade", "eq_fade", "cut", "echo_out"] = "crossfade"
    length_bars: int = 16
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


def get_compatible_keys(key: str) -> set[str]:
    """Return harmonically compatible keys (Camelot wheel neighbors)."""
    is_minor = key.endswith("m")
    root = key.rstrip("m")

    if root not in NOTES:
        return {key}

    idx = NOTES.index(root)
    suffix = "m" if is_minor else ""
    return {
        key,
        root if is_minor else root + "m",       # relative major/minor
        NOTES[(idx + 7) % 12] + suffix,          # fifth up
        NOTES[(idx + 5) % 12] + suffix,          # fifth down
    }


def find_audio_files(directory: str | Path) -> list[Path]:
    """Recursively find audio files in directory, sorted by name."""
    scan_dir = Path(directory).resolve()
    if not scan_dir.is_dir():
        return []
    return sorted(
        f for f in scan_dir.rglob("*")
        if f.is_file() and f.suffix.lower() in AUDIO_EXTENSIONS
    )
