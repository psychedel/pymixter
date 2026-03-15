"""Undo/redo system using project snapshots."""

from __future__ import annotations

import copy
import json
from dataclasses import asdict

from pymixter.core.project import Project, Track, Transition


def _snapshot(project: Project) -> dict:
    """Capture project state as a serializable dict."""
    return {
        "name": project.name,
        "library": [asdict(t) for t in project.library],
        "timeline": list(project.timeline),
        "transitions": [asdict(t) for t in project.transitions],
    }


def _restore(project: Project, snapshot: dict):
    """Restore project state from a snapshot."""
    project.name = snapshot["name"]
    project.library = [Track(**t) for t in snapshot["library"]]
    project.timeline = list(snapshot["timeline"])
    project.transitions = [Transition(**t) for t in snapshot["transitions"]]


class History:
    """Snapshot-based undo/redo for Project mutations."""

    MAX_UNDO = 50

    def __init__(self):
        self._undo_stack: list[tuple[str, dict]] = []  # (description, snapshot_before)
        self._redo_stack: list[tuple[str, dict]] = []

    def checkpoint(self, project: Project, description: str):
        """Save current state before a mutation."""
        self._undo_stack.append((description, _snapshot(project)))
        self._redo_stack.clear()
        if len(self._undo_stack) > self.MAX_UNDO:
            self._undo_stack.pop(0)

    def undo(self, project: Project) -> str | None:
        """Undo last action. Returns description or None if nothing to undo."""
        if not self._undo_stack:
            return None
        desc, before = self._undo_stack.pop()
        self._redo_stack.append((desc, _snapshot(project)))
        _restore(project, before)
        return desc

    def redo(self, project: Project) -> str | None:
        """Redo last undone action. Returns description or None."""
        if not self._redo_stack:
            return None
        desc, after = self._redo_stack.pop()
        self._undo_stack.append((desc, _snapshot(project)))
        _restore(project, after)
        return desc

    @property
    def can_undo(self) -> bool:
        return len(self._undo_stack) > 0

    @property
    def can_redo(self) -> bool:
        return len(self._redo_stack) > 0
