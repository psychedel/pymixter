"""Tests for MCP server tools — verifies structured API returns."""

import json
import tempfile
from pathlib import Path

import pytest

from pymixter.core.project import Project, Track
from pymixter.mcp.server import (
    SESSION, TOOLS, handle_request,
    project_open, project_info, library_list, track_info,
    track_set_cue, track_set_bpm, track_grid_nudge,
    timeline_append, timeline_remove,
    transition_set, transition_list,
    mix_suggest_next, mix_suggest_order, mix_compatibility_matrix,
    mix_energy_profile, mix_validate,
)


@pytest.fixture
def tmp_project(tmp_path):
    """Create a temporary project with test tracks."""
    proj = Project(name="Test Mix", _path=str(tmp_path / "test.json"))
    proj.library = [
        Track(path="/a.mp3", title="Track A", bpm=128, key="Am",
              duration=240, beats=[i * 0.46875 for i in range(512)],
              cue_in=10, cue_out=220, energy=[0.3 + 0.4 * (i/100) for i in range(100)]),
        Track(path="/b.mp3", title="Track B", bpm=126, key="Cm",
              duration=300, beats=[i * 0.476 for i in range(630)],
              cue_in=5, cue_out=280, energy=[0.5] * 100),
        Track(path="/c.mp3", title="Track C", bpm=130, key="Em",
              duration=200, energy=[0.7] * 100),
        Track(path="/d.mp3", title="Track D", bpm=128, key="Fm",
              duration=250),
    ]
    proj.save()
    SESSION.project_path = str(tmp_path / "test.json")
    SESSION.project = None  # force reload
    return proj


class TestProjectTools:
    def test_project_open(self, tmp_project):
        result = project_open({"path": SESSION.project_path})
        assert result["name"] == "Test Mix"
        assert result["library_count"] == 4

    def test_project_info(self, tmp_project):
        SESSION.load()
        result = project_info({})
        assert result["library_count"] == 4
        assert result["timeline"] == []


class TestLibraryTools:
    def test_library_list(self, tmp_project):
        SESSION.load()
        result = library_list({})
        assert result["total"] == 4
        assert result["tracks"][0]["title"] == "Track A"
        assert result["tracks"][0]["bpm"] == 128
        assert result["tracks"][0]["camelot"] == "8A"

    def test_track_info_detailed(self, tmp_project):
        SESSION.load()
        result = track_info({"index": 0})
        assert result["title"] == "Track A"
        assert result["has_beats"] is True
        assert result["has_energy"] is True
        assert "energy_profile" in result
        assert len(result["energy_profile"]) == 16
        assert "grid_bpm" in result

    def test_track_info_out_of_range(self, tmp_project):
        SESSION.load()
        result = track_info({"index": 99})
        assert "error" in result


class TestTrackManipulation:
    def test_set_cue(self, tmp_project):
        SESSION.load()
        result = track_set_cue({"index": 0, "cue_in": 15.5, "cue_out": 200.0})
        assert result["cue_in"] == 15.5
        assert result["cue_out"] == 200.0

    def test_set_cue_with_snap(self, tmp_project):
        SESSION.load()
        result = track_set_cue({"index": 0, "cue_in": 10.1, "snap": "beat"})
        # Should snap to nearest beat
        assert result["cue_in"] != 10.1

    def test_set_bpm(self, tmp_project):
        SESSION.load()
        result = track_set_bpm({"index": 0, "bpm": 130})
        assert result["bpm_after"] == 130
        assert result["bpm_before"] == 128

    def test_set_bpm_halve(self, tmp_project):
        SESSION.load()
        result = track_set_bpm({"index": 0, "halve": True})
        assert result["bpm_after"] == 64.0

    def test_grid_nudge(self, tmp_project):
        SESSION.load()
        first_before = SESSION.project.library[0].beats[0]
        result = track_grid_nudge({"index": 0, "offset_ms": 50})
        assert result["first_beat"] == pytest.approx(first_before + 0.05, abs=0.001)


class TestTimeline:
    def test_append_and_remove(self, tmp_project):
        SESSION.load()
        r1 = timeline_append({"index": 0})
        assert r1["position"] == 0
        assert r1["timeline_length"] == 1

        r2 = timeline_append({"index": 1})
        assert r2["position"] == 1

        r3 = timeline_remove({"position": 0})
        assert r3["timeline_length"] == 1


