"""Rasterize scene states with pycairo and encode with FFmpeg.

Output formats:
- opaque scenes  → H.264 (yuv420p) .mp4, fast and small; used for V2 diagram clips.
- transparent scenes (background null) → QuickTime RLE (argb) .mov with
  straight alpha (FFmpeg `unpremultiply` after cairo's premultiplied output),
  verified to composite in MLT via `qtblend`.
"""

from __future__ import annotations

import json
import math
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import cairo

from ..timebase import Fps
from ..tools import clean_env
from . import formula as fml
from .model import SceneError, base_state, format_number, parse_color, scene_hash, state_at, validate_scene


@dataclass
class BBox:
    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2

    @property
    def cy(self) -> float:
        return (self.y0 + self.y1) / 2

    @property
    def w(self) -> float:
        return self.x1 - self.x0

    @property
    def h(self) -> float:
        return self.y1 - self.y0

    def to_dict(self) -> dict:
        return {"x0": round(self.x0, 1), "y0": round(self.y0, 1), "x1": round(self.x1, 1), "y1": round(self.y1, 1)}

    def intersects(self, other: "BBox") -> bool:
        return not (self.x1 <= other.x0 or other.x1 <= self.x0 or self.y1 <= other.y0 or other.y1 <= self.y0)


class SceneRenderer:
    def __init__(self, scene: dict, width: int | None = None, height: int | None = None, fps: Fps | None = None):
        validate_scene(scene)
        self.scene = scene
        self.logical_w = int(scene.get("width", 1080))
        self.logical_h = int(scene.get("height", 1920))
        self.out_w = int(width or self.logical_w)
        self.out_h = int(height or self.logical_h)
        if self.out_w % 2 or self.out_h % 2:
            raise SceneError("output width/height must be even")
        self.fps = fps or Fps(30, 1)
        self.font = scene.get("font", "Liberation Sans")
        self.decimal_separator = scene.get("decimal_separator", ".")
        self.transparent = scene.get("background", "#000000") is None
        fmt = cairo.FORMAT_ARGB32 if self.transparent else cairo.FORMAT_RGB24
        self.surface = cairo.ImageSurface(fmt, self.out_w, self.out_h)
        self.scale = self.out_w / self.logical_w
        self._measure_ctx = cairo.Context(cairo.ImageSurface(cairo.FORMAT_RGB24, 4, 4))

    # ------------------------------------------------------------------
    def draw_frame(self, frame: int) -> cairo.ImageSurface:
        state = state_at(self.scene, frame)
        ctx = cairo.Context(self.surface)
        ctx.set_operator(cairo.OPERATOR_SOURCE)
        if self.transparent:
            ctx.set_source_rgba(0, 0, 0, 0)
        else:
            r, g, b, _ = parse_color(self.scene.get("background", "#000000"))
            ctx.set_source_rgb(r, g, b)
        ctx.paint()
        ctx.set_operator(cairo.OPERATOR_OVER)
        self._apply_camera(ctx, state["camera"])
        for oid in state["order"]:
            obj = state["objects"][oid]
            if not obj.get("visible", True) or obj.get("opacity", 1) <= 0:
                continue
            self._draw_object(ctx, obj, state)
        self.surface.flush()
        return self.surface

    def _apply_camera(self, ctx: cairo.Context, cam: dict) -> None:
        zoom = float(cam.get("zoom", 1.0))
        ctx.translate(self.out_w / 2, self.out_h / 2)
        ctx.scale(self.scale * zoom, self.scale * zoom)
        ctx.translate(-float(cam["x"]), -float(cam["y"]))

    # ------------------------------------------------------------------
    def bounds(self, frame: int) -> dict[str, dict]:
        """Logical-coordinate bounding boxes of all visible objects at `frame` (evidence for agents)."""
        state = state_at(self.scene, frame)
        ctx = self._measure_ctx
        out: dict[str, dict] = {}
        for oid in state["order"]:
            obj = state["objects"][oid]
            bb = self._bbox(ctx, obj, state)
            if bb is not None:
                out[oid] = {"type": obj["type"], "visible": bool(obj.get("visible", True)) and obj.get("opacity", 1) > 0, "opacity": round(float(obj.get("opacity", 1)), 3), "bbox": bb.to_dict()}
        out["camera"] = {"type": "camera", **{k: round(float(v), 3) for k, v in state["camera"].items()}}
        return out

    def safe_area_report(self, frame: int, margin_ratio: float = 0.10) -> list[str]:
        """Objects crossing the mobile safe margin (default 10%)."""
        warnings = []
        mx, my = self.logical_w * margin_ratio, self.logical_h * margin_ratio
        for oid, info in self.bounds(frame).items():
            if oid == "camera" or not info["visible"]:
                continue
            b = info["bbox"]
            if b["x0"] < mx or b["x1"] > self.logical_w - mx or b["y0"] < my or b["y1"] > self.logical_h - my:
                warnings.append(f"{oid} crosses the {int(margin_ratio * 100)}% safe margin at frame {frame}")
        return warnings

    # ------------------------------------------------------------------
    def _color(self, ctx: cairo.Context, color: str | None, opacity: float) -> bool:
        if color is None:
            return False
        r, g, b, a = parse_color(color)
        ctx.set_source_rgba(r, g, b, a * opacity)
        return True

    def _font(self, ctx: cairo.Context, size: float, weight: str = "normal", italic: bool = False) -> None:
        ctx.select_font_face(
            self.font,
            cairo.FONT_SLANT_ITALIC if italic else cairo.FONT_SLANT_NORMAL,
            cairo.FONT_WEIGHT_BOLD if weight == "bold" else cairo.FONT_WEIGHT_NORMAL,
        )
        ctx.set_font_size(size)

    def _fmt(self, value: float, decimals: int) -> str:
        return format_number(float(value), int(decimals), self.decimal_separator)

    def _wrap(self, ctx: cairo.Context, text: str, max_width: float | None) -> list[str]:
        lines: list[str] = []
        for para in str(text).split("\n"):
            if not max_width:
                lines.append(para)
                continue
            words = para.split(" ")
            cur = ""
            for w in words:
                trial = (cur + " " + w).strip()
                if cur and ctx.text_extents(trial).x_advance > max_width:
                    lines.append(cur)
                    cur = w
                else:
                    cur = trial
            lines.append(cur)
        return lines

    def _text_lines_bbox(self, ctx, obj, lines, size, line_height) -> BBox:
        widths = [ctx.text_extents(ln).x_advance for ln in lines]
        w = max(widths) if widths else 0
        total_h = size * line_height * len(lines)
        align = obj.get("align", "center")
        x = float(obj["x"])
        x0 = x - w / 2 if align == "center" else (x - w if align == "right" else x)
        y0 = float(obj["y"]) - total_h / 2
        return BBox(x0, y0, x0 + w, y0 + total_h)

    # ------------------------------------------------------------------
    def _resolve_point(self, ctx, ref: dict, state: dict, anchor: str) -> tuple[float, float]:
        if "ref" in ref:
            target = state["objects"][ref["ref"]]
            bb = self._bbox(ctx, target, state) or BBox(target["x"], target["y"], target["x"], target["y"])
            dx, dy = float(ref.get("dx", 0)), float(ref.get("dy", 0))
            if anchor == "top":
                return bb.cx + dx, bb.y0 + dy
            if anchor == "bottom":
                return bb.cx + dx, bb.y1 + dy
            return bb.cx + dx, bb.cy + dy
        return float(ref["x"]), float(ref["y"])

    def _bbox(self, ctx: cairo.Context, obj: dict, state: dict) -> BBox | None:
        t = obj["type"]
        x, y = float(obj["x"]), float(obj["y"])
        sc = float(obj.get("scale", 1.0))
        if t == "text":
            size = float(obj["size"]) * sc
            self._font(ctx, size, obj.get("weight", "normal"), obj.get("italic", False))
            lines = self._wrap(ctx, obj["text"], obj.get("max_width"))
            return self._text_lines_bbox(ctx, obj, lines, size, float(obj.get("line_height", 1.25)))
        if t == "formula":
            size = float(obj["size"]) * sc
            nodes = fml.parse(str(obj["text"]))
            w, a, d = fml.layout(ctx, nodes, size, self.font, obj.get("weight") == "bold")
            align = obj.get("align", "center")
            x0 = x - w / 2 if align == "center" else (x - w if align == "right" else x)
            return BBox(x0, y - a, x0 + w, y + d)
        if t == "number":
            size = float(obj["size"]) * sc
            self._font(ctx, size, obj.get("weight", "normal"))
            text = f"{obj.get('prefix', '')}{self._fmt(obj['value'], obj['decimals'])}{obj.get('suffix', '')}"
            return self._text_lines_bbox(ctx, obj, [text], size, 1.0)
        if t == "token":
            size = float(obj["size"]) * sc
            self._font(ctx, size, "normal")
            ext = ctx.text_extents(str(obj["text"]))
            w = ext.x_advance + 2 * float(obj["pad_x"]) * sc
            h = size * 1.1 + 2 * float(obj["pad_y"]) * sc
            return BBox(x - w / 2, y - h / 2, x + w / 2, y + h / 2)
        if t == "vector":
            size = float(obj["size"]) * sc
            n = len(obj["values"])
            row_h = size * 1.3
            self._font(ctx, size)
            w = max([ctx.text_extents(self._fmt(v, obj["decimals"])).x_advance for v in obj["values"]] + [size]) + size * 0.9
            label_h = float(obj.get("label_size", 36)) * 1.4 if obj.get("label") else 0
            h = n * row_h + float(obj["gap"]) * 2
            return BBox(x - w / 2, y - label_h, x + w / 2, y + h)
        if t == "matrix":
            rows = obj["rows"]
            nr = len(rows)
            nc = len(rows[0]) if rows else 0
            cw, ch = float(obj["cell_w"]) * sc, float(obj["cell_h"]) * sc
            left = x - nc * cw / 2
            top = y - nr * ch / 2
            lab = float(obj.get("label_size", 34)) * 1.5
            x0 = left - (lab * 2.2 if obj.get("row_labels") else 0)
            y0 = top - (lab if obj.get("col_labels") else 0)
            return BBox(x0, y0, left + nc * cw, top + nr * ch)
        if t == "circle":
            r = float(obj["r"]) * sc
            return BBox(x - r, y - r, x + r, y + r)
        if t == "rect":
            w, h = float(obj["w"]) * sc, float(obj["h"]) * sc
            return BBox(x - w / 2, y - h / 2, x + w / 2, y + h / 2)
        if t in ("line", "arrow"):
            x2, y2 = float(obj["x2"]), float(obj["y2"])
            return BBox(min(x, x2), min(y, y2), max(x, x2), max(y, y2))
        if t == "arc":
            p0 = self._resolve_point(ctx, obj["from"], state, obj.get("anchor", "top"))
            p1 = self._resolve_point(ctx, obj["to"], state, obj.get("anchor", "top"))
            lift = float(obj["lift"])
            ymin = min(p0[1], p1[1]) - max(lift, 0)
            ymax = max(p0[1], p1[1]) - min(lift, 0)
            return BBox(min(p0[0], p1[0]), ymin, max(p0[0], p1[0]), ymax)
        if t == "bar":
            w, hmax = float(obj["w"]) * sc, float(obj["h_max"]) * sc
            lab = float(obj.get("size", 36)) * 1.4
            return BBox(x - w / 2, y - hmax - lab, x + w / 2, y + (lab if obj.get("label") else 0))
        return None

    # ------------------------------------------------------------------
    def _draw_object(self, ctx: cairo.Context, obj: dict, state: dict) -> None:
        t = obj["type"]
        op = float(obj.get("opacity", 1.0))
        x, y = float(obj["x"]), float(obj["y"])
        sc = float(obj.get("scale", 1.0))
        rot = float(obj.get("rotation", 0.0))
        ctx.save()
        if rot:
            ctx.translate(x, y)
            ctx.rotate(math.radians(rot))
            ctx.translate(-x, -y)
        getattr(self, f"_draw_{t}")(ctx, obj, state, x, y, sc, op)
        ctx.restore()

    def _draw_text(self, ctx, obj, state, x, y, sc, op):
        size = float(obj["size"]) * sc
        self._font(ctx, size, obj.get("weight", "normal"), obj.get("italic", False))
        self._color(ctx, obj["color"], op)
        lines = self._wrap(ctx, obj["text"], obj.get("max_width"))
        lh = size * float(obj.get("line_height", 1.25))
        total_h = lh * len(lines)
        align = obj.get("align", "center")
        for i, ln in enumerate(lines):
            ext = ctx.text_extents(ln)
            baseline = y - total_h / 2 + lh * i + lh * 0.5 + size * 0.35
            if align == "center":
                px = x - ext.x_advance / 2
            elif align == "right":
                px = x - ext.x_advance
            else:
                px = x
            ctx.move_to(px, baseline)
            ctx.show_text(ln)

    def _draw_formula(self, ctx, obj, state, x, y, sc, op):
        size = float(obj["size"]) * sc
        self._color(ctx, obj["color"], op)
        nodes = fml.parse(str(obj["text"]))
        w, a, d = fml.layout(ctx, nodes, size, self.font, obj.get("weight") == "bold")
        align = obj.get("align", "center")
        x0 = x - w / 2 if align == "center" else (x - w if align == "right" else x)
        fml.draw(ctx, nodes, x0, y, size, self.font, obj.get("weight") == "bold")

    def _draw_number(self, ctx, obj, state, x, y, sc, op):
        o = dict(obj)
        o["text"] = f"{obj.get('prefix', '')}{self._fmt(obj['value'], obj['decimals'])}{obj.get('suffix', '')}"
        o["max_width"] = None
        o["line_height"] = 1.0
        self._draw_text(ctx, o, state, x, y, sc, op)

    def _rounded_rect(self, ctx, x0, y0, w, h, r):
        r = max(0.0, min(r, w / 2, h / 2))
        ctx.new_sub_path()
        ctx.arc(x0 + w - r, y0 + r, r, -math.pi / 2, 0)
        ctx.arc(x0 + w - r, y0 + h - r, r, 0, math.pi / 2)
        ctx.arc(x0 + r, y0 + h - r, r, math.pi / 2, math.pi)
        ctx.arc(x0 + r, y0 + r, r, math.pi, 3 * math.pi / 2)
        ctx.close_path()

    def _draw_token(self, ctx, obj, state, x, y, sc, op):
        size = float(obj["size"]) * sc
        self._font(ctx, size, "normal")
        ext = ctx.text_extents(str(obj["text"]))
        w = ext.x_advance + 2 * float(obj["pad_x"]) * sc
        h = size * 1.1 + 2 * float(obj["pad_y"]) * sc
        glow = float(obj.get("glow", 0.0))
        if glow > 0:
            for i in range(4, 0, -1):
                self._rounded_rect(ctx, x - w / 2 - i * 4, y - h / 2 - i * 4, w + i * 8, h + i * 8, float(obj["radius"]) + i * 4)
                self._color(ctx, obj["stroke"], op * glow * 0.12)
                ctx.set_line_width(4)
                ctx.stroke()
        self._rounded_rect(ctx, x - w / 2, y - h / 2, w, h, float(obj["radius"]) * sc)
        if self._color(ctx, obj.get("fill"), op):
            ctx.fill_preserve()
        if self._color(ctx, obj.get("stroke"), op):
            ctx.set_line_width(float(obj["stroke_width"]) * (1 + glow))
            ctx.stroke()
        else:
            ctx.new_path()
        self._color(ctx, obj["color"], op)
        ctx.move_to(x - ext.x_advance / 2, y + size * 0.36)
        ctx.show_text(str(obj["text"]))

    def _draw_vector(self, ctx, obj, state, x, y, sc, op):
        size = float(obj["size"]) * sc
        values = obj["values"]
        n = len(values)
        row_h = size * 1.3
        self._font(ctx, size)
        texts = [self._fmt(v, obj["decimals"]) for v in values]
        inner_w = max([ctx.text_extents(t).x_advance for t in texts] + [size * 0.5])
        gap = float(obj["gap"])
        h = n * row_h + gap * 2
        self._color(ctx, obj["color"], op)
        if obj.get("label"):
            self._font(ctx, float(obj.get("label_size", 36)), "bold")
            lab = str(obj["label"])
            ext = ctx.text_extents(lab)
            ctx.move_to(x - ext.x_advance / 2, y - float(obj.get("label_size", 36)) * 0.5)
            ctx.show_text(lab)
        self._font(ctx, size)
        for i, t in enumerate(texts):
            ext = ctx.text_extents(t)
            ctx.move_to(x - ext.x_advance / 2, y + gap + row_h * i + row_h * 0.5 + size * 0.35)
            ctx.show_text(t)
        if obj.get("bracket", True):
            bw = size * 0.35
            left = x - inner_w / 2 - bw
            right = x + inner_w / 2 + bw
            ctx.set_line_width(max(2.0, size * 0.06))
            ctx.move_to(left + bw * 0.5, y)
            ctx.line_to(left, y)
            ctx.line_to(left, y + h)
            ctx.line_to(left + bw * 0.5, y + h)
            ctx.stroke()
            ctx.move_to(right - bw * 0.5, y)
            ctx.line_to(right, y)
            ctx.line_to(right, y + h)
            ctx.line_to(right - bw * 0.5, y + h)
            ctx.stroke()

    def _draw_matrix(self, ctx, obj, state, x, y, sc, op):
        rows = obj["rows"]
        nr = len(rows)
        nc = len(rows[0]) if rows else 0
        cw, ch = float(obj["cell_w"]) * sc, float(obj["cell_h"]) * sc
        left = x - nc * cw / 2
        top = y - nr * ch / 2
        size = float(obj["size"]) * sc
        self._color(ctx, obj["color"], op)
        self._font(ctx, size)
        for i, row in enumerate(rows):
            for j, v in enumerate(row):
                t = self._fmt(v, obj["decimals"]) if isinstance(v, (int, float)) else str(v)
                ext = ctx.text_extents(t)
                ctx.move_to(left + cw * j + cw / 2 - ext.x_advance / 2, top + ch * i + ch / 2 + size * 0.35)
                ctx.show_text(t)
        ctx.set_line_width(max(2.0, size * 0.06))
        bw = size * 0.35
        for sx, d in ((left - bw * 0.4, 1), (left + nc * cw + bw * 0.4, -1)):
            ctx.move_to(sx + d * bw * 0.5, top)
            ctx.line_to(sx, top)
            ctx.line_to(sx, top + nr * ch)
            ctx.line_to(sx + d * bw * 0.5, top + nr * ch)
            ctx.stroke()
        lab_size = float(obj.get("label_size", 34))
        self._font(ctx, lab_size, "bold")
        for i, lab in enumerate(obj.get("row_labels", [])[:nr]):
            ext = ctx.text_extents(str(lab))
            ctx.move_to(left - bw - ext.x_advance - lab_size * 0.6, top + ch * i + ch / 2 + lab_size * 0.35)
            ctx.show_text(str(lab))
        for j, lab in enumerate(obj.get("col_labels", [])[:nc]):
            ext = ctx.text_extents(str(lab))
            ctx.move_to(left + cw * j + cw / 2 - ext.x_advance / 2, top - lab_size * 0.6)
            ctx.show_text(str(lab))

    def _draw_circle(self, ctx, obj, state, x, y, sc, op):
        ctx.new_sub_path()
        ctx.arc(x, y, float(obj["r"]) * sc, 0, 2 * math.pi)
        if self._color(ctx, obj.get("fill"), op):
            ctx.fill_preserve()
        if self._color(ctx, obj.get("stroke"), op):
            ctx.set_line_width(float(obj["stroke_width"]))
            ctx.stroke()
        else:
            ctx.new_path()

    def _draw_rect(self, ctx, obj, state, x, y, sc, op):
        w, h = float(obj["w"]) * sc, float(obj["h"]) * sc
        self._rounded_rect(ctx, x - w / 2, y - h / 2, w, h, float(obj.get("rx", 0)))
        if self._color(ctx, obj.get("fill"), op):
            ctx.fill_preserve()
        if self._color(ctx, obj.get("stroke"), op):
            ctx.set_line_width(float(obj["stroke_width"]))
            ctx.stroke()
        else:
            ctx.new_path()

    def _draw_line(self, ctx, obj, state, x, y, sc, op, head: float = 0.0):
        p = max(0.0, min(1.0, float(obj.get("progress", 1.0))))
        if p <= 0:
            return
        x2 = x + (float(obj["x2"]) - x) * p
        y2 = y + (float(obj["y2"]) - y) * p
        self._color(ctx, obj["color"], op)
        ctx.set_line_width(float(obj["width"]))
        if obj.get("dash"):
            ctx.set_dash([float(d) for d in obj["dash"]])
        ctx.move_to(x, y)
        ctx.line_to(x2, y2)
        ctx.stroke()
        if head > 0:
            ang = math.atan2(y2 - y, x2 - x)
            ctx.move_to(x2, y2)
            ctx.line_to(x2 - head * math.cos(ang - 0.45), y2 - head * math.sin(ang - 0.45))
            ctx.line_to(x2 - head * math.cos(ang + 0.45), y2 - head * math.sin(ang + 0.45))
            ctx.close_path()
            ctx.fill()

    def _draw_arrow(self, ctx, obj, state, x, y, sc, op):
        self._draw_line(ctx, obj, state, x, y, sc, op, head=float(obj.get("head", 18)))

    def _draw_arc(self, ctx, obj, state, x, y, sc, op):
        p = max(0.0, min(1.0, float(obj.get("progress", 1.0))))
        if p <= 0:
            return
        anchor = obj.get("anchor", "top")
        p0 = self._resolve_point(ctx, obj["from"], state, anchor)
        p1 = self._resolve_point(ctx, obj["to"], state, anchor)
        lift = float(obj["lift"])
        cx, cy = (p0[0] + p1[0]) / 2, (p0[1] + p1[1]) / 2 - lift
        weight = max(0.0, min(1.0, float(obj.get("weight", 1.0))))
        width = float(obj["min_width"]) + (float(obj["max_width"]) - float(obj["min_width"])) * weight
        self._color(ctx, obj["color"], op * (0.25 + 0.75 * weight))
        ctx.set_line_width(width)
        ctx.set_line_cap(cairo.LINE_CAP_ROUND)
        # quadratic bezier sampled so that `progress` draws it on
        steps = 48
        ctx.move_to(*p0)
        for i in range(1, int(steps * p) + 1):
            t = i / steps
            bx = (1 - t) ** 2 * p0[0] + 2 * (1 - t) * t * cx + t * t * p1[0]
            by = (1 - t) ** 2 * p0[1] + 2 * (1 - t) * t * cy + t * t * p1[1]
            ctx.line_to(bx, by)
        ctx.stroke()

    def _draw_bar(self, ctx, obj, state, x, y, sc, op):
        w, hmax = float(obj["w"]) * sc, float(obj["h_max"]) * sc
        v = max(0.0, min(1.0, float(obj["value"])))
        h = hmax * v
        if self._color(ctx, obj.get("fill"), op):
            ctx.rectangle(x - w / 2, y - h, w, h)
            ctx.fill()
        size = float(obj.get("size", 36))
        self._font(ctx, size)
        self._color(ctx, obj["color"], op)
        if obj.get("show_value", True):
            t = self._fmt(obj["value"], obj["decimals"])
            ext = ctx.text_extents(t)
            ctx.move_to(x - ext.x_advance / 2, y - h - size * 0.4)
            ctx.show_text(t)
        if obj.get("label"):
            lab = str(obj["label"])
            ext = ctx.text_extents(lab)
            ctx.move_to(x - ext.x_advance / 2, y + size * 1.1)
            ctx.show_text(lab)


