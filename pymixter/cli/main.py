"""CLI interface — used by Claude to control the project programmatically.

Usage:
    uv run python -m mix.cli.main <command> [args...]

Commands:
    init <name>                     Create new project
    add <audio_file> [--analyze]    Add track to library
    scan <directory> [--analyze]    Import all audio from directory
    analyze <track_index> [--full]  Analyze a track (BPM, key, beats, energy)
    analyze-all [--full]            Analyze all unanalyzed tracks
    play <track_index>              Play a track (blocking)
    timeline append <track_index>   Add track to timeline
    timeline show                   Show current timeline
    transition add <from> <to> [--type crossfade] [--bars 16]
    library                         List all tracks in library
    info                            Show project summary
    suggest                         Suggest next track based on key/BPM compatibility
    automix [--tracks 0,1,2] [--start 0]  Auto-arrange tracks into a mix
"""

from __future__ import annotations

import argparse
import json
import signal
import sys
from pathlib import Path

from pymixter.core.project import Project, find_audio_files
from pymixter.core.analysis import analyze_track


DEFAULT_PROJECT = "project.json"


def get_project(path: str = DEFAULT_PROJECT) -> Project:
    p = Path(path)
    if p.exists():
        return Project.load(path)
    return Project(_path=path)


def cmd_init(args):
    proj = Project(name=args.name)
    proj.save(args.project)
    print(f"Created project: {args.name} -> {args.project}")


def cmd_add(args):
    proj = get_project(args.project)
    audio_path = str(Path(args.file).resolve())
    if not Path(audio_path).exists():
        print(f"File not found: {audio_path}", file=sys.stderr)
        sys.exit(1)

    analysis = {}
    if args.analyze:
        print(f"Analyzing {audio_path}...")
        try:
            analysis = analyze_track(audio_path, full=True)
        except Exception as e:
            print(f"Analysis failed: {e}", file=sys.stderr)

    track = proj.import_track(audio_path, **analysis)
    print(f"Imported: {track.title} -> {track.path}")
    if args.analyze:
        print(f"  BPM: {track.bpm}  Key: {track.key}  Duration: {track.duration}s")
        if track.bars:
            print(f"  Bars: {track.bars}  Cue: {track.cue_in}s -> {track.cue_out}s")

    proj.save()
    print(f"Track index: {len(proj.library) - 1}")


def cmd_scan(args):
    """Import all audio files from a directory."""
    proj = get_project(args.project)
    files = find_audio_files(args.directory)

    if not files:
        print(f"No audio files found in {args.directory}")
        return

    print(f"Found {len(files)} audio files")
    for f in files:
        analysis = {}
        if args.analyze:
            print(f"  Analyzing {f.name}...")
            try:
                analysis = analyze_track(str(f), full=True)
            except Exception as e:
                print(f"    Analysis failed: {e}", file=sys.stderr)

        track = proj.import_track(str(f), **analysis)
        bpm_str = f" BPM:{track.bpm}" if track.bpm else ""
        key_str = f" Key:{track.key}" if track.key else ""
        print(f"  + {track.title}{bpm_str}{key_str}")

    proj.save()
    print(f"\nImported {len(files)} tracks. Library now has {len(proj.library)} tracks.")


def cmd_analyze(args):
    proj = get_project(args.project)
    idx = args.index
    if idx >= len(proj.library):
        print(f"Track index {idx} out of range", file=sys.stderr)
        sys.exit(1)

    track = proj.library[idx]
    print(f"Analyzing {track.path}...")
    try:
        analysis = analyze_track(track.path, full=args.full)
    except Exception as e:
        print(f"Analysis failed: {e}", file=sys.stderr)
        sys.exit(1)

    track.bpm = analysis["bpm"]
    track.key = analysis["key"]
    track.duration = analysis["duration"]
    track.replay_gain = analysis.get("replay_gain")
    if args.full:
        track.beats = analysis.get("beats", [])
        track.cue_in = analysis.get("cue_in")
        track.cue_out = analysis.get("cue_out")
        track.energy = analysis.get("energy", [])
        track.waveform = analysis.get("waveform", [])
        track.lufs = analysis.get("lufs")
        track.danceability = analysis.get("danceability")
        track.dynamic_complexity = analysis.get("dynamic_complexity")
        track.onsets = analysis.get("onsets", [])
        track.fade_in_end = analysis.get("fade_in_end")
        track.fade_out_start = analysis.get("fade_out_start")
        track.chords = analysis.get("chords", [])

    proj.save()
    result = {
        "bpm": track.bpm,
        "key": track.key,
        "duration": track.duration,
        "bars": track.bars,
        "cue_in": track.cue_in,
        "cue_out": track.cue_out,
        "replay_gain": track.replay_gain,
    }
    if args.full:
        result.update({
            "lufs": track.lufs,
            "danceability": track.danceability,
            "dynamic_complexity": track.dynamic_complexity,
            "fade_in_end": track.fade_in_end,
            "fade_out_start": track.fade_out_start,
            "chords_count": len(track.chords),
            "onsets_count": len(track.onsets),
        })
    print(json.dumps(result, indent=2))


