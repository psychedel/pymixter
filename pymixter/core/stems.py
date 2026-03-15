"""Stem separation using audio-separator (htdemucs).

Splits a track into vocals, drums, bass, and other (melody/instruments).
Results are stored in project's stems/ directory and referenced in Track.stems.
"""

from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger(__name__)

# Default model: htdemucs is fast and good quality for 4-stem
DEFAULT_MODEL = "Demucs v4: htdemucs"

# Stem names produced by htdemucs
STEM_NAMES = ("vocals", "drums", "bass", "other")


def separate_track(audio_path: str, output_dir: str,
                   model: str = DEFAULT_MODEL,
                   on_progress: callable | None = None) -> dict[str, str]:
    """Separate a track into stems.

    Args:
        audio_path: path to audio file
        output_dir: directory to write stem files
        model: separator model name
        on_progress: optional callback(message)

    Returns:
        dict mapping stem name -> output file path
    """
    from audio_separator.separator import Separator

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    if on_progress:
        on_progress(f"Loading model {model}...")

    sep = Separator(
        output_dir=str(out),
        output_format="WAV",
        sample_rate=44100,
        log_level=logging.WARNING,
    )
    sep.load_model(model_filename=model)

    if on_progress:
        on_progress("Separating stems...")

    output_files = sep.separate(audio_path)

    # Map output files to stem names
    # audio-separator outputs files like: "trackname_(Vocals).wav", "trackname_(Drums).wav"
    # or "trackname_(Instrumental).wav" depending on model
    stems: dict[str, str] = {}
    for fpath in output_files:
        fpath = str(fpath)
        fname_lower = Path(fpath).stem.lower()
        for stem in STEM_NAMES:
            if stem in fname_lower:
                stems[stem] = fpath
                break
        else:
            # For 2-stem models: vocal/instrumental
            if "instrumental" in fname_lower or "no_vocal" in fname_lower:
                stems["instrumental"] = fpath
            elif "vocal" in fname_lower:
                stems["vocals"] = fpath
            else:
                stems["other"] = fpath

    log.info("Separated %s -> %d stems: %s", audio_path, len(stems),
             list(stems.keys()))
    return stems


def list_models() -> dict[str, list[str]]:
    """Return available separation models grouped by type."""
    from audio_separator.separator import Separator

    sep = Separator(log_level=logging.WARNING)
    all_models = sep.list_supported_model_files()
    result = {}
    for category, models in all_models.items():
        result[category] = list(models.keys())
    return result
