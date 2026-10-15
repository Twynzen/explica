"""Scene schema, validation, easing and per-frame state resolution."""

from __future__ import annotations

import copy
import hashlib
import json
import math
from typing import Any

from .. import SCHEMA_SCENE
from ..paths import check_id


class SceneError(ValueError):
    pass


OBJECT_TYPES: dict[str, dict[str, Any]] = {
    # type: default properties (besides id/type/x/y/opacity/color/visible)
    "text": {"text": "", "size": 56, "align": "center", "weight": "normal", "italic": False, "max_width": None, "line_height": 1.25},
    "formula": {"text": "", "size": 56, "align": "center", "weight": "normal"},
    "token": {"text": "", "size": 56, "pad_x": 28, "pad_y": 16, "fill": "#000000", "stroke": "#ffffff", "stroke_width": 3, "radius": 40, "glow": 0.0},
    "vector": {"values": [], "size": 44, "decimals": 2, "label": "", "gap": 8, "bracket": True, "label_size": 36},
    "matrix": {"rows": [], "size": 44, "decimals": 2, "cell_w": 150, "cell_h": 72, "row_labels": [], "col_labels": [], "label_size": 34},
    "circle": {"r": 40, "fill": None, "stroke": "#ffffff", "stroke_width": 3},
    "rect": {"w": 100, "h": 100, "rx": 0, "fill": None, "stroke": "#ffffff", "stroke_width": 3},
    "line": {"x2": 0, "y2": 0, "width": 3, "progress": 1.0, "dash": None},
    "arrow": {"x2": 0, "y2": 0, "width": 3, "progress": 1.0, "head": 18},
    "arc": {"from": None, "to": None, "lift": 120, "width": 3, "progress": 1.0, "weight": 1.0, "anchor": "top", "min_width": 1.5, "max_width": 14},
    "bar": {"w": 60, "h_max": 240, "value": 0.0, "fill": "#ffffff", "label": "", "decimals": 2, "show_value": True, "size": 36},
    "number": {"value": 0.0, "decimals": 2, "size": 56, "prefix": "", "suffix": "", "align": "center", "weight": "normal"},
}

COMMON_DEFAULTS: dict[str, Any] = {"x": 0.0, "y": 0.0, "opacity": 1.0, "color": "#ffffff", "visible": True, "rotation": 0.0, "scale": 1.0}

EASINGS = ("linear", "ease_in", "ease_out", "ease_in_out", "step", "smooth")


def ease(name: str, t: float) -> float:
    t = min(1.0, max(0.0, t))
    if name == "linear":
        return t
    if name == "ease_in":
        return t * t
    if name == "ease_out":
        return 1 - (1 - t) * (1 - t)
    if name == "ease_in_out":
        return 0.5 - 0.5 * math.cos(math.pi * t)
    if name == "smooth":
        return t * t * (3 - 2 * t)
    if name == "step":
        return 1.0 if t >= 1.0 else 0.0
    raise SceneError(f"unknown easing: {name}")


def validate_scene(scene: dict) -> list[str]:
    """Validate structure; raise SceneError on hard errors, return warnings."""
    warnings: list[str] = []
    if not isinstance(scene, dict):
        raise SceneError("scene must be an object")
    if scene.get("schema") != SCHEMA_SCENE:
        raise SceneError(f"scene.schema must be {SCHEMA_SCENE!r}")
    check_id(scene.get("id", ""), "scene id")
    dur = scene.get("duration_frames")
    if not isinstance(dur, int) or dur <= 0:
        raise SceneError("duration_frames must be a positive integer")
    for key in ("width", "height"):
        v = scene.get(key)
        if v is not None and (not isinstance(v, int) or v <= 0 or v % 2):
            raise SceneError(f"{key} must be a positive even integer")
    bg = scene.get("background", "#000000")
    if bg is not None:
        _parse_color(bg)
    cam = scene.get("camera", {})
    if not isinstance(cam, dict):
        raise SceneError("camera must be an object")
    if cam.get("zoom", 1.0) <= 0:
        raise SceneError("camera.zoom must be > 0")
    objects = scene.get("objects", [])
    if not isinstance(objects, list):
        raise SceneError("objects must be a list")
    ids: set[str] = set()
    for obj in objects:
        if not isinstance(obj, dict):
            raise SceneError("object must be an object")
        oid = check_id(obj.get("id", ""), "object id")
        if oid in ids:
            raise SceneError(f"duplicate object id: {oid}")
        if oid == "camera":
            raise SceneError("'camera' is a reserved id")
        ids.add(oid)
        otype = obj.get("type")
        if otype not in OBJECT_TYPES:
            raise SceneError(f"object {oid}: unknown type {otype!r}; allowed: {sorted(OBJECT_TYPES)}")
        for key in ("color", "fill", "stroke"):
            if key in obj and obj[key] is not None:
                _parse_color(obj[key])
        if otype == "vector" and not all(isinstance(v, (int, float)) for v in obj.get("values", [])):
            raise SceneError(f"object {oid}: vector values must be numbers")
        if otype == "matrix":
            rows = obj.get("rows", [])
            if rows and any(len(r) != len(rows[0]) for r in rows):
                raise SceneError(f"object {oid}: matrix rows must have equal length")
        if otype == "arc":
            for end in ("from", "to"):
                ref = obj.get(end)
                if not isinstance(ref, dict) or not (("ref" in ref) or ("x" in ref and "y" in ref)):
                    raise SceneError(f"object {oid}: arc.{end} must be {{'ref': id}} or {{'x','y'}}")
    for obj in objects:
        if obj.get("type") == "arc":
            for end in ("from", "to"):
                ref = obj[end].get("ref")
                if ref is not None and ref not in ids:
                    raise SceneError(f"object {obj['id']}: arc.{end} references unknown id {ref!r}")
    anims = scene.get("animations", [])
    if not isinstance(anims, list):
        raise SceneError("animations must be a list")
    aids: set[str] = set()
    for a in anims:
        if not isinstance(a, dict):
            raise SceneError("animation must be an object")
        aid = check_id(a.get("id", ""), "animation id")
        if aid in aids:
            raise SceneError(f"duplicate animation id: {aid}")
        aids.add(aid)
        tgt = a.get("target")
        if tgt != "camera" and tgt not in ids:
            raise SceneError(f"animation {aid}: unknown target {tgt!r}")
        if not isinstance(a.get("property"), str) or not a["property"]:
            raise SceneError(f"animation {aid}: property required")
        start, end = a.get("start"), a.get("end")
        if not isinstance(start, int) or not isinstance(end, int) or start < 0 or end < start:
            raise SceneError(f"animation {aid}: start/end must be integers with 0 <= start <= end")
        if end > dur:
            warnings.append(f"animation {aid} ends at {end} after scene duration {dur}")
        if a.get("easing", "linear") not in EASINGS:
            raise SceneError(f"animation {aid}: easing must be one of {EASINGS}")
        if "to" not in a:
            raise SceneError(f"animation {aid}: 'to' required")
    return warnings