def cmd_analyze_all(args):
    """Analyze all tracks that don't have BPM/key yet."""
    proj = get_project(args.project)
    count = 0
    for i, track in enumerate(proj.library):
        if track.bpm and track.key and (not args.full or track.beats):
            continue
        print(f"  [{i}] Analyzing {track.title}...")
        try:
            analysis = analyze_track(track.path, full=args.full)
            track.bpm = analysis["bpm"]
            track.key = analysis["key"]
            track.duration = analysis["duration"]
            track.replay_gain = analysis.get("replay_gain")
            if args.full:
                track.beats = analysis.get("beats", [])
                track.cue_in = analysis.get("cue_in")
                track.cue_out = analysis.get("cue_out")
                track.energy = analysis.get("energy", [])
                track.waveform = analysis.get("waveform", [])
                track.lufs = analysis.get("lufs")
                track.danceability = analysis.get("danceability")
                track.dynamic_complexity = analysis.get("dynamic_complexity")
                track.onsets = analysis.get("onsets", [])
                track.fade_in_end = analysis.get("fade_in_end")
                track.fade_out_start = analysis.get("fade_out_start")
                track.chords = analysis.get("chords", [])
            count += 1
            print(f"       {track.bpm} BPM, {track.key}, {track.bars} bars")
        except Exception as e:
            print(f"       Failed: {e}", file=sys.stderr)

    proj.save()
    print(f"\nAnalyzed {count} tracks.")


def cmd_play(args):
    """Play a track via sounddevice (blocking, Ctrl-C to stop)."""
    from pymixter.core.player import Player

    proj = get_project(args.project)
    idx = args.index
    if idx >= len(proj.library):
        print(f"Track index {idx} out of range", file=sys.stderr)
        sys.exit(1)

    track = proj.library[idx]
    player = Player()

    def _on_finish():
        print("\nDone.")

    player.on_finish = _on_finish
    signal.signal(signal.SIGINT, lambda *_: (player.stop(), sys.exit(0)))

    print(f"Playing: {track.title} ({track.bpm or '?'} BPM, {track.key or '?'})")
    print("Press Ctrl-C to stop")
    player.load(track.path, replay_gain_db=track.replay_gain)
    player.play()

    # Block until playback finishes
    import time
    while player.state.value != "stopped":
        time.sleep(0.5)

    player.close()


def cmd_library(args):
    proj = get_project(args.project)
    if not proj.library:
        print("Library is empty. Use 'add' to add tracks.")
        return
    for i, t in enumerate(proj.library):
        bpm = f"{t.bpm}" if t.bpm else "?"
        key = t.key or "?"
        dur = f"{t.duration:.0f}s" if t.duration else "?"
        bars = f"{t.bars}bar" if t.bars else ""
        extras = ""
        if t.lufs is not None:
            extras += f"  LUFS:{t.lufs:.1f}"
        if t.danceability is not None:
            extras += f"  Dance:{t.danceability:.2f}"
        print(f"  [{i}] {t.title}  BPM:{bpm}  Key:{key}  Dur:{dur}  {bars}{extras}")


def cmd_timeline_show(args):
    proj = get_project(args.project)
    if not proj.timeline:
        print("Timeline is empty.")
        return
    for pos, tidx in enumerate(proj.timeline):
        t = proj.library[tidx]
        print(f"  {pos}. [{tidx}] {t.title} ({t.bpm} BPM, {t.key})")

    for tr in proj.transitions:
        f = proj.library[proj.timeline[tr.from_track]].title
        t = proj.library[proj.timeline[tr.to_track]].title
        print(f"  Transition: {f} -> {t}  type={tr.type}  bars={tr.length_bars}")


