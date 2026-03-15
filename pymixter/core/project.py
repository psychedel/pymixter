"""Shared project state — the single source of truth for both TUI and CLI."""

from __future__ import annotations

import json
import shutil
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Literal

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
        data = {
            "name": self.name,
            "version": self._version,
            "saved_at": time.time(),
            "library": [asdict(t) for t in self.library],
            "timeline": self.timeline,
            "transitions": [asdict(t) for t in self.transitions],
        }
        save_path.write_text(json.dumps(data, indent=2, ensure_ascii=False))

    @classmethod
    def load(cls, path: str) -> Project:
        data = json.loads(Path(path).read_text())
        proj = cls(
            name=data.get("name", "Untitled Mix"),
            library=[Track(**t) for t in data.get("library", [])],
            timeline=data.get("timeline", []),
            transitions=[Transition(**t) for t in data.get("transitions", [])],
        )
        proj._path = path
        proj._version = data.get("version", 0)
        return proj

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
        item = self.timeline.pop(from_pos)
        self.timeline.insert(to_pos, item)
        self._rebuild_transitions()

    def remove_from_timeline(self, pos: int):
        """Remove a track from the timeline by position."""
        if pos < 0 or pos >= len(self.timeline):
            raise IndexError(f"Position {pos} out of range")
        self.timeline.pop(pos)
        self._rebuild_transitions()

    def _rebuild_transitions(self):
        """Remove transitions that reference invalid positions."""
        max_pos = len(self.timeline) - 1
        self.transitions = [
            t for t in self.transitions
            if t.from_track < max_pos and t.to_track <= max_pos
        ]

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
