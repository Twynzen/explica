"""Project core: manifest, scenes, timeline, captions, titles, checkpoints, patches.

A project is a directory:

    project.json          manifest (this module owns it)
    scenes/<id>.json      scene sources (explica/scene@1), editable
    assets/               audio/video/image assets, each indexed with sha256
    renders/scenes/       derived clips, cached by (scene hash, size, fps)
    renders/preview/      preview renders and frame grabs
    checkpoints/          snapshots (manifest + scenes) for undo/restore
    jobs/                 render jobs (json + log)
    export/               .kdenlive / .mlt / .srt / bundle

Every mutating operation:
  1. acquires the project lock,
  2. checks `expected_revision` when the caller provides one,
  3. snapshots the project (automatic checkpoint) *before* applying,
  4. applies, validates, bumps `revision`, writes the manifest atomically,
  5. returns {revision, affected_files, warnings, diff, evidence}.
`dry_run=True` performs steps 2 and the diff only.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import SCHEMA_PROJECT, SCHEMA_SCENE, __version__
from .paths import PathError, check_id, relpath, resolve_inside
from .scene.model import SceneError, scene_hash, validate_scene
from .timebase import Fps

DEFAULT_TRACKS = [
    {"id": "V1", "kind": "video", "name": "Fondo"},
    {"id": "V2", "kind": "video", "name": "Escenas"},
    {"id": "V3", "kind": "video", "name": "Títulos"},
    {"id": "V4", "kind": "video", "name": "Captions"},
    {"id": "A1", "kind": "audio", "name": "Voz"},
    {"id": "A2", "kind": "audio", "name": "Música"},
    {"id": "A3", "kind": "audio", "name": "Efectos"},
]

CLIP_SOURCE_KINDS = ("scene", "asset", "title", "color", "caption")


class ProjectError(RuntimeError):
    pass


class RevisionConflict(ProjectError):
    def __init__(self, expected: int, current: int):
        super().__init__(f"revision conflict: expected {expected}, project is at {current}; re-inspect before patching")
        self.expected = expected
        self.current = current


class LockedError(ProjectError):
    pass


@dataclass
class OpResult:
    ok: bool
    op: str
    revision: int
    affected_files: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    diff: list[dict] = field(default_factory=list)
    evidence: dict = field(default_factory=dict)
    limits: list[str] = field(default_factory=list)
    data: dict = field(default_factory=dict)
    dry_run: bool = False
    checkpoint: str | None = None

    def to_dict(self) -> dict:
        d = {
            "ok": self.ok,
            "op": self.op,
            "revision": self.revision,
            "dry_run": self.dry_run,
            "affected_files": self.affected_files,
            "warnings": self.warnings,
            "diff": self.diff,
            "evidence": self.evidence,
            "limits": self.limits,
            "checkpoint": self.checkpoint,
        }
        d.update(self.data)
        return d


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def _atomic_write_json(path: Path, data: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _json_diff(before: Any, after: Any, path: str = "") -> list[dict]:
    """Structural diff (list of {path, before, after})."""
    out: list[dict] = []
    if isinstance(before, dict) and isinstance(after, dict):
        for k in sorted(set(before) | set(after)):
            p = f"{path}.{k}" if path else str(k)
            if k not in before:
                out.append({"path": p, "before": None, "after": after[k]})
            elif k not in after:
                out.append({"path": p, "before": before[k], "after": None})
            else:
                out.extend(_json_diff(before[k], after[k], p))
    elif isinstance(before, list) and isinstance(after, list) and len(before) == len(after):
        for i, (b, a) in enumerate(zip(before, after, strict=True)):
            out.extend(_json_diff(b, a, f"{path}[{i}]"))
    elif before != after:
        out.append({"path": path, "before": before, "after": after})
    return out


class Project:
    MANIFEST = "project.json"

    def __init__(self, root: Path, manifest: dict):
        self.root = Path(root).resolve()
        self.m = manifest

    # ------------------------------------------------------------------ lifecycle
    @classmethod
    def create(
        cls,
        root: Path,
        name: str,
        fps: Fps | None = None,
        width: int = 1080,
        height: int = 1920,
        preview: tuple[int, int] = (540, 960),
        project_id: str | None = None,
        font: str = "Liberation Sans",
    ) -> "Project":
        fps = fps or Fps(30, 1)
        root = Path(root).resolve()
        if (root / cls.MANIFEST).exists():
            raise ProjectError(f"project already exists at {root}")
        root.mkdir(parents=True, exist_ok=True)
        for sub in ("scenes", "assets", "renders/scenes", "renders/preview", "checkpoints", "jobs", "export"):
            (root / sub).mkdir(parents=True, exist_ok=True)
        pid = check_id(project_id or name.lower().replace(" ", "-"), "project id")
        manifest = {
            "schema": SCHEMA_PROJECT,
            "id": pid,
            "name": name,
            "explica_version": __version__,
            "revision": 1,
            "created": _now(),
            "updated": _now(),
            "fps": fps.to_dict(),
            "width": width,
            "height": height,
            "preview": {"width": preview[0], "height": preview[1]},
            "font": font,
            "decimal_separator": ",",
            "assets": {},
            "scenes": {},
            "titles": {},
            "tracks": [dict(t, clips=[]) for t in DEFAULT_TRACKS],
            "captions": [],
            "transcripts": {},
            "sync": {},
            "history": [{"revision": 1, "op": "project.create", "at": _now(), "summary": f"created {name}"}],
            "provenance": {"notes": "", "licenses": []},
        }
        p = cls(root, manifest)
        p._write_manifest()
        return p

    @classmethod
    def load(cls, root: Path) -> "Project":
        root = Path(root).resolve()
        mp = root / cls.MANIFEST
        if not mp.exists():
            raise ProjectError(f"no {cls.MANIFEST} in {root}")
        manifest = json.loads(mp.read_text(encoding="utf-8"))
        if manifest.get("schema") != SCHEMA_PROJECT:
            raise ProjectError(f"unsupported manifest schema {manifest.get('schema')!r}")
        return cls(root, manifest)

    def _write_manifest(self) -> None:
        self.m["updated"] = _now()
        _atomic_write_json(self.root / self.MANIFEST, self.m)

    # ------------------------------------------------------------------ basics
    @property
    def revision(self) -> int:
        return int(self.m["revision"])

    @property
    def fps(self) -> Fps:
        return Fps.from_dict(self.m["fps"])

    @property
    def width(self) -> int:
        return int(self.m["width"])

    @property
    def height(self) -> int:
        return int(self.m["height"])

    def path(self, rel: str, must_exist: bool = False) -> Path:
        return resolve_inside(self.root, rel, must_exist=must_exist)

    def rel(self, p: Path) -> str:
        return relpath(self.root, p)

    def duration_frames(self) -> int:
        end = 0
        for t in self.m["tracks"]:
            for c in t["clips"]:
                end = max(end, int(c["start"]) + int(c["out"]) - int(c["in"]))
        for c in self.m["captions"]:
            end = max(end, int(c["end"]))
        return end

    def track(self, track_id: str) -> dict:
        for t in self.m["tracks"]:
            if t["id"] == track_id:
                return t
        raise ProjectError(f"unknown track {track_id!r}")

    def find_clip(self, clip_id: str) -> tuple[dict, dict]:
        for t in self.m["tracks"]:
            for c in t["clips"]:
                if c["id"] == clip_id:
                    return t, c
        raise ProjectError(f"unknown clip {clip_id!r}")

    # ------------------------------------------------------------------ lock
    def _lock_path(self) -> Path:
        return self.root / ".lock"

    def acquire_lock(self, owner: str = "explica", timeout: float = 5.0) -> None:
        lp = self._lock_path()
        deadline = time.time() + timeout
        while True:
            try:
                fd = os.open(lp, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                with os.fdopen(fd, "w") as fh:
                    json.dump({"pid": os.getpid(), "owner": owner, "at": _now()}, fh)
                return
            except FileExistsError:
                try:
                    info = json.loads(lp.read_text())
                    pid = int(info.get("pid", 0))
                    alive = pid > 0 and _pid_alive(pid)
                except (ValueError, OSError):
                    alive = False
                if not alive:
                    lp.unlink(missing_ok=True)  # stale lock from a dead process
                    continue
                if time.time() > deadline:
                    raise LockedError(f"project locked by pid {pid} ({info.get('owner')}) since {info.get('at')}") from None
                time.sleep(0.1)

    def release_lock(self) -> None:
        lp = self._lock_path()
        try:
            info = json.loads(lp.read_text())
            if int(info.get("pid", -1)) == os.getpid():
                lp.unlink(missing_ok=True)
        except (OSError, ValueError):
            pass

    def lock_state(self) -> dict | None:
        lp = self._lock_path()
        if not lp.exists():
            return None
        try:
            info = json.loads(lp.read_text())
            info["alive"] = _pid_alive(int(info.get("pid", 0)))
            return info
        except (OSError, ValueError):
            return {"corrupt": True}

    # ------------------------------------------------------------------ scenes
    def scene_path(self, scene_id: str) -> Path:
        check_id(scene_id, "scene id")
        entry = self.m["scenes"].get(scene_id)
        if not entry:
            raise ProjectError(f"unknown scene {scene_id!r}")
        return self.path(entry["path"], must_exist=True)

    def load_scene(self, scene_id: str) -> dict:
        return json.loads(self.scene_path(scene_id).read_text(encoding="utf-8"))

    def add_scene(self, scene: dict, expected_revision: int | None = None, dry_run: bool = False) -> OpResult:
        warnings = validate_scene(scene)
        sid = scene["id"]
        if sid in self.m["scenes"]:
            raise ProjectError(f"scene {sid!r} already exists; use scene_patch")
        scene.setdefault("width", self.width)
        scene.setdefault("height", self.height)
        scene.setdefault("font", self.m.get("font", "Liberation Sans"))
        scene.setdefault("decimal_separator", self.m.get("decimal_separator", "."))
        rel = f"scenes/{sid}.json"

        def apply() -> list[str]:
            p = self.path(rel)
            _atomic_write_json(p, scene)
            self.m["scenes"][sid] = {"path": rel, "sha256": scene_hash(scene), "duration_frames": scene["duration_frames"]}
            return [rel]

        return self._mutate("scene.add", expected_revision, dry_run, apply, warnings=warnings, summary=f"add scene {sid}")

    def scene_patch(self, scene_id: str, ops: list[dict], expected_revision: int | None = None, dry_run: bool = False) -> OpResult:
        before = self.load_scene(scene_id)
        after = copy.deepcopy(before)
        for op in ops:
            _apply_scene_op(after, op)
        warnings = validate_scene(after)
        diff = _json_diff(before, after)
        rel = self.m["scenes"][scene_id]["path"]
        old_dur, new_dur = int(before["duration_frames"]), int(after["duration_frames"])
        if new_dur != old_dur:
            for t in self.m["tracks"]:
                for c in t["clips"]:
                    if c["source"]["kind"] == "scene" and c["source"]["ref"] == scene_id and int(c["out"]) > new_dur:
                        warnings.append(f"clip {c['id']} uses frames beyond new scene duration ({c['out']} > {new_dur}); trim it with timeline_patch")

        def apply() -> list[str]:
            _atomic_write_json(self.path(rel), after)
            self.m["scenes"][scene_id] = {"path": rel, "sha256": scene_hash(after), "duration_frames": new_dur}
            return [rel]

        res = self._mutate("scene.patch", expected_revision, dry_run, apply, warnings=warnings, diff=diff, summary=f"patch scene {scene_id} ({len(ops)} ops)")
        res.data["scene_id"] = scene_id
        res.data["scene_hash"] = scene_hash(after)
        return res

    # ------------------------------------------------------------------ assets
    def add_asset(self, asset_id: str, src: Path, kind: str, license: str = "", provenance: str = "", copy_into: str | None = "assets", expected_revision: int | None = None, meta: dict | None = None) -> OpResult:
        check_id(asset_id, "asset id")
        src = Path(src)
        if not src.exists():
            raise ProjectError(f"asset source not found: {src}")
        if asset_id in self.m["assets"]:
            raise ProjectError(f"asset {asset_id!r} exists; use replace_asset")
        dest_rel = self.rel(src) if copy_into is None and _inside(self.root, src) else f"{copy_into or 'assets'}/{src.name}"

        def apply() -> list[str]:
            dest = self.path(dest_rel)
            if dest.resolve() != src.resolve():
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dest)
            info = {"kind": kind, "path": dest_rel, "sha256": sha256_file(dest), "size": dest.stat().st_size, "license": license, "provenance": provenance}
            info.update(meta or {})
            self.m["assets"][asset_id] = info
            return [dest_rel]

        return self._mutate("asset.add", expected_revision, False, apply, summary=f"add asset {asset_id}")

    def replace_asset(self, asset_id: str, src: Path, expected_revision: int | None = None, meta: dict | None = None) -> OpResult:
        """Replace an asset file in place (e.g. a human recording replacing synthetic narration)."""
        entry = self.m["assets"].get(asset_id)
        if not entry:
            raise ProjectError(f"unknown asset {asset_id!r}")
        src = Path(src)
        if not src.exists():
            raise ProjectError(f"asset source not found: {src}")

        def apply() -> list[str]:
            dest = self.path(entry["path"])
            shutil.copy2(src, dest)
            entry["sha256"] = sha256_file(dest)
            entry["size"] = dest.stat().st_size
            entry.update(meta or {})
            return [entry["path"]]

        return self._mutate("asset.replace", expected_revision, False, apply, summary=f"replace asset {asset_id}")

    def asset_path(self, asset_id: str) -> Path:
        entry = self.m["assets"].get(asset_id)
        if not entry:
            raise ProjectError(f"unknown asset {asset_id!r}")
        return self.path(entry["path"], must_exist=True)

    # ------------------------------------------------------------------ titles
    def set_title(self, title_id: str, fields: dict, expected_revision: int | None = None, dry_run: bool = False) -> OpResult:
        check_id(title_id, "title id")
        before = copy.deepcopy(self.m["titles"].get(title_id))
        defaults = {"text": "", "x": self.width // 2, "y": int(self.height * 0.12), "size": 64, "color": "#ffffff", "weight": "bold", "align": "center", "box": False, "box_width": int(self.width * 0.84)}
        after = {**defaults, **(before or {}), **fields}
        if not isinstance(after.get("text"), str):
            raise ProjectError("title.text must be a string")
        diff = _json_diff(before, after, f"titles.{title_id}")

        def apply() -> list[str]:
            self.m["titles"][title_id] = after
            return []

        return self._mutate("title.set", expected_revision, dry_run, apply, diff=diff, summary=f"set title {title_id}")

    # ------------------------------------------------------------------ timeline
    def timeline_query(self, start: int = 0, end: int | None = None) -> dict:
        end = self.duration_frames() if end is None else int(end)
        tracks = []
        for t in self.m["tracks"]:
            clips = []
            for c in sorted(t["clips"], key=lambda c: c["start"]):
                length = int(c["out"]) - int(c["in"])
                cs, ce = int(c["start"]), int(c["start"]) + length
                if ce <= start or cs >= end:
                    continue
                item = dict(c)
                item["end"] = ce
                item["length"] = length
                item["source_info"] = self._source_info(c["source"])
                clips.append(item)
            tracks.append({"id": t["id"], "kind": t["kind"], "name": t["name"], "clips": clips})
        captions = [c for c in self.m["captions"] if not (int(c["end"]) <= start or int(c["start"]) >= end)]
        return {
            "revision": self.revision,
            "fps": self.m["fps"],
            "range": [start, end],
            "interval_convention": "[start, end) in frames",
            "duration_frames": self.duration_frames(),
            "tracks": tracks,
            "captions": captions,
            "titles": self.m["titles"],
        }

    def _source_info(self, src: dict) -> dict:
        kind, ref = src.get("kind"), src.get("ref")
        if kind == "scene":
            e = self.m["scenes"].get(ref, {})
            return {"kind": kind, "ref": ref, "path": e.get("path"), "sha256": e.get("sha256"), "duration_frames": e.get("duration_frames"), "editable": "scene source JSON (objects have ids)"}
        if kind == "asset":
            e = self.m["assets"].get(ref, {})
            return {"kind": kind, "ref": ref, "path": e.get("path"), "sha256": e.get("sha256"), "duration_frames": e.get("duration_frames"), "editable": "replace file via asset.replace"}
        if kind == "title":
            return {"kind": kind, "ref": ref, "title": self.m["titles"].get(ref), "editable": "titles.<id> fields; native Kdenlive title clip on export"}
        return {"kind": kind, "ref": ref}

    def timeline_patch(self, ops: list[dict], expected_revision: int | None = None, dry_run: bool = False) -> OpResult:
        before = copy.deepcopy({"tracks": self.m["tracks"], "captions": self.m["captions"]})
        work = copy.deepcopy(before)
        for op in ops:
            _apply_timeline_op(self, work, op)
        warnings = self._validate_timeline(work["tracks"], work["captions"])
        hard = [w for w in warnings if w.startswith("ERROR")]
        if hard:
            raise ProjectError("; ".join(hard))
        diff = _json_diff(before, work)

        def apply() -> list[str]:
            self.m["tracks"] = work["tracks"]
            self.m["captions"] = work["captions"]
            return []

        return self._mutate("timeline.patch", expected_revision, dry_run, apply, warnings=warnings, diff=diff, summary=f"timeline patch ({len(ops)} ops)")

    def _validate_timeline(self, tracks: list[dict], captions: list[dict]) -> list[str]:
        warnings: list[str] = []
        seen: set[str] = set()
        for t in tracks:
            clips = sorted(t["clips"], key=lambda c: int(c["start"]))
            prev_end = -1
            for c in clips:
                cid = c.get("id", "")
                try:
                    check_id(cid, "clip id")
                except PathError as exc:
                    warnings.append(f"ERROR {exc}")
                    continue
                if cid in seen:
                    warnings.append(f"ERROR duplicate clip id {cid}")
                seen.add(cid)
                src = c.get("source", {})
                if src.get("kind") not in CLIP_SOURCE_KINDS:
                    warnings.append(f"ERROR clip {cid}: unknown source kind {src.get('kind')!r}")
                    continue
                if t["kind"] == "audio" and src["kind"] != "asset":
                    warnings.append(f"ERROR clip {cid}: audio tracks accept only asset sources")
                if t["kind"] == "video" and src["kind"] == "asset":
                    a = self.m["assets"].get(src["ref"])
                    if a and a.get("kind") == "audio":
                        warnings.append(f"ERROR clip {cid}: audio asset on video track {t['id']}")
                in_, out, start = int(c["in"]), int(c["out"]), int(c["start"])
                if start < 0 or in_ < 0 or out <= in_:
                    warnings.append(f"ERROR clip {cid}: need start>=0, 0<=in<out (half-open)")
                    continue
                if start < prev_end:
                    warnings.append(f"ERROR clip {cid} overlaps previous clip on track {t['id']} (ends {prev_end}, starts {start})")
                prev_end = start + (out - in_)
                src_dur = self._source_duration(src)
                if src_dur is not None and out > src_dur:
                    warnings.append(f"ERROR clip {cid}: out {out} exceeds source duration {src_dur}")
                if src["kind"] == "scene" and src["ref"] not in self.m["scenes"]:
                    warnings.append(f"ERROR clip {cid}: unknown scene {src['ref']}")
                if src["kind"] == "asset" and src["ref"] not in self.m["assets"]:
                    warnings.append(f"ERROR clip {cid}: unknown asset {src['ref']}")
                if src["kind"] == "title" and src["ref"] not in self.m["titles"]:
                    warnings.append(f"ERROR clip {cid}: unknown title {src['ref']}")
        last_end = 0
        dur = max([int(c["start"]) + int(c["out"]) - int(c["in"]) for t in tracks for c in t["clips"]] + [0])
        for c in sorted(captions, key=lambda c: int(c["start"])):
            s, e = int(c["start"]), int(c["end"])
            if not (0 <= s < e):
                warnings.append(f"ERROR caption {c.get('id')}: need 0 <= start < end")
            if s < last_end:
                warnings.append(f"ERROR caption {c.get('id')} overlaps previous caption (ends {last_end}, starts {s})")
            if dur and e > dur:
                warnings.append(f"caption {c.get('id')} ends at {e} after the last clip ({dur})")
            if not str(c.get("text", "")).strip():
                warnings.append(f"caption {c.get('id')} is empty")
            last_end = max(last_end, e)
        return warnings

    def _source_duration(self, src: dict) -> int | None:
        if src["kind"] == "scene":
            e = self.m["scenes"].get(src["ref"])
            return int(e["duration_frames"]) if e else None
        if src["kind"] == "asset":
            e = self.m["assets"].get(src["ref"])
            return int(e["duration_frames"]) if e and e.get("duration_frames") else None
        return None

    # ------------------------------------------------------------------ validate
    def validate(self, deep: bool = True) -> dict:
        errors: list[str] = []
        warnings: list[str] = []
        for w in self._validate_timeline(self.m["tracks"], self.m["captions"]):
            (errors if w.startswith("ERROR") else warnings).append(w.replace("ERROR ", "", 1))
        for sid, e in self.m["scenes"].items():
            try:
                scene = json.loads(self.path(e["path"], must_exist=True).read_text(encoding="utf-8"))
                warnings.extend(f"scene {sid}: {w}" for w in validate_scene(scene))
                if scene_hash(scene) != e.get("sha256"):
                    warnings.append(f"scene {sid}: file hash differs from manifest (edited outside explica?)")
                if int(scene["duration_frames"]) != int(e["duration_frames"]):
                    errors.append(f"scene {sid}: duration mismatch manifest/file")
            except (OSError, ValueError, SceneError, PathError) as exc:
                errors.append(f"scene {sid}: {exc}")
        for aid, e in self.m["assets"].items():
            try:
                p = self.path(e["path"], must_exist=True)
                if deep and sha256_file(p) != e.get("sha256"):
                    warnings.append(f"asset {aid}: file hash differs from manifest (file replaced outside explica?)")
            except (PathError, OSError) as exc:
                errors.append(f"asset {aid}: {exc}")
        if deep:
            warnings.extend(self._caption_overlap_warnings())
        sync = self.m.get("sync", {}).get("kdenlive")
        if sync:
            try:
                p = self.path(sync["path"], must_exist=True)
                if sha256_file(p) != sync["sha256"]:
                    warnings.append("Kdenlive project changed since last sync; run sync-from-kdenlive before exporting again")
            except (PathError, OSError):
                warnings.append("Kdenlive project referenced in sync is missing")
        return {"ok": not errors, "revision": self.revision, "errors": errors, "warnings": warnings, "duration_frames": self.duration_frames()}

    def _caption_overlap_warnings(self) -> list[str]:
        """Captions must not cover diagram objects: compare caption box with scene object bounds."""
        from .scene.render import BBox, SceneRenderer

        out: list[str] = []
        cap_box = self.caption_box()
        box = BBox(cap_box["x0"], cap_box["y0"], cap_box["x1"], cap_box["y1"])
        v2 = [t for t in self.m["tracks"] if t["id"] == "V2"]
        if not v2 or not self.m["captions"]:
            return out
        renderers: dict[str, SceneRenderer] = {}
        for cap in self.m["captions"]:
            mid = (int(cap["start"]) + int(cap["end"])) // 2
            for c in v2[0]["clips"]:
                if c["source"]["kind"] != "scene":
                    continue
                length = int(c["out"]) - int(c["in"])
                if not (int(c["start"]) <= mid < int(c["start"]) + length):
                    continue
                sid = c["source"]["ref"]
                r = renderers.get(sid)
                if r is None:
                    r = renderers[sid] = SceneRenderer(self.load_scene(sid))
                local = mid - int(c["start"]) + int(c["in"])
                for oid, info in r.bounds(local).items():
                    if oid == "camera" or not info["visible"] or info["opacity"] < 0.05:
                        continue
                    b = info["bbox"]
                    if box.intersects(BBox(b["x0"], b["y0"], b["x1"], b["y1"])):
                        out.append(f"caption {cap['id']} box overlaps {sid}.{oid} at frame {mid}")
        return out

    def caption_box(self) -> dict:
        """Caption safe box in logical coordinates (bottom area, inside 10% margins)."""
        w, h = self.width, self.height
        return {"x0": w * 0.08, "y0": h * 0.78, "x1": w * 0.92, "y1": h * 0.90}

    # ------------------------------------------------------------------ checkpoints
    def checkpoint(self, label: str = "manual", op: str | None = None) -> str:
        cps = self.root / "checkpoints"
        cps.mkdir(exist_ok=True)
        n = len([d for d in cps.iterdir() if d.is_dir()]) + 1
        safe = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in label)[:40] or "cp"
        cid = f"{n:04d}-{safe}"
        dest = cps / cid
        dest.mkdir()
        shutil.copy2(self.root / self.MANIFEST, dest / self.MANIFEST)
        if (self.root / "scenes").exists():
            shutil.copytree(self.root / "scenes", dest / "scenes")
        meta = {"id": cid, "label": label, "op": op, "revision": self.revision, "at": _now()}
        _atomic_write_json(dest / "checkpoint.json", meta)
        return cid

    def list_checkpoints(self) -> list[dict]:
        cps = self.root / "checkpoints"
        out = []
        if cps.exists():
            for d in sorted(cps.iterdir()):
                mp = d / "checkpoint.json"
                if mp.exists():
                    out.append(json.loads(mp.read_text()))
        return out

    def restore(self, checkpoint_id: str, expected_revision: int | None = None) -> OpResult:
        check_id(checkpoint_id, "checkpoint id")
        src = self.root / "checkpoints" / checkpoint_id
        if not (src / self.MANIFEST).exists():
            raise ProjectError(f"unknown checkpoint {checkpoint_id!r}")
        if expected_revision is not None and expected_revision != self.revision:
            raise RevisionConflict(expected_revision, self.revision)
        self.acquire_lock("restore")
        try:
            backup = self.checkpoint(f"before-restore-{checkpoint_id}", op="project.restore")
            restored = json.loads((src / self.MANIFEST).read_text(encoding="utf-8"))
            scenes_dir = self.root / "scenes"
            if scenes_dir.exists():
                shutil.rmtree(scenes_dir)
            if (src / "scenes").exists():
                shutil.copytree(src / "scenes", scenes_dir)
            else:
                scenes_dir.mkdir()
            # keep the revision counter monotonic so clients never see it go backwards
            new_rev = self.revision + 1
            history = self.m.get("history", [])
            restored["history"] = history + [{"revision": new_rev, "op": "project.restore", "at": _now(), "summary": f"restored {checkpoint_id} (was revision {self.revision}, snapshot of revision {restored['revision']})"}]
            restored["revision"] = new_rev
            self.m = restored
            self._write_manifest()
        finally:
            self.release_lock()
        return OpResult(True, "project.restore", self.revision, affected_files=[self.MANIFEST, "scenes/"], checkpoint=backup, data={"restored": checkpoint_id})

    def undo(self) -> OpResult:
        """Restore the automatic snapshot taken before the most recent mutating operation."""
        cps = [c for c in self.list_checkpoints() if c.get("op") and c["op"] not in ("project.restore",)]
        if not cps:
            raise ProjectError("nothing to undo (no automatic checkpoints)")
        last = cps[-1]
        res = self.restore(last["id"])
        res.op = "project.undo"
        res.data["undone"] = last
        return res

    # ------------------------------------------------------------------ inspect
    def inspect(self, capabilities: dict | None = None) -> dict:
        tracks = []
        for t in self.m["tracks"]:
            tracks.append({"id": t["id"], "kind": t["kind"], "name": t["name"], "clips": len(t["clips"])})
        return {
            "schema": self.m["schema"],
            "explica_version": __version__,
            "id": self.m["id"],
            "name": self.m["name"],
            "root": str(self.root),
            "revision": self.revision,
            "fps": self.m["fps"],
            "size": {"width": self.width, "height": self.height},
            "preview": self.m["preview"],
            "duration_frames": self.duration_frames(),
            "interval_convention": "[start, end) in frames; MLT out = end - 1",
            "assets": {k: {kk: vv for kk, vv in v.items() if kk != "meta"} for k, v in self.m["assets"].items()},
            "scenes": self.m["scenes"],
            "titles": self.m["titles"],
            "tracks": tracks,
            "captions": len(self.m["captions"]),
            "checkpoints": len(self.list_checkpoints()),
            "lock": self.lock_state(),
            "sync": self.m.get("sync", {}),
            "history_tail": self.m.get("history", [])[-5:],
            "capabilities": capabilities,
            "limits": [
                "frames returned as PNG files/images; audio returned as WAV file paths — a client that cannot open them must ask a human to listen/look",
                "render jobs are asynchronous; poll job_status no faster than every 2 s or use wait_seconds",
                "Kdenlive GUI edits are read back with sync_from_kdenlive; exporting over a modified .kdenlive is refused",
            ],
        }

    # ------------------------------------------------------------------ mutate helper
    def _mutate(self, op: str, expected_revision: int | None, dry_run: bool, apply, warnings: list[str] | None = None, diff: list[dict] | None = None, summary: str = "") -> OpResult:
        if expected_revision is not None and int(expected_revision) != self.revision:
            raise RevisionConflict(int(expected_revision), self.revision)
        warnings = list(warnings or [])
        if dry_run:
            return OpResult(True, op, self.revision, warnings=warnings, diff=diff or [], dry_run=True, data={"would_apply": True})
        self.acquire_lock(op)
        try:
            cp = self.checkpoint(op, op=op)
            files = apply()
            self.m["revision"] = self.revision + 1
            self.m.setdefault("history", []).append({"revision": self.revision, "op": op, "at": _now(), "summary": summary, "checkpoint": cp})
            self._write_manifest()
        finally:
            self.release_lock()
        return OpResult(True, op, self.revision, affected_files=[self.MANIFEST, *files], warnings=warnings, diff=diff or [], checkpoint=cp)


# ---------------------------------------------------------------------- op appliers
def _apply_scene_op(scene: dict, op: dict) -> None:
    kind = op.get("op")
    if kind == "set":
        path = op.get("path", "")
        if not path or path in ("schema", "id"):
            raise ProjectError(f"cannot set {path!r}")
        _set_scene_path(scene, path, op.get("value"))
    elif kind == "add_object":
        obj = op.get("object")
        if not isinstance(obj, dict) or "id" not in obj:
            raise ProjectError("add_object needs {'object': {...with id, type}}")
        if any(o["id"] == obj["id"] for o in scene["objects"]):
            raise ProjectError(f"object {obj['id']} exists")
        idx = op.get("index")
        scene["objects"].insert(len(scene["objects"]) if idx is None else int(idx), obj)
    elif kind == "remove_object":
        oid = op.get("id")
        scene["objects"] = [o for o in scene["objects"] if o["id"] != oid]
        scene["animations"] = [a for a in scene.get("animations", []) if a["target"] != oid]
    elif kind == "add_animation":
        anim = op.get("animation")
        if not isinstance(anim, dict) or "id" not in anim:
            raise ProjectError("add_animation needs {'animation': {...}}")
        scene.setdefault("animations", []).append(anim)
    elif kind == "remove_animation":
        scene["animations"] = [a for a in scene.get("animations", []) if a["id"] != op.get("id")]
    elif kind == "set_duration":
        scene["duration_frames"] = int(op["frames"])
    else:
        raise ProjectError(f"unknown scene op {kind!r}")


def _set_scene_path(scene: dict, path: str, value: Any) -> None:
    """Paths: 'objects.<id>.<prop>[.sub]', 'animations.<id>.<prop>', 'camera.<prop>', 'background', 'duration_frames', ..."""
    parts = path.split(".")
    if parts[0] in ("objects", "animations"):
        if len(parts) < 3:
            raise ProjectError(f"path too short: {path}")
        coll = scene.setdefault(parts[0], [])
        target = next((o for o in coll if o.get("id") == parts[1]), None)
        if target is None:
            raise ProjectError(f"{parts[0]}: no item with id {parts[1]!r}")
        _set_nested(target, parts[2:], value)
    elif parts[0] == "camera":
        _set_nested(scene.setdefault("camera", {}), parts[1:], value)
    elif len(parts) == 1:
        scene[parts[0]] = value
    else:
        _set_nested(scene, parts, value)


def _set_nested(target: Any, parts: list[str], value: Any) -> None:
    cur = target
    for p in parts[:-1]:
        if isinstance(cur, list):
            cur = cur[int(p)]
        else:
            cur = cur.setdefault(p, {})
    last = parts[-1]
    if isinstance(cur, list):
        cur[int(last)] = value
    else:
        cur[last] = value


def _apply_timeline_op(project: Project, work: dict, op: dict) -> None:
    kind = op.get("op")
    tracks = work["tracks"]

    def find(cid: str) -> tuple[dict, dict]:
        for t in tracks:
            for c in t["clips"]:
                if c["id"] == cid:
                    return t, c
        raise ProjectError(f"unknown clip {cid!r}")

    if kind == "add_clip":
        track = next((t for t in tracks if t["id"] == op.get("track")), None)
        if track is None:
            raise ProjectError(f"unknown track {op.get('track')!r}")
        clip = dict(op.get("clip", {}))
        for key in ("id", "source", "start", "in", "out"):
            if key not in clip:
                raise ProjectError(f"add_clip: clip.{key} required")
        clip.setdefault("gain_db", 0.0)
        clip.setdefault("enabled", True)
        track["clips"].append(clip)
    elif kind == "remove_clip":
        t, c = find(op["id"])
        t["clips"].remove(c)
    elif kind == "move_clip":
        t, c = find(op["id"])
        c["start"] = int(op["start"])
        if op.get("track") and op["track"] != t["id"]:
            t["clips"].remove(c)
            next(tt for tt in tracks if tt["id"] == op["track"])["clips"].append(c)
    elif kind == "trim_clip":
        _, c = find(op["id"])
        if "in" in op:
            c["in"] = int(op["in"])
        if "out" in op:
            c["out"] = int(op["out"])
        if "start" in op:
            c["start"] = int(op["start"])
    elif kind == "split_clip":
        t, c = find(op["id"])
        at = int(op["at"])  # timeline frame
        length = int(c["out"]) - int(c["in"])
        if not (int(c["start"]) < at < int(c["start"]) + length):
            raise ProjectError("split point must be strictly inside the clip")
        offset = at - int(c["start"])
        right = copy.deepcopy(c)
        right["id"] = op.get("new_id") or f"{c['id']}-b"
        right["start"] = at
        right["in"] = int(c["in"]) + offset
        c["out"] = int(c["in"]) + offset
        t["clips"].append(right)
    elif kind == "set_gain":
        _, c = find(op["id"])
        c["gain_db"] = float(op["gain_db"])
    elif kind == "set_enabled":
        _, c = find(op["id"])
        c["enabled"] = bool(op["enabled"])
    elif kind == "add_caption":
        cap = {"id": check_id(op["id"], "caption id"), "start": int(op["start"]), "end": int(op["end"]), "text": str(op["text"])}
        if any(c["id"] == cap["id"] for c in work["captions"]):
            raise ProjectError(f"caption {cap['id']} exists")
        work["captions"].append(cap)
        work["captions"].sort(key=lambda c: c["start"])
    elif kind == "set_caption":
        cap = next((c for c in work["captions"] if c["id"] == op["id"]), None)
        if cap is None:
            raise ProjectError(f"unknown caption {op['id']!r}")
        for key in ("start", "end"):
            if key in op:
                cap[key] = int(op[key])
        if "text" in op:
            cap["text"] = str(op["text"])
        work["captions"].sort(key=lambda c: c["start"])
    elif kind == "remove_caption":
        work["captions"] = [c for c in work["captions"] if c["id"] != op["id"]]
    elif kind == "shift":
        # shift everything at/after `from_frame` by `delta` frames on all tracks (ripple)
        from_frame, delta = int(op["from_frame"]), int(op["delta"])
        for t in tracks:
            for c in t["clips"]:
                if int(c["start"]) >= from_frame:
                    c["start"] = int(c["start"]) + delta
        for c in work["captions"]:
            if int(c["start"]) >= from_frame:
                c["start"] += delta
                c["end"] += delta
    else:
        raise ProjectError(f"unknown timeline op {kind!r}")


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _inside(root: Path, p: Path) -> bool:
    try:
        Path(p).resolve().relative_to(Path(root).resolve())
        return True
    except ValueError:
        return False


__all__ = ["Project", "ProjectError", "RevisionConflict", "LockedError", "OpResult", "sha256_file", "SCHEMA_SCENE"]