def cmd_timeline_append(args):
    proj = get_project(args.project)
    proj.append_to_timeline(args.index)
    proj.save()
    t = proj.library[args.index]
    print(f"Appended [{args.index}] {t.title} to timeline (pos {len(proj.timeline) - 1})")


def cmd_timeline_move(args):
    proj = get_project(args.project)
    proj.move_timeline_track(args.from_pos, args.to_pos)
    proj.save()
    print(f"Moved timeline position {args.from_pos} -> {args.to_pos}")
    cmd_timeline_show(args)


def cmd_timeline_remove(args):
    proj = get_project(args.project)
    t = proj.library[proj.timeline[args.pos]]
    proj.remove_from_timeline(args.pos)
    proj.save()
    print(f"Removed [{args.pos}] {t.title} from timeline")


def cmd_transition_add(args):
    proj = get_project(args.project)
    proj.add_transition(args.from_idx, args.to_idx,
                        type=args.type, length_bars=args.bars)
    proj.save()
    print(f"Transition added: {args.from_idx} -> {args.to_idx} ({args.type}, {args.bars} bars)")


def cmd_transition_edit(args):
    proj = get_project(args.project)
    proj.set_transition(args.pos, args.type, args.bars)
    proj.save()
    print(f"Transition [{args.pos}]: {args.type} {args.bars} bars")


def cmd_transition_list(args):
    proj = get_project(args.project)
    if not proj.transitions:
        print("No transitions defined.")
        return
    for tr in proj.transitions:
        a = proj.library[proj.timeline[tr.from_track]].title
        b = proj.library[proj.timeline[tr.to_track]].title
        flags = []
        if tr.tempo_sync:
            flags.append("tempo-sync")
        if tr.beat_aligned:
            flags.append("beat-aligned")
        flag_str = f" ({', '.join(flags)})" if flags else ""
        print(f"  [{tr.from_track}] {a} -> {b}  {tr.type} {tr.length_bars}b{flag_str}")


def cmd_transition_remove(args):
    proj = get_project(args.project)
    proj.transitions = [t for t in proj.transitions if t.from_track != args.pos]
    proj.save()
    print(f"Removed transition at position {args.pos}")


def cmd_cue(args):
    """Set cue points for a track."""
    proj = get_project(args.project)
    idx = args.index
    if idx >= len(proj.library):
        print(f"Track index {idx} out of range", file=sys.stderr)
        sys.exit(1)
    track = proj.library[idx]
    if args.cue_in is not None:
        track.cue_in = args.cue_in
    if args.cue_out is not None:
        track.cue_out = args.cue_out
    proj.save()
    print(f"Cue points for [{idx}] {track.title}: in={track.cue_in}s out={track.cue_out}s")


def cmd_info(args):
    proj = get_project(args.project)
    print(f"Project: {proj.name}")
    print(f"Library: {len(proj.library)} tracks")
    print(f"Timeline: {len(proj.timeline)} tracks")
    print(f"Transitions: {len(proj.transitions)}")
    print(f"Version: {proj.get_version()}")


def cmd_render(args):
    """Render timeline to audio file (WAV, MP3, FLAC)."""
    from pymixter.core.mixer import render_to_file

    proj = get_project(args.project)
    if not proj.timeline:
        print("Timeline is empty. Run 'automix' first.")
        return

    output = args.output or "mix_output.wav"

    def progress(pos, total, msg):
        print(f"  [{pos+1}/{total}] {msg}")

    print(f"Rendering {len(proj.timeline)} tracks...")
    path = render_to_file(proj, output, on_progress=progress,
                          quality=getattr(args, 'quality', None))

    # Show duration
    from pedalboard.io import AudioFile
    with AudioFile(path) as f:
        dur = f.frames / f.samplerate
    m, s = divmod(int(dur), 60)
    print(f"\nRendered to {path} ({m}:{s:02d})")


