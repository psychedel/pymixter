# PyMixter

> **Super early alpha** — expect breaking changes, missing features, and rough edges.

Terminal-based AI DJ mix agent and studio. Analyze tracks, build playlists, automix by key/BPM, play with real-time EQ — all from the terminal or browser.

![PyMixter TUI](docs/tui.png)

## Features

- **4 interfaces** — TUI (terminal), Web (browser), CLI (scripting), MCP (AI agents)
- **Audio analysis** — BPM, key, beat grid, cue points, energy, waveform, LUFS, danceability, chords (essentia)
- **Harmonic mixing** — Camelot wheel compatibility, automatic key matching via pitch shift (±6 semitones)
- **5 transition types** — crossfade, EQ fade, cut, echo out, filter sweep (LadderFilter)
- **DSP engine** — pedalboard-powered: 3-band EQ, compressor, limiter, noise gate, pitch shift, ladder filter
- **Dual-deck player** — independent decks with per-deck effects, crossfader, master compression
- **Stem separation** — vocals/drums/bass/other via htdemucs (audio-separator)
- **Automix** — auto-arrange by key/BPM, auto-select transition types and lengths
- **Tempo sync** — time-stretch tracks to match BPM during transitions
- **Loudness normalization** — ReplayGain auto-applied during rendering for consistent volume
- **Undo/redo** — full project state history
- **Rekordbox XML** — import/export for interop with Rekordbox, Mixxx, Traktor, Serato
- **Multi-format render** — WAV, MP3 (V0/V2/320k), FLAC output

## Quick start

```bash
uv run python main.py                              # launch TUI
uv run python main.py web                           # launch in browser
uv run python main.py cli scan ~/Music --analyze    # import + analyze
uv run python main.py cli automix                   # auto-arrange by key/BPM
uv run python main.py cli render -o mix.wav         # render to file
uv run python main.py cli export                    # export to Rekordbox XML
```

## Web mode

Run the same TUI interface in any browser via textual-serve:

```bash
uv run python main.py web                     # http://localhost:8000
uv run python main.py web --port 9000         # custom port
uv run python main.py web --host 0.0.0.0      # expose to network
```

## TUI keys

| Key | Action |
|-----|--------|
| `Space` / `p` | Play / pause |
| `[` `]` | Seek -5s / +5s |
| `x` | Stop |
| `o` | Open file browser |
| `a` | Add selected track to timeline |
| `/` | Fuzzy search library |
| `:` | Command console |
| `l` | Open recent project |
| `1` `2` | Switch bottom tabs (Timeline, Zoom) |
| `u` / `Ctrl+r` | Undo / redo |
| `s` | Save project |
| `r` | Reload project |
| `q` | Quit |

## Console commands (`:`)

### Library & playback

`add <file>` `scan <dir>` `analyze [idx]` `play [idx]` `seek <sec>` `stop` `suggest` `help`

### Timeline & transitions

`timeline append|move|remove|show` `transition edit|offset|info|list|remove` `automix` `validate` `preview <pos>` `zoom <pos>|clear`

### Mixing & DSP

`deckb <idx>` `xfader <0-1>` `eq low|mid|high <dB>|reset` `gain <dB>` `bpm set|halve|double|nudge|key` `stems [idx]` `cue in|out <sec>|snap|now|show`

### Beat grid editing

`grid nudge <±ms>` `grid align <beat> <time>` `grid stretch <b1> <t1> <b2> <t2>` `grid info`

### Render & I/O

`render [output.wav]` `playmix` `export [file.xml]` `import <file.xml>` `open <file>` `save`

## Transition types

| Type | Description | Bars |
|------|-------------|------|
| `crossfade` | Linear blend between tracks | 16 |
| `eq_fade` | Bass swap — progressive HP/LP with volume fade | 32 |
| `filter_sweep` | Resonant LadderFilter LP/HP sweep with rising resonance | 16 |
| `echo_out` | Echo/reverb trail on outgoing, crossfade into incoming | 8 |
| `cut` | Hard cut with 50ms micro-crossfade | 4 |

Automix selects type based on key compatibility and BPM difference.

## MCP server (AI agent integration)

PyMixter includes an MCP server with 25 tools for AI-assisted mix planning:

```bash
uv run python main.py mcp                    # start MCP server (stdio)
```

**Tools:** `project_open` `project_info` `library_list` `library_scan` `track_analyze` `track_analyze_all` `track_info` `track_set_cue` `track_set_bpm` `track_grid_nudge` `track_grid_stretch` `timeline_append` `timeline_remove` `timeline_reorder` `transition_set` `transition_list` `mix_compatibility_matrix` `mix_suggest_next` `mix_suggest_order` `mix_automix` `mix_energy_profile` `mix_validate` `mix_render`

## Using with Claude Code

PyMixter is designed to be driven by AI agents. Claude Code can control the project via CLI, observe the TUI through tmux, or use the MCP server directly.

### CLI workflow (recommended for agents)

```bash
# Import and analyze a music folder
uv run python main.py cli scan ~/Music/edm --analyze

# Check what we have
uv run python main.py cli library

# Auto-arrange into a mix (orders by key compatibility + BPM)
uv run python main.py cli automix

# View the result
uv run python main.py cli timeline show

# Render the mix
uv run python main.py cli render -o my_mix.wav

# Export for other DJ software
uv run python main.py cli export -o my_mix.xml
```

### MCP workflow

Add to your Claude Code MCP config:

```json
{
  "mcpServers": {
    "pymixter": {
      "command": "uv",
      "args": ["run", "python", "main.py", "mcp"]
    }
  }
}
```

Then Claude Code can scan tracks, analyze, automix, validate, and render — all through structured tool calls.

### Observing the TUI via tmux

```bash
# Start TUI in background tmux session
tmux new-session -d -s mix -x 56 -y 30
tmux send-keys -t mix 'uv run python main.py' Enter

# "Screenshot" the terminal at any time
tmux capture-pane -t mix -p

# Send keystrokes
tmux send-keys -t mix Space        # play/pause
tmux send-keys -t mix ':' && tmux send-keys -t mix 'automix' Enter
```

## Requirements

- Python 3.13+
- System: `libsndfile`, `portaudio` (for audio playback)

## License

AGPL-3.0-or-later
