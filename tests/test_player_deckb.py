"""Tests for Player dual-deck and crossfader."""

import numpy as np
from pymixter.core.player import Player, PlayerState


def test_crossfader_default():
    player = Player()
    assert player.crossfader == 0.0


def test_set_crossfader_clamps():
    player = Player()
    player.set_crossfader(0.5)
    assert player.crossfader == 0.5
    player.set_crossfader(-1.0)
    assert player.crossfader == 0.0
    player.set_crossfader(2.0)
    assert player.crossfader == 1.0


def test_load_deck_b_audio():
    player = Player()
    # Create fake stereo audio (channels, frames)
    audio = np.random.randn(2, 44100).astype(np.float32)
    player.load_deck_b_audio(audio, 44100, label="test")
    assert player._samples_b is not None
    assert player._total_frames_b == 44100


def test_process_audio_deck_a_only():
    """With crossfader at 0, only deck A should be heard."""
    player = Player()
    audio = np.ones((2, 1024), dtype=np.float32) * 0.5
    player._sr = 44100
    player.crossfader = 0.0
    result = player._process_audio(audio)
    # Should be non-zero (processed through deck A + master)
    assert result.shape == (2, 1024)
    assert np.any(result != 0)


def test_process_audio_with_deck_b():
    """With crossfader at 0.5, both decks should mix."""
    player = Player()
    audio_a = np.ones((2, 1024), dtype=np.float32) * 0.5
    audio_b = np.ones((2, 2048), dtype=np.float32) * 0.3

    player._sr = 44100
    player._samples_b = audio_b
    player._total_frames_b = 2048
    player._position_b = 0
    player.crossfader = 0.5

    result = player._process_audio(audio_a)
    assert result.shape == (2, 1024)
    # Position B should have advanced
    assert player._position_b == 1024
    player.close()
