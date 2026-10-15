"""Build MLT XML from a project and render it with `melt` (what Kdenlive uses too).

Shared with the Kdenlive exporter: producers, title XML and entry/filter
helpers live here; the Kdenlive-specific document skeleton lives in
`kdenlive.py`.
"""

from __future__ import annotations

import shutil
import xml.etree.ElementTree as ET
from pathlib import Path
from xml.sax.saxutils import escape

from .project import Project
from .timebase import mlt_out
from .tools import run

_TITLE_FONT_FALLBACK = "Liberation Sans"


def _prop(parent: ET.Element, name: str, value) -> ET.Element:
    el = ET.SubElement(parent, "property", name=name)
    el.text = "" if value is None else str(value)
    return el


def profile_element(parent: ET.Element, width: int, height: int, fps_num: int, fps_den: int, description: str = "explica") -> ET.Element:
    return ET.SubElement(
        parent,
        "profile",
        description=description,
        width=str(width),
        height=str(height),
        progressive="1",
        sample_aspect_num="1",
        sample_aspect_den="1",
        display_aspect_num=str(width),
        display_aspect_den=str(height),
        frame_rate_num=str(fps_num),
        frame_rate_den=str(fps_den),
        colorspace="709",
    )


def title_xmldata(project: Project, title: dict, duration_frames: int, caption_box: bool = False) -> str:
    """Native Kdenlive title document (editable in Kdenlive's title editor)."""
    w, h = project.width, project.height
    size = int(title.get("size", 64))
    color = title.get("color", "#ffffff").lstrip("#")
    r, g, b = int(color[0:2], 16), int(color[2:4], 16), int(color[4:6], 16)
    a = int(color[6:8], 16) if len(color) == 8 else 255
    weight = 700 if title.get("weight", "bold") == "bold" else 400
    align = {"left": 1, "center": 4, "right": 2}.get(title.get("align", "center"), 4)
    text = escape(str(title.get("text", "")))
    font = escape(str(title.get("font") or project.m.get("font") or _TITLE_FONT_FALLBACK))
    box_w = int(title.get("box_width", w * 0.84))
    x = int(title.get("x", w // 2)) - box_w // 2
    y = int(title.get("y", int(h * 0.12))) - int(size * 0.8)
    box_items = ""
    if caption_box or title.get("box"):
        pad = int(size * 0.5)
        lines = max(1, str(title.get("text", "")).count("\n") + 1)
        box_h = int(size * 1.35 * lines + pad)
        box_items = (
            f'<item type="QGraphicsRectItem" z-index="0"><position x="{x - pad // 2}" y="{y - pad // 2}">'
            f"<transform>1,0,0,0,1,0,0,0,1</transform></position>"
            f'<content rect="0,0,{box_w + pad},{box_h}" pen-color="0,0,0,0" brush-color="0,0,0,160" pen-width="0"/></item>'
        )
    return (
        f'<kdenlivetitle duration="{duration_frames}" LC_NUMERIC="C" width="{w}" height="{h}" out="{max(0, duration_frames - 1)}">'
        f"{box_items}"
        f'<item type="QGraphicsTextItem" z-index="1"><position x="{x}" y="{y}"><transform>1,0,0,0,1,0,0,0,1</transform></position>'
        f'<content font="{font}" font-pixel-size="{size}" font-color="{r},{g},{b},{a}" font-weight="{weight}" font-italic="0" '
        f'font-underline="0" alignment="{align}" line-spacing="0" letter-spacing="0" shadow="0;#64000000;3;3;3" '
        f'typewriter="0;2;1;0;0" box-outline="0" box-color="0,0,0,0" font-outline="0" font-outline-color="0,0,0,255" '
        f'tab-width="80" box-width="{box_w}">{text}</content></item>'
        f'<startviewport rect="0,0,{w},{h}"/><endviewport rect="0,0,{w},{h}"/><background color="0,0,0,0"/></kdenlivetitle>'
    )


def wrap_text(text: str, max_width: float, size: float, font: str, bold: bool = True) -> list[str]:
    """Word-wrap using cairo's text metrics (same font engine as the scene renderer)."""
    import cairo

    ctx = cairo.Context(cairo.ImageSurface(cairo.FORMAT_RGB24, 4, 4))
    ctx.select_font_face(font, cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD if bold else cairo.FONT_WEIGHT_NORMAL)
    ctx.set_font_size(size)
    lines: list[str] = []
    for para in str(text).split("\n"):
        cur = ""
        for word in para.split(" "):
            trial = (cur + " " + word).strip()
            if cur and ctx.text_extents(trial).x_advance > max_width:
                lines.append(cur)
                cur = word
            else:
                cur = trial
        lines.append(cur)
    return lines


def caption_title(project: Project, caption: dict) -> dict:
    box = project.caption_box()
    size = int(project.m.get("caption_size", 52))
    font = str(project.m.get("font") or _TITLE_FONT_FALLBACK)
    box_w = int(box["x1"] - box["x0"])
    lines = wrap_text(caption["text"], box_w - size, size, font)
    while len(lines) > 2 and size > 36:  # keep captions to two lines on mobile
        size -= 4
        lines = wrap_text(caption["text"], box_w - size, size, font)
    return {
        "text": "\n".join(lines),
        "x": project.width // 2,
        "y": int((box["y0"] + box["y1"]) / 2),
        "size": size,
        "color": "#ffffff",
        "weight": "bold",
        "align": "center",
        "box": True,
        "box_width": box_w,
    }


class MltBuilder:
    """Flat MLT document: one playlist per track inside a single tractor. Used for preview/master renders."""

    def __init__(self, project: Project, width: int, height: int, scene_clip_resolver, absolute: bool = True):
        self.p = project
        self.width, self.height = width, height
        self.resolve_scene = scene_clip_resolver
        self.absolute = absolute
        self.producers: dict[str, ET.Element] = {}

    def _res(self, path: Path) -> str:
        return str(path) if self.absolute else self.p.rel(path)

    def build(self, start: int = 0, end: int | None = None) -> ET.Element:
        p = self.p
        fps = p.fps
        total = p.duration_frames()
        end = total if end is None else end
        root = ET.Element("mlt", LC_NUMERIC="C", version="7.0.0", producer="main_tractor")
        if not self.absolute:
            root.set("root", str(p.root))
        profile_element(root, self.width, self.height, fps.num, fps.den, "explica preview" if (self.width, self.height) != (p.width, p.height) else "explica")
        playlists = []
        for t in p.m["tracks"]:
            pl = ET.Element("playlist", id=f"pl_{t['id']}")
            cursor = 0
            for c in sorted(t["clips"], key=lambda c: int(c["start"])):
                if not c.get("enabled", True):
                    continue
                cstart, cin, cout = int(c["start"]), int(c["in"]), int(c["out"])
                if cstart > cursor:
                    ET.SubElement(pl, "blank", length=str(cstart - cursor))
                prod_id = self._producer_for(root, c, t)
                entry = ET.SubElement(pl, "entry", producer=prod_id, **{"in": str(cin), "out": str(mlt_out(cin, cout - cin))})
                if t["kind"] == "audio" and float(c.get("gain_db", 0.0)) != 0.0:
                    f = ET.SubElement(entry, "filter", id=f"vol_{c['id']}")
                    _prop(f, "mlt_service", "volume")
                    _prop(f, "level", f"{float(c['gain_db']):.2f}")
                cursor = cstart + (cout - cin)
            if t["id"] == "V4":
                cursor = self._caption_entries(root, pl, cursor)
            playlists.append((t, pl))
        # keep playlists after producers in document order
        for _, pl in playlists:
            root.append(pl)
        tractor = ET.SubElement(root, "tractor", id="main_tractor", **{"in": "0", "out": str(max(0, total - 1))})
        multitrack = ET.SubElement(tractor, "multitrack")
        # video tracks bottom→top then audio; MLT composites b over a with transitions
        order = [t for t, _ in playlists]
        for t in order:
            ET.SubElement(multitrack, "track", producer=f"pl_{t['id']}")
        video_idx = [i for i, t in enumerate(order) if t["kind"] == "video"]
        audio_idx = [i for i, t in enumerate(order) if t["kind"] == "audio"]
        n = 0
        for i in video_idx[1:]:
            tr = ET.SubElement(tractor, "transition", id=f"tr{n}", **{"in": "0", "out": str(max(0, total - 1))})
            _prop(tr, "mlt_service", "qtblend")
            _prop(tr, "a_track", str(video_idx[0]))
            _prop(tr, "b_track", str(i))
            _prop(tr, "compositing", "0")
            _prop(tr, "always_active", "1")
            n += 1
        base_audio = audio_idx[0] if audio_idx else None
        for i in audio_idx[1:]:
            tr = ET.SubElement(tractor, "transition", id=f"tr{n}", **{"in": "0", "out": str(max(0, total - 1))})
            _prop(tr, "mlt_service", "mix")
            _prop(tr, "a_track", str(base_audio))
            _prop(tr, "b_track", str(i))
            _prop(tr, "sum", "1")
            _prop(tr, "always_active", "1")
            n += 1
        if base_audio is not None and video_idx:
            # route the mixed audio onto the video base so avformat gets both
            tr = ET.SubElement(tractor, "transition", id=f"tr{n}", **{"in": "0", "out": str(max(0, total - 1))})
            _prop(tr, "mlt_service", "mix")
            _prop(tr, "a_track", str(video_idx[0]))
            _prop(tr, "b_track", str(base_audio))
            _prop(tr, "sum", "1")
            _prop(tr, "always_active", "1")
        return root

    def _caption_entries(self, root: ET.Element, pl: ET.Element, cursor: int) -> int:
        for cap in sorted(self.p.m["captions"], key=lambda c: int(c["start"])):
            s, e = int(cap["start"]), int(cap["end"])
            if s < cursor:
                continue  # overlapping captions are rejected by validation; skip defensively
            if s > cursor:
                ET.SubElement(pl, "blank", length=str(s - cursor))
            pid = f"cap_{cap['id']}"
            if pid not in self.producers:
                prod = ET.Element("producer", id=pid, **{"in": "0", "out": str(mlt_out(0, e - s))})
                _prop(prod, "mlt_service", "kdenlivetitle")
                _prop(prod, "length", str(e - s))
                _prop(prod, "xmldata", title_xmldata(self.p, caption_title(self.p, cap), e - s, caption_box=True))
                _prop(prod, "explica:caption_id", cap["id"])
                root.append(prod)
                self.producers[pid] = prod
            ET.SubElement(pl, "entry", producer=pid, **{"in": "0", "out": str(mlt_out(0, e - s))})
            cursor = e
        return cursor

    def _producer_for(self, root: ET.Element, clip: dict, track: dict) -> str:
        src = clip["source"]
        kind, ref = src["kind"], src.get("ref")
        length = int(clip["out"]) - int(clip["in"])
        if kind == "scene":
            pid = f"scene_{ref}"
            if pid not in self.producers:
                path, sidecar = self.resolve_scene(ref)
                prod = ET.Element("producer", id=pid, **{"in": "0", "out": str(mlt_out(0, int(sidecar["frames"])))})
                _prop(prod, "resource", self._res(path))
                _prop(prod, "mlt_service", "avformat-novalidate")
                _prop(prod, "length", str(int(sidecar["frames"])))
                _prop(prod, "explica:scene_id", ref)
                _prop(prod, "explica:scene_hash", sidecar["scene_hash"])
                root.append(prod)
                self.producers[pid] = prod
            return pid
        if kind == "asset":
            pid = f"asset_{ref}"
            if pid not in self.producers:
                a = self.p.m["assets"][ref]
                frames = int(a.get("duration_frames") or length)
                prod = ET.Element("producer", id=pid, **{"in": "0", "out": str(mlt_out(0, frames))})
                _prop(prod, "resource", self._res(self.p.asset_path(ref)))
                _prop(prod, "mlt_service", "avformat-novalidate")
                _prop(prod, "length", str(frames))
                _prop(prod, "explica:asset_id", ref)
                root.append(prod)
                self.producers[pid] = prod
            return pid
        if kind == "title":
            pid = f"title_{ref}_{clip['id']}"
            if pid not in self.producers:
                title = self.p.m["titles"][ref]
                prod = ET.Element("producer", id=pid, **{"in": "0", "out": str(mlt_out(0, length))})
                _prop(prod, "mlt_service", "kdenlivetitle")
                _prop(prod, "length", str(length))
                _prop(prod, "xmldata", title_xmldata(self.p, title, length))
                _prop(prod, "explica:title_id", ref)
                root.append(prod)
                self.producers[pid] = prod
            return pid
        if kind == "color":
            color = str(clip.get("color", "#000000")).lstrip("#")
            pid = f"color_{color}_{length}"
            if pid not in self.producers:
                prod = ET.Element("producer", id=pid, **{"in": "0", "out": str(mlt_out(0, length))})
                _prop(prod, "mlt_service", "color")
                _prop(prod, "resource", f"0x{color}ff" if len(color) == 6 else f"0x{color}")
                _prop(prod, "length", str(length))
                _prop(prod, "mlt_image_format", "rgba")
                root.append(prod)
                self.producers[pid] = prod
            return pid
        raise ValueError(f"unsupported clip source kind {kind!r}")


def write_xml(root: ET.Element, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    ET.indent(root, space=" ")
    data = ET.tostring(root, encoding="unicode")
    path.write_text('<?xml version="1.0" encoding="utf-8"?>\n' + data + "\n", encoding="utf-8")
    return path


def melt_argv_render(xml_path: Path, out_path: Path, start: int, end: int, quality: str = "preview", audio: bool = True) -> list[str]:
    melt = shutil.which("melt")
    if not melt:
        raise RuntimeError("melt not found")
    consumer = [f"avformat:{out_path}"]
    if out_path.suffix.lower() == ".png":
        return [melt, str(xml_path), f"in={start}", f"out={start}", "-consumer", consumer[0]]
    if quality == "master":
        consumer += ["vcodec=libx264", "preset=medium", "crf=17", "pix_fmt=yuv420p", "acodec=aac", "ab=192k", "ar=48000", "movflags=+faststart"]
    else:
        consumer += ["vcodec=libx264", "preset=veryfast", "crf=23", "pix_fmt=yuv420p", "acodec=aac", "ab=128k", "ar=48000", "movflags=+faststart"]
    if not audio:
        consumer.append("an=1")
    return [melt, str(xml_path), f"in={start}", f"out={max(start, end - 1)}", "-progress2", "-consumer", *consumer]


def melt_frame(xml_path: Path, frame: int, out_png: Path) -> Path:
    out_png.parent.mkdir(parents=True, exist_ok=True)
    run(melt_argv_render(xml_path, out_png, frame, frame + 1))
    return out_png
