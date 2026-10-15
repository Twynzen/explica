"""High-level operations shared by the CLI and the MCP server (one core, two adapters)."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from . import jobs
from .captions import write_srt
from .media import audio_segment, extract_frame, probe
from .mlt import MltBuilder, melt_argv_render, melt_frame, write_xml
from .project import Project, ProjectError
from .scene.render import render_frame_png, render_params_hash, render_scene, scene_bounds
from .timebase import Fps
from .tools import capabilities


def _quality_size(project: Project, quality: str) -> tuple[int, int]:
    if quality == "master":
        return project.width, project.height
    return int(project.m["preview"]["width"]), int(project.m["preview"]["height"])


# ------------------------------------------------------------------ scenes
def scene_clip_path(project: Project, scene_id: str, quality: str) -> tuple[Path, Path]:
    scene = project.load_scene(scene_id)
    w, h = _quality_size(project, quality)
    key = render_params_hash(scene, w, h, project.fps)
    ext = ".mov" if scene.get("background", "#000000") is None else ".mp4"
    out = project.root / "renders" / "scenes" / f"{scene_id}-{key}-{w}x{h}{ext}"
    return out, Path(str(out) + ".json")


def ensure_scene_clip(project: Project, scene_id: str, quality: str = "preview", force: bool = False, progress_cb=None) -> dict:
    """Render (or reuse the cached) clip for a scene at the given quality. Returns the sidecar."""
    out, sidecar_path = scene_clip_path(project, scene_id, quality)
    if out.exists() and sidecar_path.exists() and not force:
        sidecar = json.loads(sidecar_path.read_text())
        sidecar["cached"] = True
        return sidecar
    scene = project.load_scene(scene_id)
    w, h = _quality_size(project, quality)
    t0 = time.time()
    sidecar = render_scene(scene, out, w, h, project.fps, progress_cb=progress_cb)
    sidecar["cached"] = False
    sidecar["render_seconds"] = round(time.time() - t0, 2)
    return sidecar


def scene_resolver(project: Project, quality: str):
    def resolve(scene_id: str) -> tuple[Path, dict]:
        sidecar = ensure_scene_clip(project, scene_id, quality)
        return Path(sidecar["path"]), sidecar

    return resolve


def scene_inspect(project: Project, scene_id: str, frame: int | None = None) -> dict:
    scene = project.load_scene(scene_id)
    out = {
        "revision": project.revision,
        "scene": scene,
        "manifest_entry": project.m["scenes"][scene_id],
        "patch_paths": "objects.<id>.<prop>, animations.<id>.<prop>, camera.<prop>, duration_frames, background",
    }
    if frame is not None:
        out["frame"] = frame
        out["bounds"] = scene_bounds(scene, frame)
    return out


def scene_frame(project: Project, scene_id: str, frame: int, quality: str = "preview") -> Path:
    scene = project.load_scene(scene_id)
    w, h = _quality_size(project, quality)
    out = project.root / "renders" / "preview" / f"scene-{scene_id}-f{frame}-{w}x{h}.png"
    return render_frame_png(scene, frame, out, w, h, project.fps)


# ------------------------------------------------------------------ timeline/mlt
def build_timeline_xml(project: Project, quality: str = "preview", absolute: bool = True) -> Path:
    w, h = _quality_size(project, quality)
    builder = MltBuilder(project, w, h, scene_resolver(project, quality), absolute=absolute)
    root = builder.build()
    out = project.root / "renders" / "preview" / f"timeline-{quality}-r{project.revision}.mlt"
    return write_xml(root, out)


def timeline_frame(project: Project, frame: int, quality: str = "preview") -> Path:
    xml = build_timeline_xml(project, quality)
    w, h = _quality_size(project, quality)
    out = project.root / "renders" / "preview" / f"timeline-f{frame}-{w}x{h}-r{project.revision}.png"
    return melt_frame(xml, frame, out)


def media_frame(project: Project, ref: str, frame: int, quality: str = "preview") -> dict:
    """Evidence frame: ref is 'timeline', 'scene:<id>' or 'asset:<id>'."""
    if ref == "timeline":
        png = timeline_frame(project, frame, quality)
        kind = "timeline composite (MLT)"
    elif ref.startswith("scene:"):
        png = scene_frame(project, ref.split(":", 1)[1], frame, quality)
        kind = "scene source render (cairo)"
    elif ref.startswith("asset:"):
        aid = ref.split(":", 1)[1]
        out = project.root / "renders" / "preview" / f"asset-{aid}-f{frame}.png"
        png = extract_frame(project.asset_path(aid), frame, project.fps, out, width=_quality_size(project, quality)[0])
        kind = "asset frame (ffmpeg)"
    else:
        raise ProjectError("ref must be 'timeline', 'scene:<id>' or 'asset:<id>'")
    return {"path": str(png), "frame": frame, "ref": ref, "kind": kind, "revision": project.revision}


def media_inspect(project: Project, asset_id: str) -> dict:
    entry = project.m["assets"].get(asset_id)
    if not entry:
        raise ProjectError(f"unknown asset {asset_id!r}")
    info = probe(project.asset_path(asset_id))
    info.update({"asset_id": asset_id, "manifest": entry})
    return info


def audio_slice(project: Project, asset_id: str, start: int, end: int) -> dict:
    out = project.root / "renders" / "preview" / f"audio-{asset_id}-{start}-{end}.wav"
    res = audio_segment(project.asset_path(asset_id), start, end, project.fps, out)
    res["limits"] = ["WAV file path only: the client must open it with its own audio capability or ask a human to listen"]
    return res


# ------------------------------------------------------------------ jobs
def start_preview_job(project: Project, start: int = 0, end: int | None = None, quality: str = "preview") -> dict:
    """Render a timeline range asynchronously (scene clips are rendered first, synchronously, with cache)."""
    end = project.duration_frames() if end is None else int(end)
    if end <= start:
        raise ProjectError("end must be > start")
    xml = build_timeline_xml(project, quality)
    w, h = _quality_size(project, quality)
    out = project.root / "renders" / "preview" / f"{quality}-{start}-{end}-r{project.revision}.mp4"
    argv = melt_argv_render(xml, out, start, end, quality=quality)
    return jobs.start(project.root, f"render.{quality}", argv, meta={"out_path": str(out), "xml": str(xml), "start": start, "end": end, "revision": project.revision, "size": [w, h]})


def start_scene_job(project: Project, scene_id: str, quality: str = "preview") -> dict:
    argv = [sys.executable, "-m", "explica.cli", "--project", str(project.root), "scene", "render", scene_id, "--quality", quality]
    out, _ = scene_clip_path(project, scene_id, quality)
    return jobs.start(project.root, "render.scene", argv, meta={"out_path": str(out), "scene_id": scene_id})


# ------------------------------------------------------------------ export
def export_srt(project: Project) -> Path:
    out = project.root / "export" / f"{project.m['id']}.srt"
    return write_srt(project.m["captions"], project.fps, out)


def export_mlt(project: Project, quality: str = "master") -> Path:
    xml = build_timeline_xml(project, quality, absolute=False)
    out = project.root / "export" / f"{project.m['id']}-{quality}.mlt"
    out.parent.mkdir(exist_ok=True)
    out.write_text(xml.read_text())
    return out


def project_inspect(project: Project, with_capabilities: bool = False) -> dict:
    return project.inspect(capabilities() if with_capabilities else None)


def fps_of(project: Project) -> Fps:
    return project.fps
