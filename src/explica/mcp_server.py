"""MCP stdio server exposing the explica core to agents (same operations as the CLI).

Security posture: stdio only (no network), one project root fixed at start,
all paths confined to that root, argv-only subprocesses, no code execution
tool. Text received from clients is data: it is written into scene JSON /
titles and rendered as glyphs, never evaluated.

Every tool result is a JSON object with at least: `revision`, `warnings`,
`limits` (what the client cannot observe) and `evidence` (files it can open).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

try:  # mcp >= 2.0
    from mcp.server.mcpserver import Image
    from mcp.server.mcpserver import MCPServer as FastMCP
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP
    from mcp.server.fastmcp.utilities.types import Image

from . import __version__, jobs, ops
from .kdenlive import export_kdenlive, sync_from_kdenlive
from .project import Project, ProjectError, RevisionConflict
from .tools import capabilities

GUIDE = (Path(__file__).parent / "guide.md").read_text(encoding="utf-8")


def build_server(project_root: Path, log_path: Path | None = None) -> FastMCP:
    root = Path(project_root).resolve()
    if not (root / "project.json").exists():
        raise SystemExit(f"no project.json in {root}")
    mcp = FastMCP("explica", instructions="Local explanatory-video lab. Call project_guide first, then project_inspect.")
    log_file = log_path or root / "mcp-server.log"

    def log(tool: str, args: dict, result: Any, started: float) -> None:
        try:
            with open(log_file, "a", encoding="utf-8") as fh:
                summary = result if isinstance(result, (str, int, float)) else {k: v for k, v in (result or {}).items() if k in ("ok", "revision", "op", "error", "path", "id", "status", "checkpoint", "warnings")}
                fh.write(json.dumps({"at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "pid": os.getpid(), "tool": tool, "args": args, "result": summary, "ms": int((time.time() - started) * 1000)}, ensure_ascii=False, default=str) + "\n")
        except OSError:
            pass

    def load() -> Project:
        return Project.load(root)

    def wrap(tool: str, args: dict, fn):
        started = time.time()
        try:
            res = fn()
            if hasattr(res, "to_dict"):
                res = res.to_dict()
            log(tool, args, res, started)
            return res
        except RevisionConflict as exc:
            res = {"ok": False, "error": str(exc), "current_revision": exc.current, "expected_revision": exc.expected}
            log(tool, args, res, started)
            return res
        except (ProjectError, ValueError, RuntimeError, OSError) as exc:
            res = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
            log(tool, args, res, started)
            return res

    # ------------------------------------------------------------------ docs
    @mcp.tool()
    def project_guide() -> str:
        """One-page guide: workflow (inspect → frame → patch → preview → undo), conventions, limits. Read this first."""
        log("project_guide", {}, "guide", time.time())
        return GUIDE

    # ------------------------------------------------------------------ inspect
    @mcp.tool()
    def project_inspect(with_capabilities: bool = False) -> dict:
        """Project summary: revision, fps, size, assets, scenes, titles, tracks, lock, sync state, measured tool capabilities."""
        return wrap("project_inspect", {"with_capabilities": with_capabilities}, lambda: ops.project_inspect(load(), with_capabilities))

    @mcp.tool()
    def timeline_query(start_frame: int = 0, end_frame: int | None = None) -> dict:
        """Clips per track overlapping [start_frame, end_frame), with their sources (scene file + hash, asset, title), captions and titles."""
        return wrap("timeline_query", {"start_frame": start_frame, "end_frame": end_frame}, lambda: load().timeline_query(start_frame, end_frame))

    @mcp.tool()
    def media_inspect(asset_id: str) -> dict:
        """ffprobe metadata for an asset (streams, duration, sample rate) plus its manifest entry (hash, license, provenance)."""
        return wrap("media_inspect", {"asset_id": asset_id}, lambda: ops.media_inspect(load(), asset_id))

    @mcp.tool()
    def media_frame(ref: str, frame: int, quality: str = "preview", return_image: bool = True) -> list:
        """Evidence image at `frame`. ref: 'timeline' (MLT composite of all tracks), 'scene:<id>' (scene source render) or 'asset:<id>'.
        Returns the PNG path (and the image inline when return_image=true, if your client renders images)."""
        started = time.time()
        try:
            info = ops.media_frame(load(), ref, frame, quality)
        except (ProjectError, ValueError, RuntimeError, OSError) as exc:
            res = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
            log("media_frame", {"ref": ref, "frame": frame}, res, started)
            return [json.dumps(res)]
        info["limits"] = ["If your client cannot display images, open the PNG path with another tool or ask a human to look."]
        log("media_frame", {"ref": ref, "frame": frame, "quality": quality}, info, started)
        out: list = [json.dumps(info, ensure_ascii=False)]
        if return_image:
            data = Path(info["path"]).read_bytes()
            out.append(Image(data=data, format="png"))
        return out

    @mcp.tool()
    def audio_segment(asset_id: str, start_frame: int, end_frame: int) -> dict:
        """Extract [start_frame, end_frame) of an audio asset as a mono 16-bit WAV file. Returns the path; the server cannot make you hear it."""
        return wrap("audio_segment", {"asset_id": asset_id, "start_frame": start_frame, "end_frame": end_frame}, lambda: ops.audio_slice(load(), asset_id, start_frame, end_frame))

    @mcp.tool()
    def transcript_query(asset_id: str | None = None, start_frame: int = 0, end_frame: int | None = None, text: str | None = None) -> dict:
        """Query stored transcripts/captions (with provenance and confidence). Returns matching segments; never invents timestamps."""

        def run():
            p = load()
            end = p.duration_frames() if end_frame is None else end_frame
            out = []
            for aid, tr in p.m.get("transcripts", {}).items():
                if asset_id and aid != asset_id:
                    continue
                for seg in tr.get("segments", []):
                    if seg["end"] <= start_frame or seg["start"] >= end:
                        continue
                    if text and text.lower() not in seg["text"].lower():
                        continue
                    out.append({"asset_id": aid, **seg, "provenance": tr.get("provenance"), "model": tr.get("model")})
            caps = [c for c in p.m["captions"] if not (c["end"] <= start_frame or c["start"] >= end) and (not text or text.lower() in c["text"].lower())]
            return {"revision": p.revision, "segments": out, "captions": caps, "limits": ["transcripts are stored data with their provenance; none is generated here"]}

        return wrap("transcript_query", {"asset_id": asset_id, "start_frame": start_frame, "end_frame": end_frame, "text": text}, run)

    # ------------------------------------------------------------------ scenes
    @mcp.tool()
    def scene_inspect(scene_id: str, frame: int | None = None) -> dict:
        """Scene source JSON (objects with ids, animations, camera) and, if `frame` is given, each object's bounding box at that frame."""
        return wrap("scene_inspect", {"scene_id": scene_id, "frame": frame}, lambda: ops.scene_inspect(load(), scene_id, frame))

    @mcp.tool()
    def scene_patch(scene_id: str, ops_json: str, expected_revision: int | None = None, dry_run: bool = False) -> dict:
        """Patch a scene source. ops_json is a JSON list of ops:
        {"op":"set","path":"objects.<id>.text","value":"..."} | {"op":"set","path":"objects.<id>.x","value":540}
        {"op":"add_object","object":{...}} | {"op":"remove_object","id":"..."}
        {"op":"add_animation","animation":{...}} | {"op":"remove_animation","id":"..."} | {"op":"set_duration","frames":N}
        Validates the result, returns a diff; dry_run=true previews without writing. A checkpoint is taken before applying."""
        return wrap("scene_patch", {"scene_id": scene_id, "ops_json": ops_json, "expected_revision": expected_revision, "dry_run": dry_run}, lambda: load().scene_patch(scene_id, json.loads(ops_json), expected_revision, dry_run))

    # ------------------------------------------------------------------ timeline
    @mcp.tool()
    def timeline_patch(ops_json: str, expected_revision: int | None = None, dry_run: bool = False) -> dict:
        """Patch the timeline. ops_json is a JSON list of ops:
        move_clip{id,start[,track]} trim_clip{id[,in][,out][,start]} split_clip{id,at[,new_id]} set_gain{id,gain_db}
        add_clip{track,clip{id,source{kind,ref},start,in,out}} remove_clip{id} set_enabled{id,enabled}
        add_caption{id,start,end,text} set_caption{id[,start][,end][,text]} remove_caption{id} shift{from_frame,delta}
        Frames are half-open [start,end). Validates (overlaps, source lengths, captions) and returns a diff."""
        return wrap("timeline_patch", {"ops_json": ops_json, "expected_revision": expected_revision, "dry_run": dry_run}, lambda: load().timeline_patch(json.loads(ops_json), expected_revision, dry_run))

    @mcp.tool()
    def title_set(title_id: str, fields_json: str, expected_revision: int | None = None, dry_run: bool = False) -> dict:
        """Create/update a native title (fields: text, x, y, size, color, weight, align, box). Exported as an editable Kdenlive title clip."""
        return wrap("title_set", {"title_id": title_id, "fields_json": fields_json}, lambda: load().set_title(title_id, json.loads(fields_json), expected_revision, dry_run))

    # ------------------------------------------------------------------ checkpoints
    @mcp.tool()
    def project_checkpoint(label: str = "manual") -> dict:
        """Snapshot manifest + scene sources. Returns the checkpoint id."""

        def run():
            p = load()
            cid = p.checkpoint(label)
            return {"ok": True, "checkpoint": cid, "revision": p.revision, "checkpoints": p.list_checkpoints()[-5:]}

        return wrap("project_checkpoint", {"label": label}, run)

    @mcp.tool()
    def project_restore(checkpoint_id: str | None = None) -> dict:
        """Restore a checkpoint, or undo the last mutating operation when checkpoint_id is omitted. The revision keeps increasing."""
        return wrap("project_restore", {"checkpoint_id": checkpoint_id}, lambda: (load().restore(checkpoint_id) if checkpoint_id else load().undo()))

    @mcp.tool()
    def project_history() -> dict:
        """Revision history and available checkpoints."""
        return wrap("project_history", {}, lambda: {"revision": load().revision, "history": load().m.get("history", [])[-30:], "checkpoints": load().list_checkpoints()})

    # ------------------------------------------------------------------ render/jobs
    @mcp.tool()
    def render_preview(start_frame: int = 0, end_frame: int | None = None, quality: str = "preview", wait_seconds: float = 0) -> dict:
        """Start an asynchronous timeline render (scene clips are rendered/cached first). Returns a job; poll job_status ≥2 s apart or pass wait_seconds."""

        def run():
            p = load()
            job = ops.start_preview_job(p, start_frame, end_frame, quality)
            if wait_seconds:
                job = jobs.wait(p.root, job["id"], timeout=wait_seconds)
            job["limits"] = ["one heavy render at a time on this host; do not start another until status != running"]
            return job

        return wrap("render_preview", {"start_frame": start_frame, "end_frame": end_frame, "quality": quality, "wait_seconds": wait_seconds}, run)

    @mcp.tool()
    def job_status(job_id: str, wait_seconds: float = 0) -> dict:
        """Status/progress/log tail of a job (optionally wait up to wait_seconds)."""
        return wrap("job_status", {"job_id": job_id, "wait_seconds": wait_seconds}, lambda: (jobs.wait(root, job_id, timeout=wait_seconds) if wait_seconds else jobs.status(root, job_id)))

    @mcp.tool()
    def job_cancel(job_id: str) -> dict:
        """Cancel a running job started by this server (only processes we launched)."""
        return wrap("job_cancel", {"job_id": job_id}, lambda: jobs.cancel(root, job_id))

    # ------------------------------------------------------------------ validate/export
    @mcp.tool()
    def project_validate() -> dict:
        """Validate timeline, scenes, assets, captions (overlap with diagrams) and Kdenlive sync state."""
        return wrap("project_validate", {}, lambda: load().validate())

    @mcp.tool()
    def project_export(kind: str = "kdenlive", quality: str = "master", force: bool = False) -> dict:
        """Export: 'kdenlive' (native editable project), 'mlt' (portable XML), 'srt' (captions) or 'bundle' (everything + QA)."""

        def run():
            p = load()
            if kind == "kdenlive":
                return export_kdenlive(p, ops.scene_resolver(p, "master"), force=force)
            if kind == "mlt":
                return {"path": str(ops.export_mlt(p, quality))}
            if kind == "srt":
                return {"path": str(ops.export_srt(p))}
            if kind == "bundle":
                from .bundle import export_bundle

                return export_bundle(p, quality=quality)
            raise ProjectError("kind must be kdenlive | mlt | srt | bundle")

        return wrap("project_export", {"kind": kind, "quality": quality, "force": force}, run)

    @mcp.tool()
    def kdenlive_sync(path: str | None = None, expected_revision: int | None = None, dry_run: bool = False, force: bool = False) -> dict:
        """Read human edits from the .kdenlive file back into the manifest (cuts, gains, titles, captions). A divergent state (manifest changed after export) is refused unless force=true."""

        def run():
            p = load()
            target = Path(path) if path else p.path(p.m.get("sync", {}).get("kdenlive", {}).get("path", f"export/{p.m['id']}.kdenlive"))
            target = p.path(str(target), must_exist=True)
            return sync_from_kdenlive(p, target, expected_revision, dry_run, force)

        return wrap("kdenlive_sync", {"path": path, "expected_revision": expected_revision, "dry_run": dry_run, "force": force}, run)

    @mcp.tool()
    def capabilities_report() -> dict:
        """Measured tool availability on this machine (ffmpeg, melt services, kdenlive, pycairo, piper) and MLT warnings."""
        return wrap("capabilities_report", {}, lambda: {"explica": __version__, **capabilities()})

    return mcp


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="explica-mcp", description="explica MCP server (stdio)")
    ap.add_argument("--project", "-p", required=True, help="project directory (contains project.json)")
    ap.add_argument("--log", help="JSONL call log (default: <project>/mcp-server.log)")
    args = ap.parse_args(argv)
    server = build_server(Path(args.project), Path(args.log) if args.log else None)
    server.run(transport="stdio")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
