"""Deliverable bundle: master, sources, project, audio stems, captions, licenses and QA over final hashes (A17)."""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

from . import jobs, ops
from .audio import normalize_loudness
from .kdenlive import export_kdenlive
from .media import detect_black_silence, loudness, probe
from .project import Project, sha256_file


def export_bundle(project: Project, quality: str = "master", wait_seconds: float = 1800, normalize: bool = True) -> dict:
    p = project
    out_dir = p.root / "export" / f"bundle-r{p.revision}"
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)
    report: dict = {"revision": p.revision, "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "files": {}, "qa": {}}
    validation = p.validate()
    report["validation"] = validation
    # 1. master render
    job = ops.start_preview_job(p, 0, None, quality)
    job = jobs.wait(p.root, job["id"], timeout=wait_seconds)
    if job["status"] != "done":
        raise RuntimeError(f"master render failed: {job['status']} (log: {job['log']})")
    raw_master = Path(job["meta"]["out_path"])
    master = out_dir / f"{p.m['id']}-master.mp4"
    if normalize:
        report["qa"]["loudnorm"] = normalize_loudness(raw_master, master)
    else:
        shutil.copy2(raw_master, master)
    # 2. editable sources
    (out_dir / "project").mkdir()
    shutil.copy2(p.root / "project.json", out_dir / "project" / "project.json")
    shutil.copytree(p.root / "scenes", out_dir / "project" / "scenes")
    kd = export_kdenlive(p, ops.scene_resolver(p, "master"), out_path=out_dir / "project" / f"{p.m['id']}.kdenlive", force=True)
    report["files"]["kdenlive"] = kd
    mlt_path = ops.export_mlt(p, quality)
    shutil.copy2(mlt_path, out_dir / "project" / mlt_path.name)
    srt = ops.export_srt(p)
    shutil.copy2(srt, out_dir / srt.name)
    # 3. stems (voice/music/effects) and scene clips
    stems = out_dir / "audio"
    stems.mkdir()
    for aid, a in p.m["assets"].items():
        if a.get("kind") == "audio":
            shutil.copy2(p.asset_path(aid), stems / f"{aid}{Path(a['path']).suffix}")
    clips = out_dir / "scene-clips"
    clips.mkdir()
    for sid in p.m["scenes"]:
        sc = ops.ensure_scene_clip(p, sid, "master")
        shutil.copy2(sc["path"], clips / Path(sc["path"]).name)
        shutil.copy2(sc["path"] + ".json", clips / (Path(sc["path"]).name + ".json"))
    # 4. licenses
    licenses = {
        "explica": "MIT (https://github.com/Twynzen/explica)",
        "assets": {aid: {"license": a.get("license"), "provenance": a.get("provenance")} for aid, a in p.m["assets"].items()},
        "fonts": p.m.get("font"),
        "notes": p.m.get("provenance", {}),
    }
    (out_dir / "LICENSES.json").write_text(json.dumps(licenses, indent=2, ensure_ascii=False))
    # 5. QA over final hashes
    report["qa"]["probe"] = probe(master)
    report["qa"]["loudness"] = loudness(master)
    report["qa"]["detect"] = detect_black_silence(master)
    hashes = {}
    for f in sorted(out_dir.rglob("*")):
        if f.is_file():
            hashes[str(f.relative_to(out_dir))] = sha256_file(f)
    report["sha256"] = hashes
    report["path"] = str(out_dir)
    (out_dir / "QA-REPORT.json").write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str))
    return report
