"""Take a headless screenshot of the TUI for visual inspection."""

import tempfile
from pathlib import Path

import pytest

from pymixter.core.project import Project
from pymixter.tui.app import MixApp


@pytest.fixture
def demo_project():
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name

    proj = Project(name="Deep House Set")
    proj.add_track("/music/Solomun - After Rain.mp3", bpm=122.0, key="Am", duration=392.0)
    proj.add_track("/music/Tale Of Us - Endless.flac", bpm=124.0, key="Cm", duration=445.0)
    proj.add_track("/music/Adriatique - Nude.mp3", bpm=121.5, key="Gm", duration=378.0)
    proj.add_track("/music/Stephan Bodzin - Powers of Ten.flac", bpm=126.0, key="Dm", duration=510.0)
    proj.append_to_timeline(0)
    proj.append_to_timeline(1)
    proj.add_transition(0, 1, type="eq_fade", length_bars=32)
    proj.save(path)
    yield path
    Path(path).unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_take_screenshot(demo_project):
    app = MixApp(project_path=demo_project)
    async with app.run_test(size=(80, 40)) as pilot:
        # Select first track to populate Track Info (Library + Track Info are side by side)
        await pilot.press("enter")
        await pilot.pause()

        # Screenshot 1: Main view (Library + Track Info side by side, Timeline below)
        svg = app.export_screenshot()
        out = Path("/home/user/mix/screenshot_main.svg")
        out.write_text(svg)
        print(f"\nScreenshot saved to {out}")

        # Screenshot 2: Switch to timeline tab
        app.query_one("#bottom-tabs").active = "tab-timeline"
        await pilot.pause()
        svg = app.export_screenshot()
        out = Path("/home/user/mix/screenshot_timeline.svg")
        out.write_text(svg)
        print(f"\nScreenshot saved to {out}")

        # Screenshot 3: Switch to zoom tab
        app.query_one("#bottom-tabs").active = "tab-zoom"
        await pilot.pause()
        svg = app.export_screenshot()
        out = Path("/home/user/mix/screenshot_zoom.svg")
        out.write_text(svg)
        print(f"\nScreenshot saved to {out}")

        assert len(svg) > 100
