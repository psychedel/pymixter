"""Main TUI application."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.events import MouseMove
from textual.theme import Theme
from textual.widgets import Header, Footer, Static, TabbedContent, TabPane
from textual.timer import Timer
from textual.worker import Worker, WorkerState

from pymixter.core.project import Project, find_audio_files
from pymixter.core.analysis import analyze_track
from pymixter.core.automix import automix
from pymixter.core.player import Player, PlayerState
from pymixter.core.recent import get_recent, add_recent
from pymixter.core.mixer import (
    render_timeline, render_to_file, validate_timeline,
    render_transition_preview,
)
from pymixter.core.history import History
from pymixter.tui.widgets.library import LibraryTable
from pymixter.tui.widgets.timeline import TimelineView
from pymixter.tui.widgets.track_info import TrackInfo
from pymixter.tui.widgets.fuzzy_finder import FuzzyFinder, FileBrowser
from pymixter.tui.widgets.command_console import CommandConsole

log = logging.getLogger(__name__)

FOREST_THEME = Theme(
    name="forest",
    primary="#a8b060",
    secondary="#7a8a50",
    accent="#c8cc6e",
    foreground="#c5c8a8",
    background="#2a2d2a",
    surface="#333833",
    panel="#3a3f3a",
    success="#a8b060",
    warning="#c8a848",
    error="#c87848",
    dark=True,
)


def _fmt_time(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    return f"{m}:{s:02d}"


class MixApp(App):
    """DJ Mix Studio — Terminal Edition."""

    TITLE = "DJ Mix Studio"
    SUB_TITLE = "Ready"
    ENABLE_COMMAND_PALETTE = False
    ansi_color = True
    CSS = """
    Screen { background: transparent; }
    TabbedContent { background: transparent; height: 1fr; }
    TabPane { background: transparent; padding: 0; }
    ContentSwitcher { background: transparent; }
    #library { background: transparent; height: 1fr; }
    #timeline { background: transparent; height: 1fr; }
    #track-info { background: transparent; height: 1fr; }
    Header { background: $primary-background; }
    Footer { background: $primary-background; }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("a", "add_to_timeline", "Add"),
        Binding("colon", "open_console", ":", key_display=":"),
        Binding("slash", "fuzzy_search", "/", key_display="/"),
        Binding("o", "open_file_browser", "Open"),
        Binding("space", "toggle_play", "Play", key_display="SPC", priority=True),
        Binding("p", "toggle_play", "Play", show=False, priority=True),
        Binding("left_square_bracket", "seek_back", "[<<", show=False),
        Binding("right_square_bracket", "seek_forward", ">>]", show=False),
        Binding("x", "stop_play", "Stop", show=False),
        Binding("r", "reload_project", "Reload", show=False),
        Binding("s", "save_project", "Save", show=False),
        Binding("l", "open_recent", "Recent", show=False),
        Binding("u", "undo", "Undo", show=False),
        Binding("ctrl+r", "redo", "Redo", show=False),
        Binding("1", "tab_library", "Library", show=False),
        Binding("2", "tab_timeline", "Timeline", show=False),
        Binding("3", "tab_info", "Info", show=False),
    ]

    def __init__(self, project_path: str = "project.json"):
        super().__init__()
        self.register_theme(FOREST_THEME)
        self.theme = "forest"
        self.project_path = project_path
        self._selected_track_idx: int | None = None
        self._last_version: int = 0
        self._watcher: Timer | None = None
        self._position_timer: Timer | None = None
        self._last_status: str = ""
        self.player = Player()
        self.history = History()
        self._load_project()

    def _load_project(self):
        p = Path(self.project_path)
        if p.exists():
            self.project = Project.load(self.project_path)
        else:
            self.project = Project(_path=self.project_path)
            self.project.save()
        add_recent(self.project_path)

    def _checkpoint(self, description: str):
        """Capture current project state for undo BEFORE a mutation."""
        self.history.checkpoint(self.project, description)

    def _save_and_sync(self):
        """Save project and update version tracker (call AFTER mutation)."""
        self.project.save()
        self._last_version = self.project.get_version()
        self._refresh_all()

    # ── Compose & lifecycle ─────────────────────────────────────

    def compose(self) -> ComposeResult:
        yield Header()
        with TabbedContent():
            with TabPane("Library [1]", id="tab-library"):
                yield LibraryTable(id="library")
            with TabPane("Timeline [2]", id="tab-timeline"):
                yield TimelineView(self.project, id="timeline")
            with TabPane("Track [3]", id="tab-info"):
                yield TrackInfo(id="track-info")
        yield Footer()

    def on_mouse_move(self, event: MouseMove) -> None:
        """Suppress mouse move processing to prevent lag."""
        event.stop()

    def on_mount(self):
        self._refresh_all()
        self._last_version = self.project.get_version()
        self._watcher = self.set_interval(2.0, self._check_for_changes)
        self._position_timer = self.set_interval(1.0, self._update_playback_status)
        # Auto-select first track and focus library
        if self.project.library:
            self._select_track(0)
        self.query_one("#library", LibraryTable).focus()

    def on_unmount(self):
        self.player.close()

    def on_worker_state_changed(self, event: Worker.StateChanged):
        worker = event.worker
        if worker.state != WorkerState.SUCCESS and worker.state != WorkerState.ERROR:
            return
        if worker.name == "render":
            if worker.error:
                self._set_status(f"Render error: {worker.error}")
            else:
                self._set_status(f"Rendered to {worker.result}")
        elif worker.name in ("playmix", "preview_transition"):
            if worker.error:
                self._set_status(f"Playback error: {worker.error}")
            else:
                audio, sr = worker.result
                if audio.shape[1] == 0:
                    self._set_status("Nothing to play — rendered empty audio")
                    return
                self.player.load_audio(audio, sr, label=worker.name)
                self.player.play()
                duration = audio.shape[1] / sr
                label = "transition" if "preview" in worker.name else "mix"
                self._set_status(f">> Playing {label} ({_fmt_time(duration)})")
        elif worker.name == "analyze":
            if worker.error:
                self._set_status(f"Analysis failed: {worker.error}")
            else:
                idx, analysis = worker.result
                track = self.project.library[idx]
                self._checkpoint("Analyze track")
                track.bpm = analysis.get("bpm")
                track.key = analysis.get("key")
                track.duration = analysis.get("duration", 0)
                track.beats = analysis.get("beats", [])
                track.cue_in = analysis.get("cue_in")
                track.cue_out = analysis.get("cue_out")
                track.energy = analysis.get("energy", [])
                track.waveform = analysis.get("_waveform", [])
                self._save_and_sync()
                self._set_status(
                    f"Analyzed: {track.title} — {track.bpm} BPM, {track.key}, "
                    f"{track.bars} bars"
                )
                self.query_one("#track-info", TrackInfo).show_track(track)
        elif worker.name == "scan":
            if worker.error:
                self._set_status(f"Scan failed: {worker.error}")
            else:
                files, directory = worker.result
                if not files:
                    self._set_status(f"No audio files found in {directory}")
                else:
                    self._checkpoint("Scan directory")
                    for f in files:
                        self.project.import_track(f)
                    self._save_and_sync()
                    self._switch_tab("tab-library")
                    self._set_status(f"Imported {len(files)} tracks from {directory}")
        elif worker.name == "stems":
            if worker.error:
                self._set_status(f"Stem separation failed: {worker.error}")
            else:
                idx, stems = worker.result
                track = self.project.library[idx]
                self._checkpoint("Stem separation")
                track.stems = stems
                self._save_and_sync()
                self._set_status(
                    f"Stems: {track.title} -> {', '.join(stems.keys())}"
                )
        elif worker.name == "deckb":
            if worker.error:
                self._set_status(f"Deck B error: {worker.error}")
            else:
                audio, sr, title = worker.result
                self.player.load_deck_b_audio(audio, sr, label=title)
                self._set_status(f"Deck B: {title}")

    def _check_for_changes(self):
        """Poll project file for external changes (e.g., from CLI)."""
        p = Path(self.project_path)
        if not p.exists():
            return
        try:
            reloaded = Project.load(self.project_path)
        except (json.JSONDecodeError, KeyError, OSError) as exc:
            log.debug("Failed to reload project: %s", exc)
            return
        if reloaded.get_version() != self._last_version:
            self.project = reloaded
            self._last_version = reloaded.get_version()
            self._refresh_all()
            self._set_status("Project updated externally — reloaded")

    def _refresh_all(self):
        self.query_one("#library", LibraryTable).refresh_library(self.project)
        self.query_one("#timeline", TimelineView).refresh_timeline(self.project)

    def _set_status(self, msg: str):
        self.sub_title = msg

    def _switch_tab(self, tab_id: str):
        self.query_one(TabbedContent).active = tab_id

    # ── Playback ─────────────────────────────────────────────

    def _update_playback_status(self):
        """Update status bar with playback position and progress bar."""
        if self.player.state == PlayerState.STOPPED:
            return
        pos = _fmt_time(self.player.position)
        dur = _fmt_time(self.player.duration)
        icon = "||" if self.player.state == PlayerState.PAUSED else ">>"
        pct = int(self.player.progress * 100)
        status = f"{icon} {pos}/{dur} {pct}%"
        if status != self._last_status:
            self._last_status = status
            self._set_status(status)

    def action_toggle_play(self):
        if self.player.state == PlayerState.PLAYING:
            self.player.pause()
            self._set_status(f"|| Paused")
            return
        if self.player.state == PlayerState.PAUSED:
            self.player.play()
            self._set_status(f">> Resumed")
            return
        # Nothing loaded — play selected track
        if self._selected_track_idx is None:
            self._set_status("No track selected — select one first")
            return
        self._play_track(self._selected_track_idx)

    def action_seek_back(self):
        if self.player.state != PlayerState.STOPPED:
            self.player.seek_relative(-5.0)

    def action_seek_forward(self):
        if self.player.state != PlayerState.STOPPED:
            self.player.seek_relative(5.0)

    def action_stop_play(self):
        self.player.stop()
        self._set_status("Stopped")

    # ── Track selection ─────────────────────────────────────────

    def _select_track(self, idx: int):
        if idx >= len(self.project.library):
            return
        track = self.project.library[idx]
        self.query_one("#track-info", TrackInfo).show_track(track)
        self._selected_track_idx = idx
        self._set_status(f"Selected: {track.title}")

    def on_library_table_track_selected(self, event: LibraryTable.TrackSelected):
        self._select_track(event.track_index)
        self._play_track(event.track_index)

    def on_timeline_view_track_clicked(self, event: TimelineView.TrackClicked):
        """Handle click on a track in the timeline view."""
        self._select_track(event.library_idx)
        self._switch_tab("tab-info")

    # ── Command console (:) ─────────────────────────────────────

    def action_open_console(self):
        self.push_screen(CommandConsole(), self._handle_command)

    def _handle_command(self, cmd: str | None):
        if not cmd:
            return
        parts = cmd.split()
        verb, args = parts[0].lower(), parts[1:]

        # Commands that take no args or handle args internally
        dispatch = {
            "help": lambda: self._set_status(
                "add scan analyze automix timeline transition cue eq gain bpm stems "
                "xfader deckb preview render validate playmix export import undo redo"
            ),
            "save": lambda: self.action_save_project(),
            "suggest": lambda: self._show_suggestions(),
            "stop": lambda: (self.player.stop(), self._set_status("Stopped")),
            "q": lambda: self.exit(),
            "quit": lambda: self.exit(),
            "info": lambda: self._set_status(
                f"{self.project.name}: {len(self.project.library)} tracks, "
                f"{len(self.project.timeline)} in timeline"
            ),
            "automix": lambda: self._run_automix(args),
            "timeline": lambda: self._handle_timeline_cmd(args),
            "transition": lambda: self._handle_transition_cmd(args),
            "cue": lambda: self._handle_cue_cmd(args),
            "eq": lambda: self._handle_eq_cmd(args),
            "undo": lambda: self.action_undo(),
            "redo": lambda: self.action_redo(),
            "render": lambda: self._render_mix(args),
            "validate": lambda: self._validate_mix(),
            "playmix": lambda: self._play_mix(),
            "export": lambda: self._export_project(args),
            "play": lambda: self._cmd_play(args),
            "seek": lambda: self._cmd_seek(args),
            "analyze": lambda: self._cmd_analyze(args),
            "add": lambda: self._import_file(" ".join(args)) if args else self._set_status("Usage: add <path>"),
            "scan": lambda: self._scan_directory(" ".join(args)) if args else self._set_status("Usage: scan <dir>"),
            "gain": lambda: self._cmd_gain(args),
            "import": lambda: self._import_xml(" ".join(args)) if args else self._set_status("Usage: import <file>"),
            "open": lambda: self._open_project(" ".join(args)) if args else self._set_status("Usage: open <file>"),
            "bpm": lambda: self._handle_bpm_cmd(args),
            "stems": lambda: self._handle_stems_cmd(args),
            "xfader": lambda: self._cmd_crossfader(args),
            "deckb": lambda: self._cmd_deck_b(args),
            "preview": lambda: self._preview_transition(args),
        }

        handler = dispatch.get(verb)
        if handler:
            handler()
        else:
            self._set_status(f"Unknown: {verb}. Type :help")

    def _cmd_play(self, args: list[str]):
        if args:
            try:
                idx = int(args[0])
            except ValueError:
                self._set_status("Usage: play [index]")
                return
        else:
            idx = self._selected_track_idx
        if idx is not None:
            self._play_track(idx)
        else:
            self._set_status("Usage: play [index]")

    def _cmd_seek(self, args: list[str]):
        if not args:
            self._set_status("Usage: seek <seconds>")
            return
        try:
            self.player.seek(float(args[0]))
        except ValueError:
            self._set_status("Usage: seek <seconds>")

    def _cmd_analyze(self, args: list[str]):
        if args:
            try:
                idx = int(args[0])
            except ValueError:
                self._set_status("Usage: analyze [index]")
                return
        else:
            idx = self._selected_track_idx
        if idx is not None:
            self._analyze_track(idx)
        else:
            self._set_status("Usage: analyze [index]")

    def _cmd_gain(self, args: list[str]):
        if not args:
            self._set_status("Usage: gain <dB>")
            return
        try:
            self.player.deck_a.gain.gain_db = float(args[0])
            self._set_status(f"Gain: {args[0]} dB")
        except ValueError:
            self._set_status("Usage: gain <dB>")

    # ── Playback from command / selection ─────────────────────

    def _play_track(self, idx: int):
        if idx >= len(self.project.library):
            self._set_status(f"Track index {idx} out of range")
            return
        track = self.project.library[idx]
        try:
            self.player.play(track.path)
            self._selected_track_idx = idx
            self.query_one("#track-info", TrackInfo).show_track(track)
            self._set_status(f">> {track.title}")
        except Exception as e:
            self._set_status(f"Playback error: {e}")

    # ── Import / scan ───────────────────────────────────────────

    def _import_file(self, path: str):
        try:
            self._checkpoint("Import track")
            track = self.project.import_track(path)
            self._save_and_sync()
            self._set_status(f"Imported: {track.title}")
            self._switch_tab("tab-library")
        except FileNotFoundError:
            self._set_status(f"File not found: {path}")
        except Exception as e:
            self._set_status(f"Error: {e}")

    def _scan_directory(self, directory: str):
        """Scan and import audio files (non-blocking).

        Worker only finds files; actual import happens on main thread
        in on_worker_state_changed to avoid mutating project from a thread.
        """
        self._set_status(f"Scanning {directory}...")

        def _do_scan():
            files = find_audio_files(directory)
            return [str(f) for f in files], directory

        self.run_worker(_do_scan, thread=True, exit_on_error=False,
                        name="scan", exclusive=True, group="scan")

    def _analyze_track(self, idx: int):
        """Run track analysis in background worker."""
        if idx >= len(self.project.library):
            self._set_status(f"Track index {idx} out of range")
            return
        track = self.project.library[idx]
        self._set_status(f"Analyzing {track.title}...")

        def _do_analyze():
            return idx, analyze_track(track.path, full=True)

        self.run_worker(_do_analyze, thread=True, exit_on_error=False,
                        name="analyze", exclusive=True, group="analyze")

    def _run_automix(self, args: list[str]):
        track_indices = None
        start_idx = None
        if args:
            try:
                track_indices = [int(x) for x in args[0].split(",")]
            except ValueError:
                self._set_status("Usage: automix [indices] e.g. automix 0,1,2,3")
                return
        if len(args) >= 2:
            try:
                start_idx = int(args[1])
            except ValueError:
                pass

        self._checkpoint("Automix")
        order = automix(self.project, track_indices=track_indices,
                        start_idx=start_idx)
        if not order:
            self._set_status("No analyzed tracks — run analyze first")
            return
        self._save_and_sync()
        self._switch_tab("tab-timeline")
        n_tr = len(self.project.transitions)
        self._set_status(
            f"Automix: {len(order)} tracks, {n_tr} transitions"
        )

    def _show_suggestions(self):
        candidates = self.project.suggest_next(limit=1)
        if not candidates:
            self._set_status("No suggestions — need analyzed tracks in timeline")
            return
        i, t, _score, key_ok = candidates[0]
        mark = "+" if key_ok else "-"
        self._set_status(f"Next: [{i}] {t.title} ({t.bpm} BPM, {t.key} {mark})")

    # ── Fuzzy search (/) ────────────────────────────────────────

    def action_fuzzy_search(self):
        if not self.project.library:
            self._set_status("Library is empty — open files first (o)")
            return
        items = [
            f"{t.title}  {t.bpm or '?'} BPM  {t.key or '?'}"
            for t in self.project.library
        ]
        self.push_screen(FuzzyFinder(items), self._handle_fuzzy_result)

    def _handle_fuzzy_result(self, idx: int | None):
        if idx is None:
            return
        self._select_track(idx)
        self._switch_tab("tab-library")
        self.query_one("#library", LibraryTable).move_cursor(row=idx)

    # ── Recent projects (l) ────────────────────────────────────

    def action_open_recent(self):
        recent = get_recent()
        if not recent:
            self._set_status("No recent projects")
            return
        items = [f"{Path(p).stem}  {p}" for p in recent]
        self.push_screen(FuzzyFinder(items), self._handle_recent_result)

    def _handle_recent_result(self, idx: int | None):
        if idx is None:
            return
        recent = get_recent()
        if idx >= len(recent):
            return
        path = recent[idx]
        self._open_project(path)

    # ── File browser (o) ────────────────────────────────────────

    def action_open_file_browser(self):
        self.push_screen(FileBrowser(), self._handle_file_selected)

    def _handle_file_selected(self, path: str | None):
        if path:
            self._import_file(path)

    # ── Basic actions ───────────────────────────────────────────

    # ── Project import/export ────────────────────────────────

    def _export_project(self, args: list[str]):
        from pymixter.core.rekordbox_xml import export_rekordbox_xml
        output = args[0] if args else self.project_path.replace(".json", ".xml")
        try:
            path = export_rekordbox_xml(self.project, output)
            self._set_status(f"Exported {len(self.project.library)} tracks -> {path}")
        except Exception as e:
            self._set_status(f"Export error: {e}")

    def _import_xml(self, path: str):
        from pymixter.core.rekordbox_xml import import_rekordbox_xml
        try:
            self._checkpoint("Import XML")
            before = len(self.project.library)
            import_rekordbox_xml(path, self.project)
            added = len(self.project.library) - before
            self._save_and_sync()
            self._set_status(f"Imported {added} tracks from XML")
        except Exception as e:
            self._set_status(f"Import error: {e}")

    def _open_project(self, path: str):
        try:
            if path.endswith(".xml"):
                from pymixter.core.rekordbox_xml import import_rekordbox_xml
                self._checkpoint("Open XML")
                self.project = import_rekordbox_xml(path)
                self.project._path = self.project_path
                self._save_and_sync()
                self._set_status(
                    f"Opened XML: {len(self.project.library)} tracks"
                )
            elif path.endswith(".json"):
                self.project = Project.load(path)
                self.project_path = path
                self._last_version = self.project.get_version()
                self._refresh_all()
                self._set_status(
                    f"Opened: {len(self.project.library)} tracks"
                )
            else:
                self._set_status("Use .json or .xml")
        except Exception as e:
            self._set_status(f"Open error: {e}")

    # ── Mix rendering / validation ─────────────────────────────

    def _render_mix(self, args: list[str]):
        """Render timeline to audio file (non-blocking)."""
        if not self.project.timeline:
            self._set_status("Timeline is empty — add tracks first")
            return
        output = args[0] if args else self.project_path.replace(".json", "_mix.wav")
        self._set_status(f"Rendering {len(self.project.timeline)} tracks...")

        def _do_render():
            return render_to_file(
                self.project, output,
                on_progress=lambda pos, total, msg: self.call_from_thread(
                    self._set_status, f"Rendering [{pos+1}/{total}] {msg}"
                ),
            )

        self.run_worker(_do_render, thread=True, exit_on_error=False,
                        name="render", exclusive=True, group="render")

    def _validate_mix(self):
        """Validate timeline for issues."""
        if not self.project.timeline:
            self._set_status("Timeline is empty")
            return
        warnings = validate_timeline(self.project)
        if not warnings:
            self._set_status("Timeline OK — no issues found")
        elif len(warnings) == 1:
            self._set_status(f"Warning: {warnings[0]}")
        else:
            self._set_status(f"{len(warnings)} warnings: {warnings[0]} ...")
            for w in warnings:
                log.warning("validate: %s", w)

    def _play_mix(self):
        """Render timeline and play it (non-blocking)."""
        if not self.project.timeline:
            self._set_status("Timeline is empty — add tracks first")
            return
        self._set_status("Rendering mix for playback...")

        def _do_render_and_play():
            audio, sr = render_timeline(
                self.project,
                on_progress=lambda pos, total, msg: self.call_from_thread(
                    self._set_status, f"Rendering [{pos+1}/{total}] {msg}"
                ),
            )
            return audio, sr

        self.run_worker(_do_render_and_play, thread=True, exit_on_error=False,
                        name="playmix", exclusive=True, group="render")

    # ── Timeline commands ──────────────────────────────────────

    def _handle_timeline_cmd(self, args: list[str]):
        """Handle :timeline subcommands."""
        if not args:
            self._set_status("timeline: append|move|remove|show")
            return
        sub = args[0]
        if sub == "append" and len(args) >= 2:
            try:
                self._checkpoint("Add to timeline")
                self.project.append_to_timeline(int(args[1]))
                self._save_and_sync()
                self._set_status(f"Added [{args[1]}] to timeline")
            except (ValueError, IndexError) as e:
                self._set_status(f"Error: {e}")
        elif sub == "move" and len(args) >= 3:
            try:
                self._checkpoint("Move timeline track")
                self.project.move_timeline_track(int(args[1]), int(args[2]))
                self._save_and_sync()
                self._set_status(f"Moved {args[1]} -> {args[2]}")
            except (ValueError, IndexError) as e:
                self._set_status(f"Error: {e}")
        elif sub == "remove" and len(args) >= 2:
            try:
                self._checkpoint("Remove from timeline")
                self.project.remove_from_timeline(int(args[1]))
                self._save_and_sync()
                self._set_status(f"Removed position {args[1]}")
            except (ValueError, IndexError) as e:
                self._set_status(f"Error: {e}")
        elif sub == "show":
            if not self.project.timeline:
                self._set_status("Timeline empty")
            else:
                names = [self.project.library[i].title[:15]
                         for i in self.project.timeline]
                self._set_status(" > ".join(names))
        else:
            self._set_status("timeline: append|move|remove|show")

    # ── Transition commands ──────────────────────────────────

    def _handle_transition_cmd(self, args: list[str]):
        """Handle :transition subcommands."""
        if not args:
            self._set_status("transition: edit|list|remove")
            return
        sub = args[0]
        if sub == "edit" and len(args) >= 2:
            try:
                pos = int(args[1])
                tr_type = args[2] if len(args) > 2 else "crossfade"
                bars = int(args[3]) if len(args) > 3 else 16
                self._checkpoint("Edit transition")
                self.project.set_transition(pos, tr_type, bars)
                self._save_and_sync()
                self._set_status(f"Transition [{pos}]: {tr_type} {bars}b")
            except (ValueError, IndexError) as e:
                self._set_status(f"Error: {e}")
        elif sub == "list":
            if not self.project.transitions:
                self._set_status("No transitions")
            else:
                parts = [f"[{t.from_track}] {t.type} {t.length_bars}b"
                         for t in self.project.transitions]
                self._set_status(" | ".join(parts))
        elif sub == "remove" and len(args) >= 2:
            try:
                pos = int(args[1])
                self._checkpoint("Remove transition")
                self.project.transitions = [
                    t for t in self.project.transitions if t.from_track != pos
                ]
                self._save_and_sync()
                self._set_status(f"Removed transition at [{pos}]")
            except ValueError:
                self._set_status("Usage: transition remove <pos>")
        else:
            self._set_status("transition: edit <pos> [type] [bars] | list | remove <pos>")

    # ── Cue point commands ──────────────────────────────────

    def _handle_cue_cmd(self, args: list[str]):
        """Handle :cue in/out commands for selected track."""
        if self._selected_track_idx is None:
            self._set_status("Select a track first")
            return
        if len(args) < 2:
            self._set_status("cue: in <sec> | out <sec>")
            return
        track = self.project.library[self._selected_track_idx]
        try:
            val = float(args[1])
            if args[0] == "in":
                self._checkpoint("Set cue in")
                track.cue_in = val
                self._save_and_sync()
                self._set_status(f"Cue in: {_fmt_time(val)}")
            elif args[0] == "out":
                self._checkpoint("Set cue out")
                track.cue_out = val
                self._save_and_sync()
                self._set_status(f"Cue out: {_fmt_time(val)}")
            else:
                self._set_status("cue: in <sec> | out <sec>")
        except ValueError:
            self._set_status("Usage: cue in|out <seconds>")

    # ── EQ commands ──────────────────────────────────────────

    def _handle_eq_cmd(self, args: list[str]):
        """Handle :eq low/mid/high/reset commands."""
        if not args:
            lo = self.player.deck_a.eq.low.gain_db
            mi = self.player.deck_a.eq.mid.gain_db
            hi = self.player.deck_a.eq.high.gain_db
            self._set_status(f"EQ: lo={lo:+.0f} mid={mi:+.0f} hi={hi:+.0f}")
            return
        sub = args[0]
        if sub == "reset":
            self.player.deck_a.eq.reset()
            self._set_status("EQ reset to 0/0/0")
        elif sub in ("low", "mid", "high") and len(args) >= 2:
            try:
                db = float(args[1])
                db = max(-12, min(12, db))
                getattr(self.player.deck_a.eq, f"set_{sub}")(db)
                self._set_status(f"EQ {sub}: {db:+.0f} dB")
            except ValueError:
                self._set_status(f"Usage: eq {sub} <dB>")
        else:
            self._set_status("eq: low|mid|high <dB> | reset")

    # ── BPM / beat grid editing ──────────────────────────────

    def _handle_bpm_cmd(self, args: list[str]):
        """Handle :bpm set/halve/double/tap/nudge commands."""
        if self._selected_track_idx is None:
            self._set_status("Select a track first")
            return
        track = self.project.library[self._selected_track_idx]
        if not args:
            self._set_status(
                f"BPM: {track.bpm or '?'} | bpm set <val> | halve | double | "
                f"nudge <+/-0.1>"
            )
            return
        sub = args[0]
        idx = self._selected_track_idx
        if sub == "set" and len(args) >= 2:
            try:
                new_bpm = float(args[1])
                if new_bpm < 30 or new_bpm > 300:
                    self._set_status("BPM must be 30–300")
                    return
                old_bpm = track.bpm
                self._checkpoint("Set BPM")
                self.project.set_bpm(idx, new_bpm)
                self._save_and_sync()
                self._set_status(f"BPM: {old_bpm} -> {track.bpm}")
            except ValueError:
                self._set_status("Usage: bpm set <value>")
        elif sub == "halve":
            if track.bpm:
                old = track.bpm
                self._checkpoint("Halve BPM")
                self.project.set_bpm(idx, track.bpm / 2)
                self._save_and_sync()
                self._set_status(f"BPM: {old} -> {track.bpm}")
            else:
                self._set_status("No BPM — analyze first")
        elif sub == "double":
            if track.bpm:
                old = track.bpm
                self._checkpoint("Double BPM")
                self.project.set_bpm(idx, track.bpm * 2)
                self._save_and_sync()
                self._set_status(f"BPM: {old} -> {track.bpm}")
            else:
                self._set_status("No BPM — analyze first")
        elif sub == "nudge" and len(args) >= 2:
            try:
                delta = float(args[1])
                if track.bpm:
                    old = track.bpm
                    self._checkpoint("Nudge BPM")
                    self.project.set_bpm(idx, track.bpm + delta)
                    self._save_and_sync()
                    self._set_status(f"BPM: {old} -> {track.bpm}")
                else:
                    self._set_status("No BPM — analyze first")
            except ValueError:
                self._set_status("Usage: bpm nudge <delta>")
        elif sub == "key" and len(args) >= 2:
            self._checkpoint("Set key")
            track.key = args[1]
            self._save_and_sync()
            self._set_status(f"Key: {track.key}")
        else:
            self._set_status("bpm: set <val> | halve | double | nudge <d> | key <K>")

    # ── Stem separation ──────────────────────────────────────

    def _handle_stems_cmd(self, args: list[str]):
        """Handle :stems [index] [force] command — separate track into stems."""
        force = "force" in args
        # Parse index from first numeric arg
        idx = self._selected_track_idx
        for a in args:
            try:
                idx = int(a)
                break
            except ValueError:
                continue
        if idx is None:
            self._set_status("Usage: stems [index] [force]")
            return
        if idx >= len(self.project.library):
            self._set_status(f"Track index {idx} out of range")
            return
        track = self.project.library[idx]
        if track.stems and not force:
            self._set_status(
                f"Stems already exist: {', '.join(track.stems.keys())}. "
                "Use :stems force to redo"
            )
            return

        stems_dir = str(self.project.project_dir / "stems" / Path(track.path).stem)
        self._set_status(f"Separating stems for {track.title}...")

        def _do_separate():
            from pymixter.core.stems import separate_track
            stems = separate_track(
                track.path, stems_dir,
                on_progress=lambda msg: self.call_from_thread(
                    self._set_status, msg
                ),
            )
            return idx, stems

        self.run_worker(_do_separate, thread=True, exit_on_error=False,
                        name="stems", exclusive=True, group="stems")

    # ── Crossfader / Deck B ──────────────────────────────────

    def _cmd_crossfader(self, args: list[str]):
        """Handle :xfader <0.0-1.0> command."""
        if not args:
            self._set_status(f"Crossfader: {self.player.crossfader:.2f} (0=A, 1=B)")
            return
        try:
            val = float(args[0])
            self.player.set_crossfader(val)
            self._set_status(f"Crossfader: {self.player.crossfader:.2f}")
        except ValueError:
            self._set_status("Usage: xfader <0.0-1.0>")

    def _cmd_deck_b(self, args: list[str]):
        """Handle :deckb <index> — load track into deck B (non-blocking)."""
        if not args:
            self._set_status("Usage: deckb <track_index>")
            return
        try:
            idx = int(args[0])
        except ValueError:
            self._set_status("Usage: deckb <track_index>")
            return
        if idx >= len(self.project.library):
            self._set_status(f"Track index {idx} out of range")
            return
        track = self.project.library[idx]
        path = track.path
        title = track.title
        self._set_status(f"Loading deck B: {title}...")

        def _do_load():
            from pedalboard.io import AudioFile
            with AudioFile(path) as f:
                data = f.read(f.frames)
                sr = f.samplerate
            return data, sr, title

        self.run_worker(_do_load, thread=True, exit_on_error=False,
                        name="deckb", exclusive=True, group="deckb")

    # ── Transition preview ────────────────────────────────────

    def _preview_transition(self, args: list[str]):
        """Render and play just the transition zone (non-blocking)."""
        if not args:
            self._set_status("Usage: preview <timeline_pos>")
            return
        try:
            pos = int(args[0])
        except ValueError:
            self._set_status("Usage: preview <timeline_pos>")
            return
        if pos < 0 or pos >= len(self.project.timeline) - 1:
            self._set_status(f"No transition at position {pos}")
            return

        self._set_status(f"Rendering transition preview [{pos}]...")

        def _do_preview():
            return render_transition_preview(self.project, pos)

        self.run_worker(_do_preview, thread=True, exit_on_error=False,
                        name="preview_transition", exclusive=True, group="render")

    def action_add_to_timeline(self):
        if self._selected_track_idx is None:
            self._set_status("No track selected")
            return
        self._checkpoint("Add to timeline")
        self.project.append_to_timeline(self._selected_track_idx)
        self._save_and_sync()
        self._set_status(f"Added track [{self._selected_track_idx}] to timeline")

    def action_reload_project(self):
        self._load_project()
        self._last_version = self.project.get_version()
        self._refresh_all()
        self._set_status("Project reloaded")

    def action_save_project(self):
        self._save_and_sync()
        self._set_status("Project saved")

    def action_undo(self):
        desc = self.history.undo(self.project)
        if desc:
            self.project.save()
            self._last_version = self.project.get_version()
            self._refresh_all()
            self._set_status(f"Undo: {desc}")
        else:
            self._set_status("Nothing to undo")

    def action_redo(self):
        desc = self.history.redo(self.project)
        if desc:
            self.project.save()
            self._last_version = self.project.get_version()
            self._refresh_all()
            self._set_status(f"Redo: {desc}")
        else:
            self._set_status("Nothing to redo")

    def action_tab_library(self):
        self._switch_tab("tab-library")

    def action_tab_timeline(self):
        self._switch_tab("tab-timeline")

    def action_tab_info(self):
        self._switch_tab("tab-info")
