"""Headless TUI tests using Textual's Pilot API.

This lets Claude verify TUI behavior without a terminal.
"""

import json
import tempfile
from pathlib import Path

import pytest

from pymixter.core.project import Project
from pymixter.tui.app import MixApp


@pytest.fixture
def project_file():
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name

    proj = Project(name="Test Mix")
    proj.add_track("/fake/track_a.mp3", bpm=128.0, key="Am", duration=300.0)
    proj.add_track("/fake/track_b.mp3", bpm=130.0, key="Cm", duration=240.0)
    proj.save(path)
    yield path
    Path(path).unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_app_starts(project_file):
    app = MixApp(project_path=project_file)
    async with app.run_test() as pilot:
        # App should display the header
        assert app.title == "DJ Mix Studio"
        # Library should have 2 tracks
        assert len(app.project.library) == 2


@pytest.mark.asyncio
async def test_reload_picks_up_external_changes(project_file):
    app = MixApp(project_path=project_file)
    async with app.run_test() as pilot:
        # Simulate external CLI adding a track
        proj = Project.load(project_file)
        proj.add_track("/fake/track_c.mp3", bpm=126.0, key="Gm", duration=360.0)
        proj.save()

        # Press 'r' to reload
        await pilot.press("r")
        assert len(app.project.library) == 3
        assert app.project.library[2].title == "track_c"


@pytest.mark.asyncio
async def test_save_project(project_file):
    app = MixApp(project_path=project_file)
    async with app.run_test() as pilot:
        app.project.add_track("/fake/new.mp3", bpm=120.0, key="F", duration=180.0)
        await pilot.press("s")

        # Verify saved to disk
        reloaded = Project.load(project_file)
        assert len(reloaded.library) == 3
