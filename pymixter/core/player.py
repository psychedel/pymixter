"""Audio playback engine with pedalboard effects processing.

Uses sounddevice for output, pedalboard for real-time effects (EQ, gain,
pitch shift, compression, etc.).
"""

from __future__ import annotations

import threading
import logging
from enum import Enum
from pathlib import Path

import numpy as np
import sounddevice as sd
from pedalboard import (
    Pedalboard, Gain, Compressor, Limiter,
    LowShelfFilter, HighShelfFilter, PeakFilter,
    HighpassFilter, LowpassFilter, LadderFilter,
    PitchShift, Delay, Reverb, Chorus,
)
from pedalboard.io import AudioFile

log = logging.getLogger(__name__)


class PlayerState(Enum):
    STOPPED = "stopped"
    PLAYING = "playing"
    PAUSED = "paused"


class EQ:
    """3-band DJ EQ (low / mid / high) using pedalboard filters."""

    def __init__(self):
        self.low = LowShelfFilter(cutoff_frequency_hz=200, gain_db=0.0)
        self.mid = PeakFilter(cutoff_frequency_hz=1000, gain_db=0.0, q=0.7)
        self.high = HighShelfFilter(cutoff_frequency_hz=5000, gain_db=0.0)
        self._board = Pedalboard([self.low, self.mid, self.high])

    def set_low(self, db: float):
        self.low.gain_db = db

    def set_mid(self, db: float):
        self.mid.gain_db = db

    def set_high(self, db: float):
        self.high.gain_db = db

    def reset(self):
        self.low.gain_db = 0.0
        self.mid.gain_db = 0.0
        self.high.gain_db = 0.0

    def process(self, audio: np.ndarray, sr: int) -> np.ndarray:
        return self._board(audio, sr, reset=False)


class Deck:
    """Single playback deck with independent EQ, gain, and effects."""

    def __init__(self):
        self.eq = EQ()
        self.gain = Gain(gain_db=0.0)
        self.effects: list = []  # additional pedalboard plugins
        self._board = Pedalboard([])

    def _rebuild_board(self):
        """Rebuild the processing chain."""
        plugins = [self.eq.low, self.eq.mid, self.eq.high, self.gain]
        plugins.extend(self.effects)
        self._board = Pedalboard(plugins)

    def process(self, audio: np.ndarray, sr: int) -> np.ndarray:
        """Process audio through EQ + gain + effects chain."""
        self._rebuild_board()
        return self._board(audio, sr, reset=False)


