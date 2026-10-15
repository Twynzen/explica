"""Kdenlive export + simulated GUI edit + sync back (A03/A04/A19, headless part)."""

import re
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from explica import ops
from explica.kdenlive import export_kdenlive, kdenlive_file_hash, read_kdenlive, sync_from_kdenlive
from explica.project import Project, ProjectError
from explica.tools import run

from .conftest import needs_ffmpeg, needs_melt


def _export(p: Project) -> Path:
    res = export_kdenlive(p, ops.scene_resolver(p, "preview"))
    assert not res["warnings"], res["warnings"]
    return Path(res["path"])


@needs_ffmpeg
def test_export_structure(timeline_project: Project):
    path = _export(timeline_project)
    root = ET.parse(path).getroot()
    assert root.get("producer") == "main_bin"
    props = {p.get("name"): p.text for p in root.find("playlist[@id='main_bin']").findall("property")}
    assert props["kdenlive:docproperties.version"] == "1.1"
    assert props["kdenlive:docproperties.profile"] == "vertical_hd_30"
    seq = next(t for t in root.findall("tractor") if t.find("property[@name='kdenlive:uuid']") is not None)
    tracks = seq.findall("track")
    assert tracks[0].get("producer") == "black_track"
    assert len(tracks) == 1 + 7
    # audio tracks first (bottom), then video; every track tractor has two playlists
    for tr in root.findall("tractor"):
        if tr.get("id", "").startswith("tractor") and tr.find("property[@name='kdenlive:projectTractor']") is None:
            assert len(tr.findall("track")) == 2
    # native title and caption producers are kdenlivetitle with xmldata
    titles = [p for p in root.iter("producer") if (p.find("property[@name='mlt_service']") is not None and p.find("property[@name='mlt_service']").text == "kdenlivetitle")]
    assert len(titles) == 3  # 1 title + 2 captions
    assert "Título" in titles[0].find("property[@name='xmldata']").text
    chain = root.find("chain")
    assert chain.find("property[@name='kdenlive:file_hash']").text == kdenlive_file_hash(Path(chain.find("property[@name='resource']").text))


@needs_ffmpeg
def test_file_hash_algorithm(tmp_path: Path):
    small = tmp_path / "small.bin"
    small.write_bytes(b"a" * 1000)
    import hashlib

    assert kdenlive_file_hash(small) == hashlib.md5(b"a" * 1000).hexdigest()
    big = tmp_path / "big.bin"
    big.write_bytes(b"x" * 1_000_000 + b"y" * 500_000 + b"z" * 1_000_000)
    assert kdenlive_file_hash(big) == hashlib.md5(b"x" * 1_000_000 + b"z" * 1_000_000).hexdigest()


@needs_ffmpeg
@needs_melt
def test_melt_renders_exported_kdenlive_frame(timeline_project: Project, tmp_path: Path):
    path = _export(timeline_project)
    out = tmp_path / "f.png"
    run([shutil.which("melt"), str(path), "in=30", "out=30", "-consumer", f"avformat:{out}"])
    assert out.stat().st_size > 500


@needs_ffmpeg
def test_roundtrip_no_change_is_empty_diff(timeline_project: Project):
    path = _export(timeline_project)
    res = sync_from_kdenlive(timeline_project, path, dry_run=True)
    assert res.diff == [], res.diff


@needs_ffmpeg
def test_gui_edit_simulation_roundtrip(timeline_project: Project):
    """Simulate what Kdenlive does on save: move a clip (blank length), trim in/out, edit title text, change gain."""
    p = timeline_project
    path = _export(p)
    text = path.read_text()
    # 1. V2 clip c1: move from 10 → 25 and trim out to 50 (blank 10 → 25, out 59 → 49)
    text = re.sub(r'<blank length="10" />(\s*)<entry producer="scene_s1" in="0" out="59">', r'<blank length="25" />\1<entry producer="scene_s1" in="0" out="49">', text, count=1)
    assert 'out="49"' in text
    # 2. title text edited in the title editor
    text = text.replace("&gt;Título&lt;/content&gt;", "&gt;Título editado&lt;/content&gt;")
    assert "Título editado" in text
    # 3. caption text edited
    text = text.replace("&gt;Primer caption&lt;/content&gt;", "&gt;Primer caption editado&lt;/content&gt;")
    path.write_text(text)
    parsed = read_kdenlive(path, p)
    v2 = next(t for t in parsed["tracks"] if t["id"] == "V2")
    assert v2["clips"][0]["start"] == 25 and v2["clips"][0]["out"] == 50
    assert parsed["titles"]["main"]["text"] == "Título editado"
    res = sync_from_kdenlive(p, path)
    assert res.ok
    p2 = Project.load(p.root)
    _, c1 = p2.find_clip("c1")
    assert (c1["start"], c1["in"], c1["out"]) == (25, 0, 50)
    assert p2.m["titles"]["main"]["text"] == "Título editado"
    assert p2.m["captions"][0]["text"] == "Primer caption editado"
    # scene source untouched by the GUI edit (A04: geometry lives in the scene source)
    assert p2.load_scene("s1")["objects"][0]["text"] == "Hola"
    # re-export after sync is allowed and reproduces the human edit
    path2 = _export(p2)
    assert 'out="49"' in path2.read_text() and "Título editado" in path2.read_text()


