"""Rekordbox XML import/export for DJ software interoperability.

Supports the Rekordbox XML format used by Pioneer DJ, Mixxx, Traktor,
Serato, and other DJ software for library exchange.

Format spec: https://rekordbox.com/en/support/developer/
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import quote, unquote
from urllib.request import pathname2url, url2pathname

from pymixter.core.project import Project, Track


def _path_to_uri(path: str) -> str:
    """Convert file path to file:// URI."""
    p = Path(path).resolve()
    return "file://localhost" + pathname2url(str(p))


def _uri_to_path(uri: str) -> str:
    """Convert file:// URI to local path."""
    uri = uri.replace("file://localhost", "").replace("file://", "")
    return url2pathname(unquote(uri))


def _key_to_tonality(key: str | None) -> str:
    """Convert our key format (e.g. 'Am', 'C') to Rekordbox Tonality."""
    if not key:
        return ""
    return key


def _tonality_to_key(tonality: str) -> str | None:
    """Convert Rekordbox Tonality to our key format."""
    if not tonality:
        return None
    return tonality


def export_rekordbox_xml(project: Project, output_path: str) -> str:
    """Export project library to Rekordbox XML format.

    Args:
        project: Project to export
        output_path: Where to write the XML file

    Returns:
        Path to the written file.
    """
    root = ET.Element("DJ_PLAYLISTS", Version="1.0.0")

    # Product info
    product = ET.SubElement(root, "PRODUCT",
                            Name="PyMixter",
                            Version="0.1.0",
                            Company="")

    # Collection
    collection = ET.SubElement(root, "COLLECTION",
                               Entries=str(len(project.library)))

    for i, track in enumerate(project.library):
        attrs = {
            "TrackID": str(i + 1),
            "Name": track.title,
            "Artist": "",
            "Album": "",
            "Genre": "",
            "Kind": Path(track.path).suffix.lstrip(".").upper(),
            "TotalTime": str(int(track.duration)),
            "AverageBpm": f"{track.bpm:.2f}" if track.bpm else "0.00",
            "Tonality": _key_to_tonality(track.key),
            "Location": _path_to_uri(track.path),
        }
        track_el = ET.SubElement(collection, "TRACK", **attrs)

        # Beat grid (TEMPO elements)
        if track.bpm and track.beats:
            ET.SubElement(track_el, "TEMPO",
                          Inizio=f"{track.beats[0]:.3f}" if track.beats else "0.000",
                          Bpm=f"{track.bpm:.2f}",
                          Metro="4/4",
                          Battito="1")

        # Cue points (POSITION_MARK elements)
        if track.cue_in is not None:
            ET.SubElement(track_el, "POSITION_MARK",
                          Name="Cue In",
                          Type="0",
                          Start=f"{track.cue_in:.3f}",
                          Num="0")
        if track.cue_out is not None:
            ET.SubElement(track_el, "POSITION_MARK",
                          Name="Cue Out",
                          Type="0",
                          Start=f"{track.cue_out:.3f}",
                          Num="1")

    # Playlists
    playlists = ET.SubElement(root, "PLAYLISTS")
    root_node = ET.SubElement(playlists, "NODE",
                              Type="0", Name="ROOT", Count="1")

    # Library playlist (all tracks)
    lib_node = ET.SubElement(root_node, "NODE",
                             Type="1",
                             Name=project.name,
                             Entries=str(len(project.library)),
                             KeyType="0")
    for i in range(len(project.library)):
        ET.SubElement(lib_node, "TRACK", Key=str(i + 1))

    # Timeline playlist (if exists)
    if project.timeline:
        tl_node = ET.SubElement(root_node, "NODE",
                                Type="1",
                                Name=f"{project.name} — Timeline",
                                Entries=str(len(project.timeline)),
                                KeyType="0")
        for tidx in project.timeline:
            ET.SubElement(tl_node, "TRACK", Key=str(tidx + 1))

    # Write
    tree = ET.ElementTree(root)
    ET.indent(tree, space="  ")
    out = Path(output_path)
    tree.write(str(out), encoding="utf-8", xml_declaration=True)
    return str(out)


def import_rekordbox_xml(xml_path: str, project: Project | None = None) -> Project:
    """Import tracks from Rekordbox XML into a project.

    Args:
        xml_path: Path to Rekordbox XML file
        project: Existing project to add tracks to (or None to create new)

    Returns:
        Project with imported tracks.
    """
    tree = ET.parse(xml_path)
    root = tree.getroot()

    if project is None:
        project = Project()

    collection = root.find("COLLECTION")
    if collection is None:
        return project

    for track_el in collection.findall("TRACK"):
        location = track_el.get("Location", "")
        if not location:
            continue

        path = _uri_to_path(location)

        # Skip if already in library
        if any(t.path == path for t in project.library):
            continue

        name = track_el.get("Name", "")
        bpm_str = track_el.get("AverageBpm", "0")
        bpm = float(bpm_str) if bpm_str and float(bpm_str) > 0 else None
        tonality = track_el.get("Tonality", "")
        key = _tonality_to_key(tonality)
        total_time = int(track_el.get("TotalTime", "0"))

        # Extract cue points
        cue_in = None
        cue_out = None
        for mark in track_el.findall("POSITION_MARK"):
            mark_name = mark.get("Name", "").lower()
            start = float(mark.get("Start", "0"))
            if "in" in mark_name or mark.get("Num") == "0":
                cue_in = start
            elif "out" in mark_name or mark.get("Num") == "1":
                cue_out = start

        # Extract beat grid
        beats = []
        tempo_el = track_el.find("TEMPO")
        # Rekordbox stores grid start + BPM, not individual beats
        # We store the BPM from TEMPO if available
        if tempo_el is not None:
            tempo_bpm = float(tempo_el.get("Bpm", "0"))
            if tempo_bpm > 0 and not bpm:
                bpm = tempo_bpm

        track = Track(
            path=path,
            title=name,
            bpm=bpm,
            key=key,
            duration=float(total_time),
            cue_in=cue_in,
            cue_out=cue_out,
        )
        project.library.append(track)

    # Import playlists as timeline (use first non-root playlist)
    playlists = root.find("PLAYLISTS")
    if playlists is not None and not project.timeline:
        for node in playlists.iter("NODE"):
            if node.get("Type") == "1":
                track_keys = [int(t.get("Key", "0")) for t in node.findall("TRACK")]
                if track_keys:
                    # Convert 1-based TrackIDs to 0-based library indices
                    project.timeline = [k - 1 for k in track_keys
                                        if 0 < k <= len(project.library)]
                    break

    return project