def cmd_validate(args):
    """Validate timeline for issues."""
    from pymixter.core.mixer import validate_timeline

    proj = get_project(args.project)
    warnings = validate_timeline(proj)
    if not warnings:
        print("Timeline OK — no issues found")
    else:
        print(f"Found {len(warnings)} issue(s):")
        for w in warnings:
            print(f"  ! {w}")


def cmd_automix(args):
    """Auto-arrange tracks into a harmonically compatible mix."""
    from pymixter.core.automix import automix

    proj = get_project(args.project)

    track_indices = None
    if args.tracks:
        track_indices = [int(x) for x in args.tracks.split(",")]

    start_idx = args.start

    order = automix(proj, track_indices=track_indices, start_idx=start_idx)
    if not order:
        print("No analyzed tracks available. Run 'analyze-all --full' first.")
        return

    proj.save()

    print(f"Automix: {len(order)} tracks arranged, {len(proj.transitions)} transitions")
    for pos, tidx in enumerate(proj.timeline):
        t = proj.library[tidx]
        print(f"  {pos}. [{tidx}] {t.title} ({t.bpm} BPM, {t.key})")

    for tr in proj.transitions:
        a = proj.library[proj.timeline[tr.from_track]].title
        b = proj.library[proj.timeline[tr.to_track]].title
        print(f"  >> {a} -> {b}  [{tr.type}, {tr.length_bars} bars]")


def cmd_export(args):
    """Export project to Rekordbox XML."""
    from pymixter.core.rekordbox_xml import export_rekordbox_xml

    proj = get_project(args.project)
    output = args.output or args.project.replace(".json", ".xml")
    path = export_rekordbox_xml(proj, output)
    print(f"Exported {len(proj.library)} tracks to {path}")
    if proj.timeline:
        print(f"  + timeline playlist ({len(proj.timeline)} tracks)")


def cmd_import_xml(args):
    """Import tracks from Rekordbox XML."""
    from pymixter.core.rekordbox_xml import import_rekordbox_xml

    proj = get_project(args.project)
    before = len(proj.library)
    import_rekordbox_xml(args.file, proj)
    added = len(proj.library) - before
    proj.save()
    print(f"Imported {added} tracks from {args.file}")
    print(f"Library now has {len(proj.library)} tracks")


def cmd_open(args):
    """Open a project file (JSON or Rekordbox XML)."""
    path = args.file
    if path.endswith(".xml"):
        from pymixter.core.rekordbox_xml import import_rekordbox_xml
        proj = import_rekordbox_xml(path)
        # Save as JSON project
        out = args.project
        proj.save(out)
        print(f"Imported Rekordbox XML -> {out}")
        print(f"  {len(proj.library)} tracks, {len(proj.timeline)} in timeline")
    elif path.endswith(".json"):
        proj = Project.load(path)
        if args.project != path:
            proj.save(args.project)
            print(f"Copied project {path} -> {args.project}")
        print(f"  {len(proj.library)} tracks, {len(proj.timeline)} in timeline")
    else:
        print(f"Unknown format: {path} (use .json or .xml)")


def cmd_bpm(args):
    """Set or adjust BPM for a track."""
    proj = get_project(args.project)
    idx = args.index
    if idx >= len(proj.library):
        print(f"Track index {idx} out of range", file=sys.stderr)
        sys.exit(1)
    track = proj.library[idx]

    if args.value is not None:
        old_bpm = track.bpm
        proj.set_bpm(idx, args.value)
        proj.save()
        print(f"BPM [{idx}] {track.title}: {old_bpm} -> {track.bpm}")
    elif args.halve:
        if track.bpm:
            proj.set_bpm(idx, track.bpm / 2)
            proj.save()
            print(f"BPM halved: {track.bpm}")
    elif args.double:
        if track.bpm:
            proj.set_bpm(idx, track.bpm * 2)
            proj.save()
            print(f"BPM doubled: {track.bpm}")
    elif args.key:
        track.key = args.key
        proj.save()
        print(f"Key [{idx}] {track.title}: {track.key}")
    else:
        print(f"BPM [{idx}] {track.title}: {track.bpm or '?'}")


