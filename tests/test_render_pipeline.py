"""End-to-end with real ffmpeg/melt: scene clips, MLT composite, preview job, frame evidence, cancel."""

import time
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from explica import jobs, ops
from explica.audio import generate_music
from explica.media import duration_frames, probe
from explica.project import Project

from .conftest import needs_ffmpeg, needs_melt


@needs_ffmpeg
def test_scene_clip_cache(timeline_project: Project):
    a = ops.ensure_scene_clip(timeline_project, "s1", "preview")
    assert a["cached"] is False and Path(a["path"]).exists()
    b = ops.ensure_scene_clip(timeline_project, "s1", "preview")
    assert b["cached"] is True and b["path"] == a["path"]
    timeline_project.scene_patch("s1", [{"op": "set", "path": "objects.title.text", "value": "otro"}])
    c = ops.ensure_scene_clip(timeline_project, "s1", "preview")
    assert c["cached"] is False and c["path"] != a["path"]  # hash changed → new derived clip


@needs_ffmpeg
@needs_melt
def test_timeline_frame_and_preview_job(timeline_project: Project):
    p = timeline_project
    music = generate_music(p.root / "assets" / "music" / "bed.wav", 3.0, seed=1)
    p.add_asset("bed", Path(music["path"]), "audio", copy_into=None, meta={"duration_frames": duration_frames(Path(music["path"]), p.fps, "floor")})
    p.timeline_patch([{"op": "add_clip", "track": "A2", "clip": {"id": "mus", "source": {"kind": "asset", "ref": "bed"}, "start": 0, "in": 0, "out": 60, "gain_db": -6}}])
    xml = ops.build_timeline_xml(p, "preview")
    root = ET.parse(xml).getroot()
    assert root.find("profile").get("width") == "270"
    assert any(f.find("property[@name='level']") is not None for f in root.iter("filter"))
    png = ops.media_frame(p, "timeline", 30)
    assert Path(png["path"]).stat().st_size > 500
    job = ops.start_preview_job(p, 0, 60)
    info = jobs.wait(p.root, job["id"], timeout=180)
    assert info["status"] == "done", info["log_tail"]
    pr = probe(Path(info["meta"]["out_path"]))
    v = next(s for s in pr["streams"] if s["codec_type"] == "video")
    a = next(s for s in pr["streams"] if s["codec_type"] == "audio")
    assert int(v["nb_frames"]) == 60 and a is not None
    assert info["progress_percent"] == 100


@needs_ffmpeg
@needs_melt
def test_job_cancel(timeline_project: Project):
    p = timeline_project
    p.timeline_patch([{"op": "trim_clip", "id": "bg", "out": 90}])
    job = ops.start_preview_job(p, 0, 90, quality="master")
    time.sleep(0.5)
    info = jobs.cancel(p.root, job["id"])
    assert info["status"] in ("cancelled", "done")
    listed = jobs.list_jobs(p.root)
    assert any(j["id"] == job["id"] for j in listed)


@needs_ffmpeg
def test_media_frame_scene_and_inspect(timeline_project: Project):
    info = ops.media_frame(timeline_project, "scene:s1", 25)
    assert info["kind"].startswith("scene source")
    insp = ops.scene_inspect(timeline_project, "s1", frame=25)
    assert "t2" in insp["bounds"]


@needs_ffmpeg
def test_music_generation_is_deterministic(tmp_path: Path):
    a = generate_music(tmp_path / "a.wav", 2.0, seed=3)
    b = generate_music(tmp_path / "b.wav", 2.0, seed=3)
    assert Path(a["path"]).read_bytes()[44:] == Path(b["path"]).read_bytes()[44:]
    assert probe(Path(a["path"]))["duration_seconds"] == pytest.approx(2.0, abs=0.05)
