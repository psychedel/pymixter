"""Recent projects history stored in ~/.mix_recent."""

from __future__ import annotations

from pathlib import Path

RECENT_FILE = Path.home() / ".mix_recent"
MAX_RECENT = 10


def get_recent() -> list[str]:
    """Return list of recent project paths, newest first."""
    if not RECENT_FILE.exists():
        return []
    lines = RECENT_FILE.read_text().strip().splitlines()
    # Filter out non-existent files
    return [p for p in lines if Path(p).exists()][:MAX_RECENT]


def add_recent(path: str):
    """Add a project path to the top of the recent list."""
    resolved = str(Path(path).resolve())
    recent = get_recent()
    # Remove if already present, then prepend
    recent = [p for p in recent if p != resolved]
    recent.insert(0, resolved)
    recent = recent[:MAX_RECENT]
    RECENT_FILE.write_text("\n".join(recent) + "\n")