def cmd_stems(args):
    """Separate track into stems (vocals, drums, bass, other)."""
    from pymixter.core.stems import separate_track

    proj = get_project(args.project)
    idx = args.index
    if idx >= len(proj.library):
        print(f"Track index {idx} out of range", file=sys.stderr)
        sys.exit(1)

    track = proj.library[idx]
    stems_dir = str(proj.project_dir / "stems" / Path(track.path).stem)
    model = args.model or "Demucs v4: htdemucs"

    print(f"Separating stems for {track.title}...")
    print(f"  Model: {model}")
    print(f"  Output: {stems_dir}")

    stems = separate_track(
        track.path, stems_dir, model=model,
        on_progress=lambda msg: print(f"  {msg}"),
    )

    track.stems = stems
    proj.save()
    print(f"\nStems created:")
    for name, path in stems.items():
        print(f"  {name}: {path}")


def cmd_preview(args):
    """Preview a transition by rendering and playing it."""
    from pymixter.core.mixer import render_transition_preview
    from pymixter.core.player import Player

    proj = get_project(args.project)
    pos = args.pos

    print(f"Rendering transition preview [{pos}]...")
    audio, sr = render_transition_preview(proj, pos)
    duration = audio.shape[1] / sr
    m, s = divmod(int(duration), 60)
    print(f"Playing transition preview ({m}:{s:02d})...")

    player = Player()
    signal.signal(signal.SIGINT, lambda *_: (player.stop(), sys.exit(0)))
    player.load_audio(audio, sr, label="preview")
    player.play()

    import time
    while player.state.value != "stopped":
        time.sleep(0.5)
    player.close()


def cmd_suggest(args):
    """Suggest next compatible track based on key and BPM."""
    proj = get_project(args.project)
    if not proj.timeline:
        print("Timeline is empty — add a track first.")
        return

    last = proj.library[proj.timeline[-1]]
    if not last.bpm or not last.key:
        print(f"Last track '{last.title}' has no BPM/key info. Run analyze first.")
        return

    candidates = proj.suggest_next()
    if not candidates:
        print("No candidates available.")
        return

    print(f"Suggestions after '{last.title}' ({last.bpm} BPM, {last.key}):")
    for i, t, score, key_ok in candidates:
        bpm_diff = abs(t.bpm - last.bpm)
        key_mark = "+" if key_ok else "-"
        print(f"  [{i}] {t.title}  BPM:{t.bpm} (d{bpm_diff:.1f})  "
              f"Key:{t.key} {key_mark}  score:{score:.1f}")