class TestTransitions:
    def test_set_and_list(self, tmp_project):
        SESSION.load()
        timeline_append({"index": 0})
        timeline_append({"index": 1})

        r = transition_set({"position": 0, "type": "eq_fade", "bars": 32})
        assert r["type"] == "eq_fade"
        assert r["length_bars"] == 32
        assert r["from_title"] == "Track A"
        assert r["to_title"] == "Track B"

        r2 = transition_list({})
        assert r2["total"] == 1

    def test_set_with_offset(self, tmp_project):
        SESSION.load()
        timeline_append({"index": 0})
        timeline_append({"index": 1})

        r = transition_set({"position": 0, "offset_beats": -4})
        assert r["offset_beats"] == -4

    def test_set_filter_sweep(self, tmp_project):
        SESSION.load()
        timeline_append({"index": 0})
        timeline_append({"index": 1})

        r = transition_set({"position": 0, "type": "filter_sweep", "bars": 16})
        assert r["type"] == "filter_sweep"

    def test_set_preserves_offset(self, tmp_project):
        """Changing type should preserve existing offset."""
        SESSION.load()
        timeline_append({"index": 0})
        timeline_append({"index": 1})

        transition_set({"position": 0, "type": "crossfade", "offset_beats": -4})
        r = transition_set({"position": 0, "type": "eq_fade"})
        assert r["type"] == "eq_fade"
        assert r["offset_beats"] == -4  # preserved


class TestMixIntelligence:
    def test_compatibility_matrix(self, tmp_project):
        SESSION.load()
        result = mix_compatibility_matrix({})
        assert "matrix" in result
        # Track A (Am) and Track B (Cm) should be compatible
        a_row = result["matrix"][0]
        assert a_row["title"] == "Track A"
        assert len(a_row["compatible_with"]) > 0

    def test_suggest_next(self, tmp_project):
        SESSION.load()
        timeline_append({"index": 0})

        result = mix_suggest_next({"limit": 3})
        assert "suggestions" in result
        assert result["after_track"]["title"] == "Track A"

    def test_suggest_order(self, tmp_project):
        SESSION.load()
        result = mix_suggest_order({})
        assert result["track_count"] >= 2
        # First track in order should have transition info from pos 1 onward
        if len(result["order"]) > 1:
            assert "transition_from_prev" in result["order"][1]

    def test_energy_profile(self, tmp_project):
        SESSION.load()
        timeline_append({"index": 0})
        timeline_append({"index": 1})

        result = mix_energy_profile({})
        assert len(result["profile"]) == 2
        assert result["profile"][0]["energy_avg"] is not None

    def test_validate_empty(self, tmp_project):
        SESSION.load()
        result = mix_validate({})
        assert result["ok"] is True

    def test_validate_with_issues(self, tmp_project):
        SESSION.load()
        # Add tracks with no transition between them
        timeline_append({"index": 0})
        timeline_append({"index": 1})

        result = mix_validate({})
        # Should warn about missing transition
        types = [w["type"] for w in result["warnings"]]
        assert "missing_transition" in types


class TestMCPProtocol:
    def test_initialize(self):
        response = handle_request({
            "jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {},
        })
        assert response["result"]["protocolVersion"] == "2024-11-05"
        assert response["result"]["serverInfo"]["name"] == "pymixter"

    def test_tools_list(self):
        response = handle_request({
            "jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {},
        })
        tools = response["result"]["tools"]
        names = {t["name"] for t in tools}
        assert "project_open" in names
        assert "mix_automix" in names
        assert "track_analyze" in names
        assert "mix_compatibility_matrix" in names

    def test_tools_call(self, tmp_project):
        response = handle_request({
            "jsonrpc": "2.0", "id": 3, "method": "tools/call",
            "params": {"name": "project_open", "arguments": {"path": SESSION.project_path}},
        })
        content = response["result"]["content"][0]
        assert content["type"] == "text"
        data = json.loads(content["text"])
        assert data["name"] == "Test Mix"

    def test_unknown_tool(self):
        response = handle_request({
            "jsonrpc": "2.0", "id": 4, "method": "tools/call",
            "params": {"name": "nonexistent", "arguments": {}},
        })
        assert "error" in response

    def test_tool_count(self):
        """Verify we have a comprehensive set of tools."""
        assert len(TOOLS) >= 20, f"Expected 20+ tools, got {len(TOOLS)}"