def _parse_color(value: str) -> tuple[float, float, float, float]:
    if not isinstance(value, str) or not value.startswith("#") or len(value) not in (7, 9):
        raise SceneError(f"color must be '#rrggbb' or '#rrggbbaa', got {value!r}")
    try:
        r = int(value[1:3], 16) / 255
        g = int(value[3:5], 16) / 255
        b = int(value[5:7], 16) / 255
        a = int(value[7:9], 16) / 255 if len(value) == 9 else 1.0
    except ValueError as exc:
        raise SceneError(f"invalid color {value!r}") from exc
    return r, g, b, a


parse_color = _parse_color


def scene_hash(scene: dict) -> str:
    data = json.dumps(scene, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def _get_path(obj: dict, path: str) -> Any:
    cur: Any = obj
    for part in path.split("."):
        if isinstance(cur, list):
            cur = cur[int(part)]
        elif isinstance(cur, dict):
            cur = cur[part]
        else:
            raise SceneError(f"cannot resolve property path {path!r}")
    return cur


def _set_path(obj: dict, path: str, value: Any) -> None:
    parts = path.split(".")
    cur: Any = obj
    for part in parts[:-1]:
        if isinstance(cur, list):
            cur = cur[int(part)]
        else:
            if part not in cur:
                cur[part] = {}
            cur = cur[part]
    last = parts[-1]
    if isinstance(cur, list):
        cur[int(last)] = value
    else:
        cur[last] = value


def _interp(a: Any, b: Any, t: float) -> Any:
    if isinstance(a, bool) or isinstance(b, bool):
        return b if t >= 1.0 else a
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return a + (b - a) * t
    if isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        return [_interp(x, y, t) for x, y in zip(a, b, strict=True)]
    if isinstance(a, str) and isinstance(b, str) and a.startswith("#") and b.startswith("#"):
        ca, cb = _parse_color(a), _parse_color(b)
        mixed = [ca[i] + (cb[i] - ca[i]) * t for i in range(4)]
        return "#" + "".join(f"{int(round(max(0, min(1, c)) * 255)):02x}" for c in mixed)
    return b if t >= 1.0 else a


def animate(base: dict, animations: list[dict], frame: int) -> dict:
    """Return a copy of `base` (objects + camera) with animations applied at `frame`."""
    state = copy.deepcopy(base)
    for a in sorted(animations, key=lambda a: (a["start"], a["end"])):
        target = state["camera"] if a["target"] == "camera" else state["objects"][a["target"]]
        prop = a["property"]
        start, end = a["start"], a["end"]
        easing = a.get("easing", "linear")
        if frame < start:
            if "from" in a:
                _set_path(target, prop, a["from"])
            continue
        try:
            origin = a["from"] if "from" in a else _get_path(target, prop)
        except (KeyError, IndexError, SceneError):
            origin = a["to"]
        if frame >= end or end == start:
            _set_path(target, prop, a["to"])
            continue
        t = ease(easing, (frame - start) / (end - start))
        _set_path(target, prop, _interp(origin, a["to"], t))
    return state


def base_state(scene: dict) -> dict:
    objects: dict[str, dict] = {}
    for obj in scene.get("objects", []):
        merged = dict(COMMON_DEFAULTS)
        merged.update(OBJECT_TYPES[obj["type"]])
        merged.update(obj)
        objects[obj["id"]] = copy.deepcopy(merged)
    cam = {"x": scene.get("width", 1080) / 2, "y": scene.get("height", 1920) / 2, "zoom": 1.0}
    cam.update(scene.get("camera", {}))
    return {"objects": objects, "camera": cam, "order": [o["id"] for o in scene.get("objects", [])]}


def state_at(scene: dict, frame: int) -> dict:
    return animate(base_state(scene), scene.get("animations", []), frame)


def format_number(value: float, decimals: int, decimal_separator: str = ".") -> str:
    s = f"{value:.{decimals}f}"
    if decimal_separator != ".":
        s = s.replace(".", decimal_separator)
    return s
