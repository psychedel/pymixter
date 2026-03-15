"""MCP server for PyMixter — exposes DJ mix operations as tools for AI agents.

Run:
    uv run python -m pymixter.mcp.server [--project project.json]

Implements the Model Context Protocol (MCP) over stdio.
Works with Claude Desktop, Claude Code, and any MCP client.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from pymixter.core.project import (
    Project, Track, Transition, to_camelot,
    key_compatibility, bpm_compatibility, get_compatible_keys,
    key_semitone_distance,
)
from pymixter.core.automix import automix, find_best_order, pick_transition_type


# ── Tool registry ─────────────────────────────────────────────

TOOLS: dict[str, dict] = {}


def tool(name: str, description: str, schema: dict):
    """Decorator to register an MCP tool with its JSON Schema."""
    def decorator(func):
        TOOLS[name] = {
            "name": name,
            "description": description,
            "inputSchema": {"type": "object", "properties": schema.get("properties", {}),
                            "required": schema.get("required", [])},
            "handler": func,
        }
        return func
    return decorator


# ── Session state ─────────────────────────────────────────────

class Session:
    """Holds the current project state for the MCP session."""

    def __init__(self, project_path: str = "project.json"):
        self.project_path = project_path
        self.project: Project | None = None

    def load(self) -> Project:
        p = Path(self.project_path)
        if p.exists():
            self.project = Project.load(self.project_path)
        else:
            self.project = Project(_path=self.project_path)
        return self.project

    def ensure_loaded(self) -> Project:
        if self.project is None:
            return self.load()
        return self.project

    def save(self):
        if self.project:
            self.project.save(self.project_path)


SESSION = Session()


# ── Helpers ───────────────────────────────────────────────────

def _track_summary(idx: int, t: Track) -> dict:
    """Compact track representation for agent consumption."""
    return {
        "index": idx,
        "title": t.title,
        "bpm": t.bpm,
        "key": t.key,
        "camelot": to_camelot(t.key),
        "duration_sec": round(t.duration, 1) if t.duration else None,
        "bars": t.bars,
        "cue_in": t.cue_in,
        "cue_out": t.cue_out,
        "playable_sec": round(t.playable_duration, 1),
        "has_beats": bool(t.beats),
        "has_energy": bool(t.energy),
        "has_waveform": bool(t.waveform),
        "has_stems": bool(t.stems),
        "energy_avg": round(sum(t.energy) / len(t.energy), 3) if t.energy else None,
        "lufs": t.lufs,
        "replay_gain": t.replay_gain,
        "danceability": t.danceability,
        "dynamic_complexity": t.dynamic_complexity,
        "has_onsets": bool(t.onsets),
        "has_chords": bool(t.chords),
        "fade_in_end": t.fade_in_end,
        "fade_out_start": t.fade_out_start,
        "path": t.path,
    }


def _transition_summary(tr: Transition, proj: Project) -> dict:
    """Transition details with track context."""
    result = {
        "from_pos": tr.from_track,
        "to_pos": tr.to_track,
        "type": tr.type,
        "length_bars": tr.length_bars,
        "offset_beats": tr.offset_beats,
        "tempo_sync": tr.tempo_sync,
        "beat_aligned": tr.beat_aligned,
    }
    # Add track titles for context
    if tr.from_track < len(proj.timeline) and tr.to_track < len(proj.timeline):
        idx_a = proj.timeline[tr.from_track]
        idx_b = proj.timeline[tr.to_track]
        if idx_a < len(proj.library):
            result["from_title"] = proj.library[idx_a].title
        if idx_b < len(proj.library):
            result["to_title"] = proj.library[idx_b].title
    return result


def _error(msg: str) -> dict:
    return {"error": msg}


def _apply_analysis(track: Track, analysis: dict, full: bool = True):
    """Apply analysis results to a track. Only overwrites with non-None values."""
    if analysis.get("bpm") is not None:
        track.bpm = analysis["bpm"]
    if analysis.get("key") is not None:
        track.key = analysis["key"]
    if analysis.get("duration"):
        track.duration = analysis["duration"]
    if analysis.get("replay_gain") is not None:
        track.replay_gain = analysis["replay_gain"]
    if full:
        if analysis.get("beats"):
            track.beats = analysis["beats"]
        if analysis.get("cue_in") is not None:
            track.cue_in = analysis["cue_in"]
        if analysis.get("cue_out") is not None:
            track.cue_out = analysis["cue_out"]
        if analysis.get("energy"):
            track.energy = analysis["energy"]
        if analysis.get("waveform"):
            track.waveform = analysis["waveform"]
        if analysis.get("lufs") is not None:
            track.lufs = analysis["lufs"]
        if analysis.get("danceability") is not None:
            track.danceability = analysis["danceability"]
        if analysis.get("dynamic_complexity") is not None:
            track.dynamic_complexity = analysis["dynamic_complexity"]
        if analysis.get("onsets"):
            track.onsets = analysis["onsets"]
        if analysis.get("fade_in_end") is not None:
            track.fade_in_end = analysis["fade_in_end"]
        if analysis.get("fade_out_start") is not None:
            track.fade_out_start = analysis["fade_out_start"]
        if analysis.get("chords"):
            track.chords = analysis["chords"]


# ── Project tools ─────────────────────────────────────────────

@tool("project_open", "Open or create a project file. Returns project summary.", {
    "properties": {
        "path": {"type": "string", "description": "Path to project.json file"}
    },
})
def project_open(params: dict) -> dict:
    path = params.get("path", "project.json")
    SESSION.project_path = path
    proj = SESSION.load()
    return {
        "name": proj.name,
        "path": path,
        "library_count": len(proj.library),
        "timeline_count": len(proj.timeline),
        "transition_count": len(proj.transitions),
        "version": proj.get_version(),
    }


@tool("project_info", "Get detailed project state: library, timeline, transitions, and mix stats.", {})
def project_info(params: dict) -> dict:
    proj = SESSION.ensure_loaded()
    timeline_tracks = []
    for pos, tidx in enumerate(proj.timeline):
        if tidx < len(proj.library):
            t = proj.library[tidx]
            timeline_tracks.append({
                "position": pos,
                "library_index": tidx,
                "title": t.title,
                "bpm": t.bpm,
                "key": t.key,
                "camelot": to_camelot(t.key),
            })

    # Mix duration estimate
    total_dur = sum(
        proj.library[i].playable_duration
        for i in proj.timeline if i < len(proj.library)
    )

    return {
        "name": proj.name,
        "library_count": len(proj.library),
        "timeline": timeline_tracks,
        "transitions": [_transition_summary(tr, proj) for tr in proj.transitions],
        "estimated_duration_sec": round(total_dur, 1),
        "version": proj.get_version(),
    }


# ── Library tools ─────────────────────────────────────────────

@tool("library_list", "List all tracks with metadata. Returns array of track summaries.", {})
def library_list(params: dict) -> dict:
    proj = SESSION.ensure_loaded()
    return {
        "tracks": [_track_summary(i, t) for i, t in enumerate(proj.library)],
        "total": len(proj.library),
    }


@tool("library_scan", "Scan a directory for audio files and import them into the library.", {
    "properties": {
        "directory": {"type": "string", "description": "Directory path to scan for audio files"},
        "analyze": {"type": "boolean", "description": "Run full analysis (BPM, key, beats) on each track. Slow but recommended.", "default": False},
    },
    "required": ["directory"],
})
def library_scan(params: dict) -> dict:
    from pymixter.core.project import find_audio_files
    proj = SESSION.ensure_loaded()
    directory = params["directory"]
    do_analyze = params.get("analyze", False)

    files = find_audio_files(directory)
    if not files:
        return {"imported": 0, "message": f"No audio files found in {directory}"}

    imported = []
    for f in files:
        analysis = {}
        if do_analyze:
            try:
                from pymixter.core.analysis import analyze_track
                analysis = analyze_track(str(f), full=True)
            except Exception as e:
                analysis = {"_error": str(e)}

        track = proj.import_track(str(f), **{k: v for k, v in analysis.items() if not k.startswith("_")})
        idx = len(proj.library) - 1
        imported.append(_track_summary(idx, track))

    SESSION.save()
    return {"imported": len(imported), "tracks": imported}


@tool("track_analyze", "Run audio analysis on a track: detects BPM, key, beats, cue points, energy profile.", {
    "properties": {
        "index": {"type": "integer", "description": "Track index in library"},
        "full": {"type": "boolean", "description": "Full analysis including beats, cue points, energy, waveform", "default": True},
    },
    "required": ["index"],
})
def track_analyze(params: dict) -> dict:
    from pymixter.core.analysis import analyze_track
    proj = SESSION.ensure_loaded()
    idx = params["index"]
    if idx < 0 or idx >= len(proj.library):
        return _error(f"Track index {idx} out of range (0–{len(proj.library)-1})")

    track = proj.library[idx]
    full = params.get("full", True)
    try:
        analysis = analyze_track(track.path, full=full)
    except Exception as e:
        return _error(f"Analysis failed for '{track.title}': {e}")
    _apply_analysis(track, analysis, full=full)
    SESSION.save()
    return _track_summary(idx, track)


@tool("track_analyze_all", "Analyze all tracks that haven't been analyzed yet. Returns summary of results.", {
    "properties": {
        "full": {"type": "boolean", "description": "Full analysis", "default": True},
    },
})
def track_analyze_all(params: dict) -> dict:
    from pymixter.core.analysis import analyze_track
    proj = SESSION.ensure_loaded()
    full = params.get("full", True)
    results = []
    errors = []

    for i, track in enumerate(proj.library):
        if track.bpm and track.key and (not full or track.beats):
            continue
        try:
            analysis = analyze_track(track.path, full=full)
            _apply_analysis(track, analysis, full=full)
            results.append({"index": i, "title": track.title, "bpm": track.bpm, "key": track.key})
        except Exception as e:
            errors.append({"index": i, "title": track.title, "error": str(e)})

    SESSION.save()
    return {"analyzed": len(results), "results": results, "errors": errors}


# ── Track detail tools ────────────────────────────────────────

@tool("track_info", "Get full details of a single track including energy profile and beat grid stats.", {
    "properties": {
        "index": {"type": "integer", "description": "Track index in library"},
    },
    "required": ["index"],
})
def track_info(params: dict) -> dict:
    proj = SESSION.ensure_loaded()
    idx = params["index"]
    if idx < 0 or idx >= len(proj.library):
        return _error(f"Track index {idx} out of range")
    t = proj.library[idx]
    info = _track_summary(idx, t)

    # Add energy profile (downsampled to 16 buckets for the agent)
    if t.energy:
        n = len(t.energy)
        buckets = 16
        info["energy_profile"] = [
            round(sum(t.energy[i*n//buckets:(i+1)*n//buckets]) /
                  max(1, len(t.energy[i*n//buckets:(i+1)*n//buckets])), 3)
            for i in range(buckets)
        ]

    # Beat grid stats
    if t.beats and len(t.beats) > 1:
        intervals = [t.beats[i+1] - t.beats[i] for i in range(len(t.beats)-1)]
        avg = sum(intervals) / len(intervals)
        info["grid_bpm"] = round(60.0 / avg, 1) if avg > 0 else None
        info["grid_first_beat"] = t.beats[0]
        info["beat_count"] = len(t.beats)

    if t.stems:
        info["stems"] = list(t.stems.keys())

    return info


@tool("track_set_cue", "Set cue-in and/or cue-out points for a track. Supports snap to beat/bar/phrase.", {
    "properties": {
        "index": {"type": "integer", "description": "Track index"},
        "cue_in": {"type": "number", "description": "Cue-in time in seconds (or null to keep current)"},
        "cue_out": {"type": "number", "description": "Cue-out time in seconds (or null to keep current)"},
        "snap": {"type": "string", "enum": ["beat", "bar", "phrase"], "description": "Snap cue points to nearest grid position"},
    },
    "required": ["index"],
})
def track_set_cue(params: dict) -> dict:
    proj = SESSION.ensure_loaded()
    idx = params["index"]
    if idx < 0 or idx >= len(proj.library):
        return _error(f"Track index {idx} out of range")
    t = proj.library[idx]

    if "cue_in" in params and params["cue_in"] is not None:
        t.cue_in = params["cue_in"]
    if "cue_out" in params and params["cue_out"] is not None:
        t.cue_out = params["cue_out"]

    snap = params.get("snap")
    if snap and t.beats:
        if t.cue_in is not None:
            if snap == "bar":
                t.cue_in = t.snap_to_bar(t.cue_in)
            elif snap == "phrase":
                t.cue_in = t.snap_to_phrase(t.cue_in)
            else:
                t.cue_in = t.snap_to_beat(t.cue_in)
        if t.cue_out is not None:
            if snap == "bar":
                t.cue_out = t.snap_to_bar(t.cue_out)
            elif snap == "phrase":
                t.cue_out = t.snap_to_phrase(t.cue_out)
            else:
                t.cue_out = t.snap_to_beat(t.cue_out)

    SESSION.save()
    return {
        "index": idx, "title": t.title,
        "cue_in": t.cue_in, "cue_out": t.cue_out,
        "playable_sec": round(t.playable_duration, 1),
    }


@tool("track_set_bpm", "Set or adjust BPM for a track. Optionally halve/double. Rescales beat grid. Can also set key.", {
    "properties": {
        "index": {"type": "integer", "description": "Track index"},
        "bpm": {"type": "number", "description": "Exact BPM value to set"},
        "halve": {"type": "boolean", "description": "Halve the current BPM"},
        "double": {"type": "boolean", "description": "Double the current BPM"},
        "key": {"type": "string", "description": "Set musical key (e.g. 'Am', 'C#', 'Fm')"},
    },
    "required": ["index"],
})
def track_set_bpm(params: dict) -> dict:
    proj = SESSION.ensure_loaded()
    idx = params["index"]
    if idx < 0 or idx >= len(proj.library):
        return _error(f"Track index {idx} out of range")
    t = proj.library[idx]
    old_bpm = t.bpm

    if "bpm" in params and params["bpm"] is not None:
        proj.set_bpm(idx, params["bpm"])
    elif params.get("halve") and t.bpm:
        proj.set_bpm(idx, t.bpm / 2)
    elif params.get("double") and t.bpm:
        proj.set_bpm(idx, t.bpm * 2)

    if "key" in params and params["key"] is not None:
        t.key = params["key"]

    SESSION.save()
    return {
        "index": idx, "title": t.title,
        "bpm_before": old_bpm, "bpm_after": t.bpm,
        "key": t.key, "camelot": to_camelot(t.key),
    }


@tool("track_grid_nudge", "Shift the entire beat grid by milliseconds. Use for fine beat alignment.", {
    "properties": {
        "index": {"type": "integer", "description": "Track index"},
        "offset_ms": {"type": "number", "description": "Offset in milliseconds (positive = later, negative = earlier)"},
    },
    "required": ["index", "offset_ms"],
})
def track_grid_nudge(params: dict) -> dict:
    proj = SESSION.ensure_loaded()
    idx = params["index"]
    if idx < 0 or idx >= len(proj.library):
        return _error(f"Track index {idx} out of range")
    t = proj.library[idx]
    if not t.beats:
        return _error("No beat grid — run track_analyze first")

    t.nudge_grid(params["offset_ms"] / 1000.0)
    SESSION.save()
    return {"index": idx, "offset_ms": params["offset_ms"], "first_beat": t.beats[0]}


@tool("track_grid_stretch", "Stretch beat grid using two anchor points. Recalculates BPM. Use when detected BPM drifts.", {
    "properties": {
        "index": {"type": "integer", "description": "Track index"},
        "beat_a": {"type": "integer", "description": "First anchor beat index"},
        "time_a": {"type": "number", "description": "Time in seconds where beat_a should land"},
        "beat_b": {"type": "integer", "description": "Second anchor beat index"},
        "time_b": {"type": "number", "description": "Time in seconds where beat_b should land"},
    },
    "required": ["index", "beat_a", "time_a", "beat_b", "time_b"],
})
def track_grid_stretch(params: dict) -> dict:
    proj = SESSION.ensure_loaded()
    idx = params["index"]
    if idx < 0 or idx >= len(proj.library):
        return _error(f"Track index {idx} out of range")
    t = proj.library[idx]
    if not t.beats:
        return _error("No beat grid — run track_analyze first")

    old_bpm = t.bpm
    t.stretch_grid(params["beat_a"], params["time_a"],
                   params["beat_b"], params["time_b"])
    SESSION.save()
    return {"index": idx, "bpm_before": old_bpm, "bpm_after": t.bpm}


# ── Timeline tools ────────────────────────────────────────────

@tool("timeline_append", "Add a track to the end of the timeline.", {
    "properties": {
        "index": {"type": "integer", "description": "Library index of the track to add"},
    },
    "required": ["index"],
})
def timeline_append(params: dict) -> dict:
    proj = SESSION.ensure_loaded()
    idx = params["index"]
    proj.append_to_timeline(idx)
    SESSION.save()
    pos = len(proj.timeline) - 1
    t = proj.library[idx]
    return {"position": pos, "index": idx, "title": t.title, "timeline_length": len(proj.timeline)}


@tool("timeline_remove", "Remove a track from the timeline by position. Reindexes transitions.", {
    "properties": {
        "position": {"type": "integer", "description": "Timeline position to remove (0-based)"},
    },
    "required": ["position"],
})
def timeline_remove(params: dict) -> dict:
    proj = SESSION.ensure_loaded()
    proj.remove_from_timeline(params["position"])
    SESSION.save()
    return {"removed_position": params["position"], "timeline_length": len(proj.timeline)}


@tool("timeline_reorder", "Move a track from one timeline position to another.", {
    "properties": {
        "from_pos": {"type": "integer", "description": "Current position"},
        "to_pos": {"type": "integer", "description": "Target position"},
    },
    "required": ["from_pos", "to_pos"],
})
def timeline_reorder(params: dict) -> dict:
    proj = SESSION.ensure_loaded()
    proj.move_timeline_track(params["from_pos"], params["to_pos"])
    SESSION.save()
    return {
        "moved": f"{params['from_pos']} → {params['to_pos']}",
        "timeline": [
            {"pos": i, "index": tidx, "title": proj.library[tidx].title}
            for i, tidx in enumerate(proj.timeline) if tidx < len(proj.library)
        ],
    }


# ── Transition tools ──────────────────────────────────────────

@tool("transition_set", "Set or update a transition between two consecutive timeline tracks.", {
    "properties": {
        "position": {"type": "integer", "description": "Timeline position of the outgoing track (from_track)"},
        "type": {"type": "string", "enum": ["crossfade", "eq_fade", "cut", "echo_out", "filter_sweep"],
                 "description": "Transition type. crossfade=smooth blend, eq_fade=bass swap, cut=instant switch, echo_out=echo trail, filter_sweep=resonant LP/HP sweep"},
        "bars": {"type": "integer", "description": "Transition length in bars (4 beats each). Typical: 8–32"},
        "offset_beats": {"type": "integer", "description": "Shift transition start point by N beats (+ = later, - = earlier)"},
    },
    "required": ["position"],
})
def transition_set(params: dict) -> dict:
    proj = SESSION.ensure_loaded()
    pos = params["position"]

    # Preserve existing values if not explicitly provided
    existing = proj.get_transition(pos)
    tr_type = params.get("type", existing.type if existing else "crossfade")
    bars = params.get("bars", existing.length_bars if existing else 16)
    offset = params.get("offset_beats", existing.offset_beats if existing else 0)

    tr = proj.set_transition(pos, tr_type, bars)
    tr.offset_beats = offset

    SESSION.save()
    return _transition_summary(tr, proj)


@tool("transition_list", "List all transitions with full details.", {})
def transition_list(params: dict) -> dict:
    proj = SESSION.ensure_loaded()
    return {
        "transitions": [_transition_summary(tr, proj) for tr in proj.transitions],
        "total": len(proj.transitions),
    }


# ── Mix intelligence tools ────────────────────────────────────

@tool("mix_compatibility_matrix",
      "Get BPM and key compatibility between all analyzed tracks. Essential for planning track order.",
      {})
def mix_compatibility_matrix(params: dict) -> dict:
    proj = SESSION.ensure_loaded()
    analyzed = [(i, t) for i, t in enumerate(proj.library) if t.bpm and t.key]
    if len(analyzed) < 2:
        return _error("Need at least 2 analyzed tracks")

    matrix = []
    for i, t_a in analyzed:
        row = {"index": i, "title": t_a.title, "bpm": t_a.bpm, "key": t_a.key,
               "danceability": t_a.danceability, "lufs": t_a.lufs,
               "compatible_with": []}
        for j, t_b in analyzed:
            if i == j:
                continue
            kc = key_compatibility(t_a.key, t_b.key)
            bc = bpm_compatibility(t_a.bpm, t_b.bpm)
            bpm_diff = round(abs(t_a.bpm - t_b.bpm), 1)
            shift = key_semitone_distance(t_a.key, t_b.key)
            if kc in ("perfect", "compatible") and bc in ("perfect", "close"):
                row["compatible_with"].append({
                    "index": j, "title": t_b.title,
                    "key_compat": kc, "bpm_compat": bc, "bpm_diff": bpm_diff,
                })
            elif bc in ("perfect", "close") and shift is not None and abs(shift) <= 2:
                # Not naturally compatible, but close enough to pitch-shift
                row["compatible_with"].append({
                    "index": j, "title": t_b.title,
                    "key_compat": "pitch_shift", "bpm_compat": bc,
                    "bpm_diff": bpm_diff, "pitch_shift_semitones": shift,
                })
        matrix.append(row)
    return {"matrix": matrix}


@tool("mix_suggest_next",
      "Suggest the best next track to add to the timeline based on harmonic compatibility and BPM.",
      {"properties": {"limit": {"type": "integer", "description": "Max suggestions", "default": 5}}})
def mix_suggest_next(params: dict) -> dict:
    proj = SESSION.ensure_loaded()
    limit = params.get("limit", 5)
    candidates = proj.suggest_next(limit=limit)
    if not candidates:
        return {"suggestions": [], "message": "No suggestions — need analyzed tracks in timeline"}

    last_idx = proj.timeline[-1]
    last = proj.library[last_idx]
    return {
        "after_track": {"index": last_idx, "title": last.title, "bpm": last.bpm, "key": last.key},
        "suggestions": [
            {"index": i, "title": t.title, "bpm": t.bpm, "key": t.key,
             "camelot": to_camelot(t.key), "score": round(score, 1),
             "key_compatible": key_ok,
             "bpm_diff": round(abs(t.bpm - last.bpm), 1) if t.bpm and last.bpm else None,
             "danceability": t.danceability, "lufs": t.lufs}
            for i, t, score, key_ok in candidates
        ],
    }


@tool("mix_suggest_order",
      "Compute the optimal track order for a set of tracks using harmonic mixing rules. "
      "Uses Camelot wheel key compatibility, BPM proximity, and energy flow.",
      {
          "properties": {
              "track_indices": {
                  "type": "array", "items": {"type": "integer"},
                  "description": "Library indices to arrange. Omit for all analyzed tracks.",
              },
              "start_index": {"type": "integer", "description": "Library index to start the mix from"},
          },
      })
def mix_suggest_order(params: dict) -> dict:
    proj = SESSION.ensure_loaded()
    indices = params.get("track_indices")
    start = params.get("start_index")

    if indices:
        tracks = [(i, proj.library[i]) for i in indices if i < len(proj.library)]
    else:
        tracks = [(i, t) for i, t in enumerate(proj.library) if t.bpm and t.key]

    if not tracks:
        return _error("No analyzed tracks to arrange")

    order = find_best_order(tracks, start_idx=start)

    result = []
    for pos, lib_idx in enumerate(order):
        t = proj.library[lib_idx]
        entry = {"position": pos, "index": lib_idx, "title": t.title,
                 "bpm": t.bpm, "key": t.key, "camelot": to_camelot(t.key)}
        if pos > 0:
            prev = proj.library[order[pos - 1]]
            tr_type, tr_bars = pick_transition_type(prev, t)
            entry["transition_from_prev"] = {
                "key_compat": key_compatibility(prev.key, t.key),
                "bpm_diff": round(abs(prev.bpm - t.bpm), 1) if prev.bpm and t.bpm else None,
                "suggested_type": tr_type,
                "suggested_bars": tr_bars,
            }
        result.append(entry)

    return {"order": result, "track_count": len(result)}


@tool("mix_automix",
      "Automatically build the full mix: order tracks, set transitions. Replaces current timeline.",
      {
          "properties": {
              "track_indices": {"type": "array", "items": {"type": "integer"},
                                "description": "Which tracks to include (omit for all analyzed)"},
              "start_index": {"type": "integer", "description": "Library index to start from"},
          },
      })
def mix_automix(params: dict) -> dict:
    proj = SESSION.ensure_loaded()
    indices = params.get("track_indices")
    start = params.get("start_index")

    order = automix(proj, track_indices=indices, start_idx=start)
    if not order:
        return _error("No analyzed tracks — run track_analyze_all first")

    SESSION.save()
    return {
        "timeline": [
            {"pos": i, "index": tidx, "title": proj.library[tidx].title}
            for i, tidx in enumerate(proj.timeline)
        ],
        "transitions": [_transition_summary(tr, proj) for tr in proj.transitions],
        "track_count": len(order),
    }


@tool("mix_energy_profile",
      "Get the energy curve across the entire timeline. Useful for checking if the mix builds properly.",
      {})
def mix_energy_profile(params: dict) -> dict:
    proj = SESSION.ensure_loaded()
    if not proj.timeline:
        return _error("Timeline is empty")

    profile = []
    for pos, tidx in enumerate(proj.timeline):
        if tidx >= len(proj.library):
            continue
        t = proj.library[tidx]
        if t.energy:
            avg = round(sum(t.energy) / len(t.energy), 3)
            peak = round(max(t.energy), 3)
            # Energy in quarters
            n = len(t.energy)
            quarters = [
                round(sum(t.energy[i*n//4:(i+1)*n//4]) / max(1, n//4), 3)
                for i in range(4)
            ]
        else:
            avg = peak = None
            quarters = []

        profile.append({
            "position": pos,
            "index": tidx,
            "title": t.title,
            "energy_avg": avg,
            "energy_peak": peak,
            "energy_quarters": quarters,
            "danceability": t.danceability,
            "lufs": t.lufs,
            "dynamic_complexity": t.dynamic_complexity,
        })

    return {"profile": profile}


@tool("mix_validate",
      "Check the current timeline for issues: key clashes, BPM jumps, missing transitions, missing analysis.",
      {})
def mix_validate(params: dict) -> dict:
    proj = SESSION.ensure_loaded()
    if not proj.timeline:
        return {"warnings": [], "ok": True, "message": "Timeline is empty"}

    warnings = []

    for pos in range(len(proj.timeline) - 1):
        idx_a = proj.timeline[pos]
        idx_b = proj.timeline[pos + 1]
        if idx_a >= len(proj.library) or idx_b >= len(proj.library):
            continue
        a = proj.library[idx_a]
        b = proj.library[idx_b]

        # Check for missing analysis
        if not a.bpm or not a.key:
            warnings.append({"position": pos, "type": "missing_analysis", "track": a.title})
        if not b.bpm or not b.key:
            warnings.append({"position": pos + 1, "type": "missing_analysis", "track": b.title})

        # Check key compatibility
        if a.key and b.key:
            kc = key_compatibility(a.key, b.key)
            if kc == "clash":
                shift = key_semitone_distance(a.key, b.key)
                suggestion = "Use echo_out or cut transition to mask key clash"
                if shift is not None and abs(shift) <= 2:
                    suggestion = f"Pitch-shift by {shift:+d} semitones, or use echo_out/cut"
                warnings.append({
                    "position": pos, "type": "key_clash",
                    "from": f"{a.title} ({a.key})", "to": f"{b.title} ({b.key})",
                    "pitch_shift_semitones": shift,
                    "suggestion": suggestion,
                })

        # Check BPM difference
        if a.bpm and b.bpm:
            diff = abs(a.bpm - b.bpm)
            if diff > 8:
                warnings.append({
                    "position": pos, "type": "bpm_jump",
                    "from_bpm": a.bpm, "to_bpm": b.bpm, "diff": round(diff, 1),
                    "suggestion": "Consider a cut transition or reorder tracks",
                })

        # Check LUFS difference (loudness mismatch)
        if a.lufs is not None and b.lufs is not None:
            lufs_diff = abs(a.lufs - b.lufs)
            if lufs_diff > 6:
                warnings.append({
                    "position": pos, "type": "loudness_mismatch",
                    "from": f"{a.title} ({a.lufs:.1f} LUFS)",
                    "to": f"{b.title} ({b.lufs:.1f} LUFS)",
                    "diff_db": round(lufs_diff, 1),
                    "suggestion": "Re-analyze with --full for ReplayGain normalization",
                })

        # Check for missing transition
        tr = proj.get_transition(pos)
        if tr is None:
            warnings.append({
                "position": pos, "type": "missing_transition",
                "from": a.title, "to": b.title,
            })

    return {"warnings": warnings, "ok": len(warnings) == 0}


@tool("mix_render",
      "Render the timeline to an audio file. Returns the output file path.",
      {
          "properties": {
              "output": {"type": "string", "description": "Output file path (.wav, .mp3, .flac)",
                         "default": "mix_output.wav"},
          },
      })
def mix_render(params: dict) -> dict:
    from pymixter.core.mixer import render_to_file
    proj = SESSION.ensure_loaded()
    if not proj.timeline:
        return _error("Timeline is empty")

    output = params.get("output", "mix_output.wav")
    path = render_to_file(proj, output)

    from pedalboard.io import AudioFile
    with AudioFile(path) as f:
        dur = f.frames / f.samplerate

    return {"path": path, "duration_sec": round(dur, 1), "tracks": len(proj.timeline)}


# ── MCP Protocol Implementation ──────────────────────────────

def handle_request(request: dict) -> dict:
    """Handle a single JSON-RPC 2.0 request."""
    method = request.get("method", "")
    req_id = request.get("id")
    params = request.get("params", {})

    if method == "initialize":
        return _jsonrpc_result(req_id, {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "pymixter", "version": "0.1.0"},
        })

    if method == "notifications/initialized":
        return None  # No response for notifications

    if method == "tools/list":
        tools_list = []
        for name, tool_def in TOOLS.items():
            tools_list.append({
                "name": tool_def["name"],
                "description": tool_def["description"],
                "inputSchema": tool_def["inputSchema"],
            })
        return _jsonrpc_result(req_id, {"tools": tools_list})

    if method == "tools/call":
        tool_name = params.get("name", "")
        tool_args = params.get("arguments", {})
        tool_def = TOOLS.get(tool_name)
        if not tool_def:
            return _jsonrpc_error(req_id, -32601, f"Unknown tool: {tool_name}")
        try:
            result = tool_def["handler"](tool_args)
            return _jsonrpc_result(req_id, {
                "content": [{"type": "text", "text": json.dumps(result, indent=2)}],
            })
        except Exception as e:
            return _jsonrpc_result(req_id, {
                "content": [{"type": "text", "text": json.dumps({"error": str(e)})}],
                "isError": True,
            })

    return _jsonrpc_error(req_id, -32601, f"Unknown method: {method}")


def _jsonrpc_result(req_id, result):
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _jsonrpc_error(req_id, code, message):
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


def run_stdio():
    """Run MCP server over stdio (JSON-RPC 2.0, one message per line)."""
    import argparse
    parser = argparse.ArgumentParser(description="PyMixter MCP Server")
    parser.add_argument("--project", default="project.json", help="Project file path")
    args = parser.parse_args()

    SESSION.project_path = args.project

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            continue

        response = handle_request(request)
        if response is not None:
            sys.stdout.write(json.dumps(response) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    run_stdio()
