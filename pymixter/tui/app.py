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

from pymixter.core.project import Project, find_audio_files
from pymixter.core.analysis import analyze_track
from pymixter.core.automix import automix
from pymixter.core.player import Player, PlayerState
from pymixter.core.recent import get_recent, add_recent
from pymixter.core.mixer import render_timeline, render_to_file, validate_timeline
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
        self._load_project()

    def _load_project(self):
        p = Path(self.project_path)
        if p.exists():
            self.project = Project.load(self.project_path)
        else:
            self.project = Project(_path=self.project_path)
            self.project.save()
        add_recent(self.project_path)

    def _save_and_sync(self):
        """Save project and update version tracker."""
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

        dispatch = {
            "help": lambda: self._set_status(
                "add scan analyze automix render validate playmix export import open save quit"
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
        }

        if verb in dispatch:
            dispatch[verb]()
        elif verb == "play":
            idx = int(args[0]) if args else self._selected_track_idx
            if idx is not None:
                self._play_track(idx)
            else:
                self._set_status("Usage: play [index]")
        elif verb == "seek" and args:
            try:
                self.player.seek(float(args[0]))
            except ValueError:
                self._set_status("Usage: seek <seconds>")
        elif verb == "automix":
            self._run_automix(args)
        elif verb == "add" and args:
            self._import_file(" ".join(args))
        elif verb == "scan" and args:
            self._scan_directory(" ".join(args))
        elif verb == "analyze":
            idx = int(args[0]) if args else self._selected_track_idx
            if idx is not None:
                self._analyze_track(idx)
            else:
                self._set_status("Usage: analyze [index]")
        elif verb == "timeline" and len(args) >= 2 and args[0] == "append":
            try:
                self.project.append_to_timeline(int(args[1]))
                self._save_and_sync()
                self._set_status(f"Added [{args[1]}] to timeline")
            except (ValueError, IndexError) as e:
                self._set_status(f"Error: {e}")
        elif verb == "render":
            self._render_mix(args)
        elif verb == "validate":
            self._validate_mix()
        elif verb == "playmix":
            self._play_mix()
        elif verb == "export":
            self._export_project(args)
        elif verb == "import" and args:
            self._import_xml(" ".join(args))
        elif verb == "open" and args:
            self._open_project(" ".join(args))
        else:
            self._set_status(f"Unknown: {verb}. Type :help")

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
            track = self.project.import_track(path)
            self._save_and_sync()
            self._set_status(f"Imported: {track.title}")
            self._switch_tab("tab-library")
        except FileNotFoundError:
            self._set_status(f"File not found: {path}")
        except Exception as e:
            self._set_status(f"Error: {e}")

    def _scan_directory(self, directory: str):
        files = find_audio_files(directory)
        if not files:
            self._set_status(f"No audio files in {directory}")
            return
        for f in files:
            self.project.import_track(str(f))
        self._save_and_sync()
        self._set_status(f"Imported {len(files)} tracks from {Path(directory).name}/")
        self._switch_tab("tab-library")

    def _analyze_track(self, idx: int):
        if idx >= len(self.project.library):
            self._set_status(f"Track index {idx} out of range")
            return
        track = self.project.library[idx]
        self._set_status(f"Analyzing {track.title}...")
        try:
            analysis = analyze_track(track.path, full=True)
            analysis.pop("_waveform", None)
            track.bpm = analysis.get("bpm")
            track.key = analysis.get("key")
            track.duration = analysis.get("duration", 0)
            track.beats = analysis.get("beats", [])
            track.cue_in = analysis.get("cue_in")
            track.cue_out = analysis.get("cue_out")
            track.energy = analysis.get("energy", [])
            self._save_and_sync()
            bars = track.bars
            self._set_status(
                f"Analyzed: {track.title} — {track.bpm} BPM, {track.key}, {bars} bars"
            )
        except Exception as e:
            self._set_status(f"Analysis failed: {e}")

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
        """Render timeline to WAV file."""
        if not self.project.timeline:
            self._set_status("Timeline is empty — add tracks first")
            return
        output = args[0] if args else self.project_path.replace(".json", "_mix.wav")
        self._set_status(f"Rendering {len(self.project.timeline)} tracks...")
        try:
            path = render_to_file(
                self.project, output,
                on_progress=lambda pos, total, msg: self._set_status(
                    f"Rendering [{pos+1}/{total}] {msg}"
                ),
            )
            self._set_status(f"Rendered to {path}")
        except Exception as e:
            self._set_status(f"Render error: {e}")

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
        """Render timeline and play it."""
        if not self.project.timeline:
            self._set_status("Timeline is empty — add tracks first")
            return
        self._set_status("Rendering mix for playback...")
        try:
            audio, sr = render_timeline(
                self.project,
                on_progress=lambda pos, total, msg: self._set_status(
                    f"Rendering [{pos+1}/{total}] {msg}"
                ),
            )
            if audio.shape[1] == 0:
                self._set_status("Nothing to play — rendered empty audio")
                return
            self.player.load_audio(audio, sr, label="mix")
            self.player.play()
            duration = audio.shape[1] / sr
            self._set_status(f">> Playing mix ({_fmt_time(duration)})")
        except Exception as e:
            self._set_status(f"Mix playback error: {e}")

    def action_add_to_timeline(self):
        if self._selected_track_idx is None:
            self._set_status("No track selected")
            return
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

    def action_tab_library(self):
        self._switch_tab("tab-library")

    def action_tab_timeline(self):
        self._switch_tab("tab-timeline")

    def action_tab_info(self):
        self._switch_tab("tab-info")