@needs_ffmpeg
def test_divergent_export_refused_without_loss(timeline_project: Project):
    """A19: GUI modified the file AND the agent changed the manifest → export must not overwrite silently."""
    p = timeline_project
    path = _export(p)
    text = path.read_text().replace("&gt;Título&lt;/content&gt;", "&gt;Cambiado en GUI&lt;/content&gt;")
    assert "Cambiado en GUI" in text
    path.write_text(text)
    p.scene_patch("s1", [{"op": "set", "path": "objects.title.text", "value": "cambio del agente"}])
    res = export_kdenlive(p, ops.scene_resolver(p, "preview"))
    assert res["warnings"] and ".agent-r" in res["path"]
    assert "Cambiado en GUI" in path.read_text()  # human file intact
    # a scene-only agent change does not conflict with montage edits: sync is allowed (dry run here)
    assert sync_from_kdenlive(p, path, dry_run=True).ok
    # but once the agent also changed the montage, syncing the stale file would lose it → refused
    p.timeline_patch([{"op": "trim_clip", "id": "c1", "out": 30}])
    with pytest.raises(ProjectError, match="divergent"):
        sync_from_kdenlive(p, path)
    assert Project.load(p.root).find_clip("c1")[1]["out"] == 30  # nothing lost
    # restore the snapshot taken before the montage change, then sync works and keeps the GUI edit
    cp = [c for c in p.list_checkpoints() if c["op"] == "timeline.patch"][-1]
    p.restore(cp["id"])
    res2 = sync_from_kdenlive(Project.load(p.root), path)
    assert res2.ok and Project.load(p.root).m["titles"]["main"]["text"] == "Cambiado en GUI"
    assert Project.load(p.root).load_scene("s1")["objects"][0]["text"] == "cambio del agente"


@needs_ffmpeg
def test_unmodelled_effects_are_reported(timeline_project: Project):
    p = timeline_project
    path = _export(p)
    text = path.read_text()
    text = re.sub(r'(<entry producer="scene_s1" in="0" out="59">)', r'\1<filter id="f_gui"><property name="mlt_service">brightness</property><property name="level">0.5</property></filter>', text, count=1)
    path.write_text(text)
    res = sync_from_kdenlive(p, path, dry_run=True)
    assert any("brightness" in w for w in res.warnings)


def test_read_file_saved_by_real_kdenlive(timeline_project: Project):
    """A03 evidence: a project re-saved by Kdenlive 26.08 itself (GUI session, see docs/evidence/A03-kdenlive-gui)
    parses back: clock-formatted blanks/in/out, guides added in the GUI, captions and scene clips."""
    fixture = Path(__file__).resolve().parents[1] / "docs" / "evidence" / "A03-kdenlive-gui" / "saved-by-kdenlive-26.08.kdenlive"
    if not fixture.exists():
        pytest.skip("evidence fixture missing")
    parsed = read_kdenlive(fixture, timeline_project)
    assert parsed["revision"] >= 1
    assert any(g["comment"] == "Marker" for g in parsed["guides"])  # guide added with the GUI
    v2 = next(t for t in parsed["tracks"] if t["id"] == "V2")
    assert [c["source"]["ref"] for c in v2["clips"]][:5] == ["beat1", "beat2", "beat3", "beat4", "beat5"]
    assert v2["clips"][0]["start"] == 0 and v2["clips"][1]["start"] == v2["clips"][0]["out"]
    assert len(parsed["captions"]) == 7 and parsed["captions"][0]["text"].startswith("Tres tokens")
    assert all(t["kind"] == ("audio" if t["id"].startswith("A") else "video") for t in parsed["tracks"])