class Player:
    """Dual-deck audio player with pedalboard effects.

    Designed to be driven from both TUI and CLI.
    Thread-safe: playback runs in a background stream callback.

    Currently uses Deck A for single-track playback.
    Deck B and crossfader are ready for mix transitions.
    """

    BLOCK_SIZE = 2048

    def __init__(self):
        self._lock = threading.Lock()
        self._state = PlayerState.STOPPED
        self._stream: sd.OutputStream | None = None

        # Audio data (channels-first for pedalboard: shape = (channels, frames))
        self._samples: np.ndarray | None = None
        self._sr: int = 44100
        self._channels: int = 2
        self._position: int = 0  # current sample frame
        self._total_frames: int = 0

        # Loaded file info
        self._current_path: str | None = None

        # Decks & master
        self.deck_a = Deck()
        self.deck_b = Deck()
        self.crossfader: float = 0.0  # 0.0 = full A, 1.0 = full B
        self.master_gain = Gain(gain_db=0.0)
        self.master_limiter = Limiter(threshold_db=-1.0)
        self._master_board = Pedalboard([self.master_gain, self.master_limiter])

        # Callbacks
        self.on_position: callable | None = None
        self.on_finish: callable | None = None

    # ── Properties ───────────────────────────────────────────

    @property
    def state(self) -> PlayerState:
        return self._state

    @property
    def position(self) -> float:
        """Current position in seconds."""
        with self._lock:
            return self._position / self._sr if self._sr else 0.0

    @property
    def duration(self) -> float:
        """Total duration in seconds."""
        with self._lock:
            return self._total_frames / self._sr if self._sr else 0.0

    @property
    def progress(self) -> float:
        """Playback progress 0.0–1.0."""
        with self._lock:
            if self._total_frames == 0:
                return 0.0
            return self._position / self._total_frames

    @property
    def current_path(self) -> str | None:
        return self._current_path

    # ── Load ─────────────────────────────────────────────────

    def load(self, path: str):
        """Load an audio file into memory. Stops current playback."""
        self.stop()
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(f"Audio file not found: {path}")

        # Use pedalboard's AudioFile for reading (supports mp3, flac, wav, ogg, aiff)
        with AudioFile(str(p)) as f:
            data = f.read(f.frames)  # shape: (channels, frames)
            sr = f.samplerate
            channels = f.num_channels

        with self._lock:
            self._samples = data
            self._sr = sr
            self._channels = channels
            self._total_frames = data.shape[1]
            self._position = 0
            self._current_path = str(p)

        log.info("Loaded %s (%d ch, %d Hz, %.1fs)",
                 p.name, channels, sr, self.duration)

    def load_audio(self, audio: np.ndarray, sr: int, label: str = "mix"):
        """Load pre-rendered audio (e.g., from mixer). Shape: (channels, frames)."""
        self.stop()
        with self._lock:
            self._samples = audio
            self._sr = sr
            self._channels = audio.shape[0]
            self._total_frames = audio.shape[1]
            self._position = 0
            self._current_path = label
        log.info("Loaded %s (%d ch, %d Hz, %.1fs)",
                 label, self._channels, sr, self.duration)

    # ── Transport ────────────────────────────────────────────

    def play(self, path: str | None = None):
        """Start or resume playback. Optionally load a new file first."""
        if path:
            self.load(path)

        if self._samples is None:
            return

        if self._state == PlayerState.PAUSED and self._stream is not None:
            self._state = PlayerState.PLAYING
            self._stream.start()
            return

        self._stop_stream()
        self._state = PlayerState.PLAYING
        self._stream = sd.OutputStream(
            samplerate=self._sr,
            channels=self._channels,
            blocksize=self.BLOCK_SIZE,
            dtype="float32",
            callback=self._audio_callback,
            finished_callback=self._stream_finished,
        )
        self._stream.start()

    def pause(self):
        """Pause playback (keeps position)."""
        if self._state != PlayerState.PLAYING:
            return
        self._state = PlayerState.PAUSED
        if self._stream is not None:
            self._stream.stop()

    def toggle(self):
        """Play/pause toggle."""
        if self._state == PlayerState.PLAYING:
            self.pause()
        else:
            self.play()

    def stop(self):
        """Stop playback and reset position."""
        self._stop_stream()
        with self._lock:
            self._state = PlayerState.STOPPED
            self._position = 0

    def seek(self, seconds: float):
        """Seek to absolute position in seconds."""
        with self._lock:
            frame = int(seconds * self._sr)
            self._position = max(0, min(frame, self._total_frames))

    def seek_relative(self, delta: float):
        """Seek relative to current position (e.g., +5.0 or -5.0 seconds)."""
        self.seek(self.position + delta)

    # ── Audio callback ───────────────────────────────────────

    def _audio_callback(self, outdata: np.ndarray, frames: int,
                        _time_info, _status):
        """sounddevice callback — reads samples and applies effects."""
        with self._lock:
            if self._samples is None or self._state != PlayerState.PLAYING:
                outdata[:] = 0
                return

            end = self._position + frames
            if end > self._total_frames:
                remaining = self._total_frames - self._position
                if remaining > 0:
                    # Get chunk in channels-first format for pedalboard
                    chunk = self._samples[:, self._position:self._total_frames]
                    processed = self._process_audio(chunk)
                    # sounddevice expects (frames, channels)
                    outdata[:remaining] = processed.T
                outdata[remaining:] = 0
                self._position = self._total_frames
                raise sd.CallbackStop
            else:
                chunk = self._samples[:, self._position:end]
                processed = self._process_audio(chunk)
                outdata[:] = processed.T
                self._position = end

    def _process_audio(self, chunk: np.ndarray) -> np.ndarray:
        """Process audio chunk through deck A effects + master chain.

        Args:
            chunk: audio in channels-first format (channels, frames)

        Returns:
            Processed audio in channels-first format.
        """
        # Process through deck A
        out = self.deck_a.process(chunk, self._sr)
        # Master chain (gain + limiter)
        out = self._master_board(out, self._sr, reset=False)
        return out

    def _stream_finished(self):
        """Called by sounddevice when stream ends."""
        if self._state == PlayerState.PAUSED:
            return
        self._state = PlayerState.STOPPED
        if self.on_finish:
            self.on_finish()

    def _stop_stream(self):
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None

    # ── Cleanup ──────────────────────────────────────────────

    def close(self):
        """Release all resources."""
        self._stop_stream()
        with self._lock:
            self._samples = None
            self._state = PlayerState.STOPPED

    def __del__(self):
        self.close()