# ----------------------------------------------------------------------
def render_params_hash(scene: dict, width: int, height: int, fps: Fps) -> str:
    import hashlib

    key = f"{scene_hash(scene)}|{width}x{height}|{fps.num}/{fps.den}|codec-v2"
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def render_scene(
    scene: dict,
    out_path: Path,
    width: int,
    height: int,
    fps: Fps,
    ffmpeg_path: str | None = None,
    progress_cb=None,
    frame_range: tuple[int, int] | None = None,
) -> dict:
    """Render all frames of `scene` to `out_path` (.mp4 opaque / .mov alpha). Returns a sidecar dict."""
    ffmpeg_bin = ffmpeg_path or shutil.which("ffmpeg")
    if not ffmpeg_bin:
        raise RuntimeError("ffmpeg not found")
    renderer = SceneRenderer(scene, width, height, fps)
    duration = int(scene["duration_frames"])
    start, end = frame_range or (0, duration)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_name(out_path.name + ".part" + out_path.suffix)
    rate = f"{fps.num}/{fps.den}"
    if renderer.transparent:
        argv = [
            ffmpeg_bin, "-v", "error", "-y",
            "-f", "rawvideo", "-pix_fmt", "bgra", "-s", f"{width}x{height}", "-r", rate, "-i", "-",
            "-vf", "unpremultiply=inplace=1",
            "-c:v", "qtrle", "-pix_fmt", "argb", "-r", rate, str(tmp),
        ]
    else:
        argv = [
            ffmpeg_bin, "-v", "error", "-y",
            "-f", "rawvideo", "-pix_fmt", "bgr0", "-s", f"{width}x{height}", "-r", rate, "-i", "-",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p",
            "-g", "30", "-movflags", "+faststart", "-r", rate, str(tmp),
        ]
    proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stderr=subprocess.PIPE, env=clean_env())
    assert proc.stdin is not None
    try:
        for f in range(start, end):
            surf = renderer.draw_frame(f)
            proc.stdin.write(surf.get_data())
            if progress_cb and (f % 15 == 0):
                progress_cb(f - start + 1, end - start)
        proc.stdin.close()
        err = proc.stderr.read().decode("utf-8", "replace") if proc.stderr else ""
        code = proc.wait()
    except BrokenPipeError:
        err = proc.stderr.read().decode("utf-8", "replace") if proc.stderr else ""
        code = proc.wait()
    if code != 0:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"ffmpeg failed ({code}): {err[-800:]}")
    tmp.replace(out_path)
    sidecar = {
        "scene_id": scene["id"],
        "scene_hash": scene_hash(scene),
        "width": width,
        "height": height,
        "fps": fps.to_dict(),
        "frames": end - start,
        "duration_frames": duration,
        "transparent": renderer.transparent,
        "path": str(out_path),
        "object_ids": [o["id"] for o in scene.get("objects", [])],
    }
    Path(str(out_path) + ".json").write_text(json.dumps(sidecar, indent=2, ensure_ascii=False))
    return sidecar


def render_frame_png(scene: dict, frame: int, out_png: Path, width: int, height: int, fps: Fps | None = None) -> Path:
    renderer = SceneRenderer(scene, width, height, fps)
    surf = renderer.draw_frame(frame)
    out_png = Path(out_png)
    out_png.parent.mkdir(parents=True, exist_ok=True)
    surf.write_to_png(str(out_png))
    return out_png


def scene_bounds(scene: dict, frame: int) -> dict:
    return SceneRenderer(scene).bounds(frame)


__all__ = ["SceneRenderer", "render_scene", "render_frame_png", "scene_bounds", "render_params_hash", "base_state"]