def main():
    parser = argparse.ArgumentParser(prog="pymixter", description="DJ Mix CLI")
    parser.add_argument("-p", "--project", default=DEFAULT_PROJECT,
                        help="Project file path")
    sub = parser.add_subparsers(dest="command")

    p_init = sub.add_parser("init")
    p_init.add_argument("name")

    p_add = sub.add_parser("add")
    p_add.add_argument("file", help="Audio file to import")
    p_add.add_argument("--analyze", action="store_true")

    p_scan = sub.add_parser("scan", help="Import all audio from directory")
    p_scan.add_argument("directory")
    p_scan.add_argument("--analyze", action="store_true")

    p_analyze = sub.add_parser("analyze")
    p_analyze.add_argument("index", type=int)
    p_analyze.add_argument("--full", action="store_true",
                           help="Include beats, cue points, energy")

    p_analyze_all = sub.add_parser("analyze-all",
                                   help="Analyze all unanalyzed tracks")
    p_analyze_all.add_argument("--full", action="store_true")

    p_play = sub.add_parser("play", help="Play a track")
    p_play.add_argument("index", type=int)

    p_automix = sub.add_parser("automix", help="Auto-arrange tracks into a mix")
    p_automix.add_argument("--tracks", default=None,
                           help="Comma-separated track indices (default: all)")
    p_automix.add_argument("--start", type=int, default=None,
                           help="Library index to start from")

    p_export = sub.add_parser("export", help="Export to Rekordbox XML")
    p_export.add_argument("--output", "-o", default=None,
                          help="Output XML path (default: project.xml)")

    p_import_xml = sub.add_parser("import", help="Import from Rekordbox XML")
    p_import_xml.add_argument("file", help="Rekordbox XML file")

    p_open = sub.add_parser("open", help="Open project (JSON or Rekordbox XML)")
    p_open.add_argument("file", help="Project file (.json or .xml)")

    p_render = sub.add_parser("render", help="Render timeline to audio file")
    p_render.add_argument("--output", "-o", default="mix_output.wav",
                          help="Output path (.wav, .mp3, .flac)")
    p_render.add_argument("--quality", default=None,
                          help="MP3 quality (e.g., V0, V2, 320)")

    sub.add_parser("validate", help="Check timeline for issues")

    sub.add_parser("library")
    sub.add_parser("info")
    sub.add_parser("suggest")

    p_cue = sub.add_parser("cue", help="Set cue points for a track")
    p_cue.add_argument("index", type=int)
    p_cue.add_argument("--in", dest="cue_in", type=float, default=None)
    p_cue.add_argument("--out", dest="cue_out", type=float, default=None)

    p_bpm = sub.add_parser("bpm", help="View/set BPM and key for a track")
    p_bpm.add_argument("index", type=int)
    p_bpm.add_argument("--set", dest="value", type=float, default=None,
                        help="Set BPM to exact value")
    p_bpm.add_argument("--halve", action="store_true", help="Halve BPM")
    p_bpm.add_argument("--double", action="store_true", help="Double BPM")
    p_bpm.add_argument("--key", default=None, help="Set key (e.g. Am, C#)")

    p_stems = sub.add_parser("stems", help="Separate track into stems")
    p_stems.add_argument("index", type=int)
    p_stems.add_argument("--model", default=None,
                          help="Separation model (default: htdemucs)")

    p_preview = sub.add_parser("preview", help="Preview a transition")
    p_preview.add_argument("pos", type=int, help="Timeline position")

    p_tl = sub.add_parser("timeline")
    tl_sub = p_tl.add_subparsers(dest="tl_cmd")
    tl_sub.add_parser("show")
    p_tl_append = tl_sub.add_parser("append")
    p_tl_append.add_argument("index", type=int)
    p_tl_move = tl_sub.add_parser("move")
    p_tl_move.add_argument("from_pos", type=int)
    p_tl_move.add_argument("to_pos", type=int)
    p_tl_remove = tl_sub.add_parser("remove")
    p_tl_remove.add_argument("pos", type=int)

    p_tr = sub.add_parser("transition")
    tr_sub = p_tr.add_subparsers(dest="tr_cmd")
    p_tr_add = tr_sub.add_parser("add")
    p_tr_add.add_argument("from_idx", type=int)
    p_tr_add.add_argument("to_idx", type=int)
    p_tr_add.add_argument("--type", default="crossfade")
    p_tr_add.add_argument("--bars", type=int, default=16)
    p_tr_edit = tr_sub.add_parser("edit")
    p_tr_edit.add_argument("pos", type=int)
    p_tr_edit.add_argument("--type", default="crossfade")
    p_tr_edit.add_argument("--bars", type=int, default=16)
    tr_sub.add_parser("list")
    p_tr_remove = tr_sub.add_parser("remove")
    p_tr_remove.add_argument("pos", type=int)

    args = parser.parse_args()

    commands = {
        "init": cmd_init,
        "add": cmd_add,
        "scan": cmd_scan,
        "analyze": cmd_analyze,
        "analyze-all": cmd_analyze_all,
        "automix": cmd_automix,
        "play": cmd_play,
        "library": cmd_library,
        "info": cmd_info,
        "suggest": cmd_suggest,
        "render": cmd_render,
        "validate": cmd_validate,
        "export": cmd_export,
        "import": cmd_import_xml,
        "open": cmd_open,
        "cue": cmd_cue,
        "bpm": cmd_bpm,
        "stems": cmd_stems,
        "preview": cmd_preview,
    }

    if args.command in commands:
        commands[args.command](args)
    elif args.command == "timeline":
        tl_dispatch = {
            "show": cmd_timeline_show,
            "append": cmd_timeline_append,
            "move": cmd_timeline_move,
            "remove": cmd_timeline_remove,
        }
        if args.tl_cmd in tl_dispatch:
            tl_dispatch[args.tl_cmd](args)
        else:
            print("Usage: timeline {show|append|move|remove}")
    elif args.command == "transition":
        tr_dispatch = {
            "add": cmd_transition_add,
            "edit": cmd_transition_edit,
            "list": cmd_transition_list,
            "remove": cmd_transition_remove,
        }
        if args.tr_cmd in tr_dispatch:
            tr_dispatch[args.tr_cmd](args)
        else:
            print("Usage: transition {add|edit|list|remove}")
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
