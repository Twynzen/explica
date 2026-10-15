"""Command line adapter over the same core the MCP server uses. Output is JSON."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__, jobs, ops
from .audio import generate_music, normalize_loudness, piper_available, synthesize_voice
from .kdenlive import export_kdenlive, sync_from_kdenlive
from .media import detect_black_silence, loudness
from .project import Project, ProjectError
from .timebase import Fps
from .tools import capabilities


def _out(data) -> None:
    if hasattr(data, "to_dict"):
        data = data.to_dict()
    print(json.dumps(data, indent=2, ensure_ascii=False, default=str))


def _ops(args) -> list[dict]:
    if args.ops_json:
        return json.loads(args.ops_json)
    if args.ops_file:
        return json.loads(Path(args.ops_file).read_text(encoding="utf-8"))
    raise SystemExit("provide --ops-json or --ops-file")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="explica", description="Local, agent-controllable explanatory-video lab")
    ap.add_argument("--project", "-p", default=".", help="project directory (contains project.json)")
    ap.add_argument("--version", action="version", version=__version__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("capabilities", help="measured tool capabilities on this machine")

    p_init = sub.add_parser("init", help="create a new project directory")
    p_init.add_argument("name")
    p_init.add_argument("--fps", type=int, default=30)
    p_init.add_argument("--width", type=int, default=1080)
    p_init.add_argument("--height", type=int, default=1920)

    p_insp = sub.add_parser("inspect", help="project summary")
    p_insp.add_argument("--capabilities", action="store_true")

    p_tq = sub.add_parser("timeline", help="query clips in a frame range")
    p_tq.add_argument("--start", type=int, default=0)
    p_tq.add_argument("--end", type=int, default=None)

    p_tp = sub.add_parser("timeline-patch", help="apply timeline ops (JSON list)")
    p_tp.add_argument("--ops-json")
    p_tp.add_argument("--ops-file")
    p_tp.add_argument("--expected-revision", type=int)
    p_tp.add_argument("--dry-run", action="store_true")

    p_sc = sub.add_parser("scene", help="scene operations")
    ssub = p_sc.add_subparsers(dest="scmd", required=True)
    s_add = ssub.add_parser("add")
    s_add.add_argument("file")
    s_insp = ssub.add_parser("inspect")
    s_insp.add_argument("scene_id")
    s_insp.add_argument("--frame", type=int)
    s_patch = ssub.add_parser("patch")
    s_patch.add_argument("scene_id")
    s_patch.add_argument("--ops-json")
    s_patch.add_argument("--ops-file")
    s_patch.add_argument("--expected-revision", type=int)
    s_patch.add_argument("--dry-run", action="store_true")
    s_render = ssub.add_parser("render")
    s_render.add_argument("scene_id")
    s_render.add_argument("--quality", choices=["preview", "master"], default="preview")
    s_render.add_argument("--force", action="store_true")
    s_frame = ssub.add_parser("frame")
    s_frame.add_argument("scene_id")
    s_frame.add_argument("frame", type=int)
    s_frame.add_argument("--quality", choices=["preview", "master"], default="preview")

    p_asset = sub.add_parser("asset", help="asset operations")
    asub = p_asset.add_subparsers(dest="acmd", required=True)
    a_add = asub.add_parser("add")
    a_add.add_argument("asset_id")
    a_add.add_argument("file")
    a_add.add_argument("--kind", choices=["audio", "video", "image"], default="audio")
    a_add.add_argument("--license", default="")
    a_add.add_argument("--provenance", default="")
    a_rep = asub.add_parser("replace")
    a_rep.add_argument("asset_id")
    a_rep.add_argument("file")
    a_insp = asub.add_parser("inspect")
    a_insp.add_argument("asset_id")
    a_seg = asub.add_parser("segment")
    a_seg.add_argument("asset_id")
    a_seg.add_argument("start", type=int)
    a_seg.add_argument("end", type=int)

    p_title = sub.add_parser("title", help="set a native title")
    p_title.add_argument("title_id")
    p_title.add_argument("--fields-json", required=True)
    p_title.add_argument("--expected-revision", type=int)

    p_frame = sub.add_parser("frame", help="evidence frame: timeline | scene:<id> | asset:<id>")
    p_frame.add_argument("ref")
    p_frame.add_argument("frame", type=int)
    p_frame.add_argument("--quality", choices=["preview", "master"], default="preview")

    p_prev = sub.add_parser("preview", help="start a preview render job")
    p_prev.add_argument("--start", type=int, default=0)
    p_prev.add_argument("--end", type=int)
    p_prev.add_argument("--quality", choices=["preview", "master"], default="preview")
    p_prev.add_argument("--wait", type=float, default=0, help="seconds to wait for completion")

    p_job = sub.add_parser("job", help="job status/cancel/list")
    p_job.add_argument("action", choices=["status", "cancel", "list", "wait"])
    p_job.add_argument("job_id", nargs="?")
    p_job.add_argument("--timeout", type=float, default=600)

    p_cp = sub.add_parser("checkpoint", help="create a checkpoint")
    p_cp.add_argument("label", nargs="?", default="manual")
    sub.add_parser("checkpoints", help="list checkpoints")
    p_rs = sub.add_parser("restore", help="restore a checkpoint")
    p_rs.add_argument("checkpoint_id")
    sub.add_parser("undo", help="undo the last mutating operation")
    sub.add_parser("validate", help="validate the project")

    p_exp = sub.add_parser("export", help="export kdenlive | mlt | srt | bundle")
    p_exp.add_argument("kind", choices=["kdenlive", "mlt", "srt", "bundle"])
    p_exp.add_argument("--quality", choices=["preview", "master"], default="master")
    p_exp.add_argument("--force", action="store_true")

    p_sync = sub.add_parser("sync-from-kdenlive", help="read GUI edits back into the manifest")
    p_sync.add_argument("file")
    p_sync.add_argument("--dry-run", action="store_true")
    p_sync.add_argument("--expected-revision", type=int)
    p_sync.add_argument("--force", action="store_true")

    p_voice = sub.add_parser("voice", help="synthesize placeholder narration with local Piper TTS")
    p_voice.add_argument("asset_id")
    p_voice.add_argument("text")
    p_voice.add_argument("--model", required=True)
    p_voice.add_argument("--length-scale", type=float, default=1.0)

    p_music = sub.add_parser("music", help="generate an original ambient music bed")
    p_music.add_argument("asset_id")
    p_music.add_argument("seconds", type=float)
    p_music.add_argument("--seed", type=int, default=7)
    p_music.add_argument("--gain-db", type=float, default=-22.0)

    p_master = sub.add_parser("master", help="loudness-normalize a rendered file (two-pass loudnorm)")
    p_master.add_argument("src")
    p_master.add_argument("out")
    p_master.add_argument("--lufs", type=float, default=-16.0)
    p_master.add_argument("--tp", type=float, default=-1.0)

    p_qa = sub.add_parser("qa", help="technical QA of a rendered file: loudness, black, silence, probe")
    p_qa.add_argument("file")

    args = ap.parse_args(argv)
    try:
        return _dispatch(args)
    except ProjectError as exc:
        _out({"ok": False, "error": str(exc)})
        return 2


def _dispatch(args) -> int:
    root = Path(args.project)
    if args.cmd == "capabilities":
        _out(capabilities())
        return 0
    if args.cmd == "init":
        p = Project.create(root, args.name, Fps(args.fps, 1), args.width, args.height, preview=(args.width // 2, args.height // 2))
        _out({"ok": True, "root": str(p.root), "revision": p.revision})
        return 0
    project = Project.load(root)
    if args.cmd == "inspect":
        _out(ops.project_inspect(project, args.capabilities))
    elif args.cmd == "timeline":
        _out(project.timeline_query(args.start, args.end))
    elif args.cmd == "timeline-patch":
        _out(project.timeline_patch(_ops(args), args.expected_revision, args.dry_run))
    elif args.cmd == "scene":
        if args.scmd == "add":
            scene = json.loads(Path(args.file).read_text(encoding="utf-8"))
            _out(project.add_scene(scene))
        elif args.scmd == "inspect":
            _out(ops.scene_inspect(project, args.scene_id, args.frame))
        elif args.scmd == "patch":
            _out(project.scene_patch(args.scene_id, _ops(args), args.expected_revision, args.dry_run))
        elif args.scmd == "render":
            def cb(done, total):
                print(f"Current Frame: {done}, percentage: {int(100 * done / total)}", file=sys.stderr, flush=True)

            _out(ops.ensure_scene_clip(project, args.scene_id, args.quality, force=args.force, progress_cb=cb))
        elif args.scmd == "frame":
            _out({"path": str(ops.scene_frame(project, args.scene_id, args.frame, args.quality))})
    elif args.cmd == "asset":
        if args.acmd == "add":
            meta = {}
            if args.kind in ("audio", "video"):
                from .media import duration_frames

                meta["duration_frames"] = duration_frames(Path(args.file), project.fps, "floor")
            _out(project.add_asset(args.asset_id, Path(args.file), args.kind, args.license, args.provenance, meta=meta))
        elif args.acmd == "replace":
            from .media import duration_frames

            meta = {"duration_frames": duration_frames(Path(args.file), project.fps, "floor")}
            _out(project.replace_asset(args.asset_id, Path(args.file), meta=meta))
        elif args.acmd == "inspect":
            _out(ops.media_inspect(project, args.asset_id))
        elif args.acmd == "segment":
            _out(ops.audio_slice(project, args.asset_id, args.start, args.end))
    elif args.cmd == "title":
        _out(project.set_title(args.title_id, json.loads(args.fields_json), args.expected_revision))
    elif args.cmd == "frame":
        _out(ops.media_frame(project, args.ref, args.frame, args.quality))
    elif args.cmd == "preview":
        job = ops.start_preview_job(project, args.start, args.end, args.quality)
        if args.wait:
            job = jobs.wait(project.root, job["id"], timeout=args.wait)
        _out(job)
    elif args.cmd == "job":
        if args.action == "list":
            _out(jobs.list_jobs(project.root))
        elif args.action == "status":
            _out(jobs.status(project.root, args.job_id))
        elif args.action == "cancel":
            _out(jobs.cancel(project.root, args.job_id))
        elif args.action == "wait":
            _out(jobs.wait(project.root, args.job_id, timeout=args.timeout))
    elif args.cmd == "checkpoint":
        _out({"ok": True, "checkpoint": project.checkpoint(args.label), "revision": project.revision})
    elif args.cmd == "checkpoints":
        _out(project.list_checkpoints())
    elif args.cmd == "restore":
        _out(project.restore(args.checkpoint_id))
    elif args.cmd == "undo":
        _out(project.undo())
    elif args.cmd == "validate":
        _out(project.validate())
    elif args.cmd == "export":
        if args.kind == "kdenlive":
            _out(export_kdenlive(project, ops.scene_resolver(project, "master"), force=args.force))
        elif args.kind == "mlt":
            _out({"path": str(ops.export_mlt(project, args.quality))})
        elif args.kind == "srt":
            _out({"path": str(ops.export_srt(project))})
        elif args.kind == "bundle":
            from .bundle import export_bundle

            _out(export_bundle(project, quality=args.quality))
    elif args.cmd == "sync-from-kdenlive":
        _out(sync_from_kdenlive(project, Path(args.file), args.expected_revision, args.dry_run, args.force))
    elif args.cmd == "voice":
        if not piper_available():
            _out({"ok": False, "error": "piper-tts not installed (pip install 'explica[tts]')"})
            return 2
        out = project.root / "assets" / "voice" / f"{args.asset_id}.wav"
        info = synthesize_voice(args.text, out, Path(args.model), args.length_scale)
        from .media import duration_frames

        meta = {"duration_frames": duration_frames(out, project.fps, "floor"), "tts": info}
        if args.asset_id in project.m["assets"]:
            _out(project.replace_asset(args.asset_id, out, meta=meta))
        else:
            _out(project.add_asset(args.asset_id, out, "audio", license="placeholder synthetic narration (Piper, local)", provenance=f"piper model {Path(args.model).name}", copy_into=None, meta=meta))
    elif args.cmd == "music":
        out = project.root / "assets" / "music" / f"{args.asset_id}.wav"
        info = generate_music(out, args.seconds, args.seed, args.gain_db)
        from .media import duration_frames

        meta = {"duration_frames": duration_frames(out, project.fps, "floor"), "generator": info}
        if args.asset_id in project.m["assets"]:
            _out(project.replace_asset(args.asset_id, out, meta=meta))
        else:
            _out(project.add_asset(args.asset_id, out, "audio", license="CC0 (generated by explica)", provenance="ffmpeg lavfi synthesis", copy_into=None, meta=meta))
    elif args.cmd == "master":
        _out(normalize_loudness(Path(args.src), Path(args.out), args.lufs, args.tp))
    elif args.cmd == "qa":
        from .media import probe

        _out({"probe": probe(Path(args.file)), "loudness": loudness(Path(args.file)), "detect": detect_black_silence(Path(args.file))})
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
