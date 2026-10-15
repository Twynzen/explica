"""Native Kdenlive project export and read-back (document version 1.1, Kdenlive ≥ 23.04).

Export writes a `.kdenlive` whose structure mirrors files produced by Kdenlive
itself (sequence tractor with uuid, per-track tractors with two playlists,
`main_bin` with docproperties, final project tractor). Every producer carries
`explica:*` properties so clips can be matched when the file comes back from a
GUI session (`sync_from_kdenlive`).

What round-trips (tested headlessly with melt + XML re-import):
  clip positions / in / out on every track, audio gain (volume filter level),
  native title text (kdenlivetitle xmldata), caption title clips, guides.
What is *not* modelled and therefore preserved only inside Kdenlive's file:
  effects, keyframes, transitions between clips, mixes — they are reported as
  `unmodelled` on sync so nothing is silently dropped when re-exporting (the
  exporter refuses to overwrite a modified .kdenlive without a sync first).
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path
from xml.sax.saxutils import unescape

from .mlt import _prop, caption_title, profile_element, title_xmldata
from .project import OpResult, Project, ProjectError, _json_diff, sha256_file
from .timebase import length_from_mlt, mlt_out, parse_mlt_time

KDENLIVE_DOC_VERSION = "1.1"
KDENLIVE_VERSION = "26.08.0"


def kdenlive_file_hash(path: Path) -> str:
    """Kdenlive's clip hash: md5 of the whole file (≤2 MB) or of first+last MB."""
    size = path.stat().st_size
    with open(path, "rb") as fh:
        if size > 2_000_000:
            data = fh.read(1_000_000)
            fh.seek(size - 1_000_000)
            data += fh.read()
        else:
            data = fh.read()
    return hashlib.md5(data).hexdigest()


def _clock(frames: int, fps_num: int, fps_den: int) -> str:
    total_ms = round(frames * 1000 * fps_den / fps_num)
    h, rem = divmod(total_ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"


class KdenliveExporter:
    def __init__(self, project: Project, scene_clip_resolver):
        self.p = project
        self.resolve_scene = scene_clip_resolver
        self.next_id = 10
        self.bin_entries: list[tuple[str, int]] = []
        self.producers: dict[str, tuple[str, int, ET.Element]] = {}

    def _kid(self) -> int:
        self.next_id += 1
        return self.next_id

    def build(self) -> ET.Element:
        p = self.p
        fps = p.fps
        total = max(1, p.duration_frames())
        seq_uuid = "{" + str(uuid.uuid4()) + "}"
        root = ET.Element("mlt", LC_NUMERIC="C", producer="main_bin", version="7.40.0", root=str(p.root))
        profile_element(root, p.width, p.height, fps.num, fps.den, f"explica {p.width}x{p.height} {fps.value:g} fps")
        # --- bin producers --------------------------------------------------
        prod_elems: list[ET.Element] = []
        clip_producer: dict[str, str] = {}
        for t in p.m["tracks"]:
            for c in t["clips"]:
                clip_producer[c["id"]] = self._producer(c, prod_elems)
        caption_producers: dict[str, str] = {}
        for cap in p.m["captions"]:
            caption_producers[cap["id"]] = self._caption_producer(cap, prod_elems)
        for el in prod_elems:
            root.append(el)
        # --- black track ---------------------------------------------------------
        black = ET.SubElement(root, "producer", id="black_track", **{"in": "0", "out": str(mlt_out(0, total + 1000))})
        _prop(black, "length", str(total + 1001))
        _prop(black, "eof", "continue")
        _prop(black, "resource", "black")
        _prop(black, "aspect_ratio", "1")
        _prop(black, "mlt_service", "color")
        _prop(black, "kdenlive:playlistid", "black_track")
        _prop(black, "mlt_image_format", "rgba")
        _prop(black, "set.test_audio", "0")
        # --- tracks: Kdenlive stacks audio tracks first (bottom), then video ---
        ordered = [t for t in reversed(p.m["tracks"]) if t["kind"] == "audio"] + [t for t in p.m["tracks"] if t["kind"] == "video"]
        track_tractors = []
        n = 0
        for t in ordered:
            pl_main = ET.SubElement(root, "playlist", id=f"playlist{n}")
            pl_mix = ET.SubElement(root, "playlist", id=f"playlist{n + 1}")
            if t["kind"] == "audio":
                _prop(pl_main, "kdenlive:audio_track", "1")
                _prop(pl_mix, "kdenlive:audio_track", "1")
            cursor = 0
            for c in sorted(t["clips"], key=lambda c: int(c["start"])):
                cstart, cin, cout = int(c["start"]), int(c["in"]), int(c["out"])
                if cstart > cursor:
                    ET.SubElement(pl_main, "blank", length=str(cstart - cursor))
                pid = clip_producer[c["id"]]
                entry = ET.SubElement(pl_main, "entry", producer=pid, **{"in": str(cin), "out": str(mlt_out(cin, cout - cin))})
                _prop(entry, "kdenlive:id", str(self.producers[pid][1]))
                _prop(entry, "explica:clip_id", c["id"])
                if t["kind"] == "audio" and float(c.get("gain_db", 0.0)) != 0.0:
                    f = ET.SubElement(entry, "filter", id=f"filter_vol_{c['id']}")
                    _prop(f, "mlt_service", "volume")
                    _prop(f, "kdenlive_id", "volume")
                    _prop(f, "level", f"{float(c['gain_db']):.2f}")
                cursor = cstart + (cout - cin)
            if t["id"] == "V4":
                for cap in sorted(p.m["captions"], key=lambda c: int(c["start"])):
                    s, e = int(cap["start"]), int(cap["end"])
                    if s < cursor:
                        continue
                    if s > cursor:
                        ET.SubElement(pl_main, "blank", length=str(s - cursor))
                    pid = caption_producers[cap["id"]]
                    entry = ET.SubElement(pl_main, "entry", producer=pid, **{"in": "0", "out": str(mlt_out(0, e - s))})
                    _prop(entry, "kdenlive:id", str(self.producers[pid][1]))
                    _prop(entry, "explica:caption_id", cap["id"])
                    cursor = e
            tr = ET.SubElement(root, "tractor", id=f"tractor{n // 2}", **{"in": "0", "out": str(max(0, cursor - 1))})
            if t["kind"] == "audio":
                _prop(tr, "kdenlive:audio_track", "1")
            _prop(tr, "kdenlive:trackheight", "67")
            _prop(tr, "kdenlive:timeline_active", "1")
            _prop(tr, "kdenlive:collapsed", "0")
            _prop(tr, "kdenlive:thumbs_format", "")
            _prop(tr, "kdenlive:audio_rec", "")
            _prop(tr, "kdenlive:track_name", t["name"])
            _prop(tr, "explica:track_id", t["id"])
            hide = "video" if t["kind"] == "audio" else "audio"
            ET.SubElement(tr, "track", hide=hide, producer=f"playlist{n}")
            ET.SubElement(tr, "track", hide=hide, producer=f"playlist{n + 1}")
            if t["kind"] == "audio":
                for fid, props in (("volume", {"window": "75", "max_gain": "20dB"}), ("panner", {"channel": "-1", "start": "0.5"}), ("audiolevel", {"iec_scale": "0", "dbpeak": "1"})):
                    f = ET.SubElement(tr, "filter", id=f"filter_{n}_{fid}")
                    for k, v in props.items():
                        _prop(f, k, v)
                    _prop(f, "mlt_service", fid)
                    _prop(f, "internal_added", "237")
                    _prop(f, "disable", "1")
            track_tractors.append((t, f"tractor{n // 2}"))
            n += 2
        # --- sequence tractor ---------------------------------------------------
        seq = ET.SubElement(root, "tractor", id=seq_uuid, **{"in": "0", "out": str(max(0, total - 1))})
        _prop(seq, "kdenlive:uuid", seq_uuid)
        _prop(seq, "kdenlive:clipname", p.m["name"])
        _prop(seq, "kdenlive:sequenceproperties.hasAudio", "1")
        _prop(seq, "kdenlive:sequenceproperties.hasVideo", "1")
        _prop(seq, "kdenlive:sequenceproperties.activeTrack", str(len(ordered)))
        _prop(seq, "kdenlive:sequenceproperties.tracksCount", str(len(ordered)))
        _prop(seq, "kdenlive:sequenceproperties.documentuuid", seq_uuid)
        _prop(seq, "kdenlive:duration", _clock(total, fps.num, fps.den))
        _prop(seq, "kdenlive:maxduration", str(total))
        _prop(seq, "kdenlive:producer_type", "17")
        seq_kid = self._kid()
        _prop(seq, "kdenlive:id", str(seq_kid))
        _prop(seq, "kdenlive:clip_type", "0")
        _prop(seq, "kdenlive:folderid", "-1")
        _prop(seq, "kdenlive:markers", "[\n]\n")
        _prop(seq, "kdenlive:sequenceproperties.audioTarget", "1")
        _prop(seq, "kdenlive:sequenceproperties.tracks", str(len(ordered)))
        _prop(seq, "kdenlive:sequenceproperties.verticalzoom", "1")
        _prop(seq, "kdenlive:sequenceproperties.videoTarget", str(len(ordered)))
        _prop(seq, "kdenlive:sequenceproperties.zonein", "0")
        _prop(seq, "kdenlive:sequenceproperties.zoneout", str(max(1, total - 1)))
        _prop(seq, "kdenlive:sequenceproperties.zoom", "8")
        _prop(seq, "kdenlive:sequenceproperties.groups", "[\n]\n")
        _prop(seq, "kdenlive:sequenceproperties.guides", json.dumps(self._guides(), indent=4))
        _prop(seq, "kdenlive:sequenceproperties.position", "0")
        _prop(seq, "kdenlive:sequenceproperties.scrollPos", "0")
        _prop(seq, "kdenlive:sequenceproperties.disablepreview", "0")
        _prop(seq, "kdenlive:monitorPosition", "0")
        _prop(seq, "explica:revision", str(p.revision))
        _prop(seq, "explica:project_id", p.m["id"])
        ET.SubElement(seq, "track", producer="black_track")
        for _, tid in track_tractors:
            ET.SubElement(seq, "track", producer=tid)
        k = 0
        for i, (t, _) in enumerate(track_tractors, start=1):
            tr = ET.SubElement(seq, "transition", id=f"transition{k}")
            _prop(tr, "a_track", "0")
            _prop(tr, "b_track", str(i))
            if t["kind"] == "audio":
                _prop(tr, "mlt_service", "mix")
                _prop(tr, "kdenlive_id", "mix")
                _prop(tr, "internal_added", "237")
                _prop(tr, "always_active", "1")
                _prop(tr, "accepts_blanks", "1")
                _prop(tr, "sum", "1")
            else:
                _prop(tr, "compositing", "0")
                _prop(tr, "distort", "0")
                _prop(tr, "rotate_center", "0")
                _prop(tr, "mlt_service", "qtblend")
                _prop(tr, "kdenlive_id", "qtblend")
                _prop(tr, "internal_added", "237")
                _prop(tr, "always_active", "1")
            k += 1
        for fid, props in (("volume", {"window": "75", "max_gain": "20dB"}), ("panner", {"channel": "-1", "start": "0.5"})):
            f = ET.SubElement(seq, "filter", id=f"filter_seq_{fid}")
            for kk, v in props.items():
                _prop(f, kk, v)
            _prop(f, "mlt_service", fid)
            _prop(f, "internal_added", "237")
            _prop(f, "disable", "1")
        # --- main bin --------------------------------------------------------------
        bin_ = ET.SubElement(root, "playlist", id="main_bin")
        _prop(bin_, "kdenlive:folder.-1.2", "Sequences")
        _prop(bin_, "kdenlive:sequenceFolder", "2")
        _prop(bin_, "kdenlive:docproperties.audioChannels", "2")
        _prop(bin_, "kdenlive:docproperties.compositing", "1")
        _prop(bin_, "kdenlive:docproperties.documentid", str(int(hashlib.sha1(p.m["id"].encode()).hexdigest()[:12], 16)))
        _prop(bin_, "kdenlive:docproperties.enableTimelineZone", "0")
        _prop(bin_, "kdenlive:docproperties.enableexternalproxy", "0")
        _prop(bin_, "kdenlive:docproperties.enableproxy", "0")
        _prop(bin_, "kdenlive:docproperties.generateimageproxy", "0")
        _prop(bin_, "kdenlive:docproperties.generateproxy", "0")
        _prop(bin_, "kdenlive:docproperties.kdenliveversion", KDENLIVE_VERSION)
        _prop(bin_, "kdenlive:docproperties.previewextension", "")
        _prop(bin_, "kdenlive:docproperties.previewparameters", "")
        _prop(bin_, "kdenlive:docproperties.profile", self._profile_name())
        _prop(bin_, "kdenlive:docproperties.proxyextension", "mov")
        _prop(bin_, "kdenlive:docproperties.proxyimageminsize", "2000")
        _prop(bin_, "kdenlive:docproperties.proxyimagesize", "800")
        _prop(bin_, "kdenlive:docproperties.proxyminsize", "1000")
        _prop(bin_, "kdenlive:docproperties.proxyparams", "")
        _prop(bin_, "kdenlive:docproperties.proxyresize", "640")
        _prop(bin_, "kdenlive:docproperties.seekOffset", "30000")
        _prop(bin_, "kdenlive:docproperties.uuid", seq_uuid)
        _prop(bin_, "kdenlive:docproperties.version", KDENLIVE_DOC_VERSION)
        _prop(bin_, "kdenlive:docproperties.opensequences", seq_uuid)
        _prop(bin_, "kdenlive:docproperties.activetimeline", seq_uuid)
        _prop(bin_, "kdenlive:expandedFolders", "")
        _prop(bin_, "kdenlive:binZoom", "4")
        _prop(bin_, "kdenlive:documentnotes", f"Generated by explica revision {p.revision}. Edit cuts/titles here; scene internals live in scenes/*.json.")
        _prop(bin_, "explica:revision", str(p.revision))
        _prop(bin_, "explica:project_id", p.m["id"])
        _prop(bin_, "xml_retain", "1")
        ET.SubElement(bin_, "entry", producer=seq_uuid, **{"in": "0", "out": "0"})
        for pid, (_, _kid, el) in self.producers.items():
            ET.SubElement(bin_, "entry", producer=pid, **{"in": "0", "out": el.get("out", "0")})
        # --- project tractor -------------------------------------------------------
        pt = ET.SubElement(root, "tractor", id=f"tractor{n // 2}", **{"in": "0", "out": str(max(0, total - 1))})
        _prop(pt, "kdenlive:projectTractor", "1")
        ET.SubElement(pt, "track", producer=seq_uuid, **{"in": "0", "out": str(max(0, total - 1))})
        return root

    def _profile_name(self) -> str:
        p = self.p
        if (p.width, p.height, p.fps.num, p.fps.den) == (1080, 1920, 30, 1):
            return "vertical_hd_30"
        if (p.width, p.height, p.fps.num, p.fps.den) == (1080, 1920, 60, 1):
            return "vertical_hd_60"
        if (p.width, p.height, p.fps.num, p.fps.den) == (1920, 1080, 30, 1):
            return "atsc_1080p_30"
        return f"explica_{p.width}x{p.height}_{p.fps.num}_{p.fps.den}"

    def _guides(self) -> list[dict]:
        guides = []
        for c in (self.p.m["tracks"][1]["clips"] if len(self.p.m["tracks"]) > 1 else []):
            guides.append({"comment": f"{c['source'].get('ref', c['id'])}", "pos": int(c["start"]), "type": 0})
        return guides

    def _producer(self, clip: dict, elems: list[ET.Element]) -> str:
        src = clip["source"]
        kind, ref = src["kind"], src.get("ref")
        length = int(clip["out"]) - int(clip["in"])
        if kind == "scene":
            pid = f"scene_{ref}"
            if pid not in self.producers:
                path, sidecar = self.resolve_scene(ref)
                el = self._av_chain(pid, path, int(sidecar["frames"]), name=f"scene {ref}", clip_type="2")
                _prop(el, "explica:scene_id", ref)
                _prop(el, "explica:scene_hash", sidecar["scene_hash"])
                _prop(el, "explica:scene_source", self.p.m["scenes"][ref]["path"])
                elems.append(el)
            return pid
        if kind == "asset":
            pid = f"asset_{ref}"
            if pid not in self.producers:
                a = self.p.m["assets"][ref]
                frames = int(a.get("duration_frames") or length)
                el = self._av_chain(pid, self.p.asset_path(ref), frames, name=ref, clip_type="1" if a.get("kind") == "audio" else "0")
                _prop(el, "explica:asset_id", ref)
                elems.append(el)
            return pid
        if kind == "title":
            pid = f"title_{ref}"
            if pid not in self.producers:
                el = self._title_producer(pid, self.p.m["titles"][ref], length, name=f"title {ref}")
                _prop(el, "explica:title_id", ref)
                elems.append(el)
            return pid
        if kind == "color":
            color = str(clip.get("color", "#000000")).lstrip("#")
            pid = f"color_{color}"
            if pid not in self.producers:
                el = ET.Element("producer", id=pid, **{"in": "0", "out": str(mlt_out(0, max(length, 1)))})
                _prop(el, "length", str(max(length, 1)))
                _prop(el, "eof", "pause")
                _prop(el, "resource", f"0x{color}ff")
                _prop(el, "aspect_ratio", "1")
                _prop(el, "mlt_service", "color")
                _prop(el, "mlt_image_format", "rgba")
                kid = self._kid()
                _prop(el, "kdenlive:clipname", f"color #{color}")
                _prop(el, "kdenlive:clip_type", "2")
                _prop(el, "kdenlive:folderid", "-1")
                _prop(el, "kdenlive:id", str(kid))
                _prop(el, "kdenlive:duration", _clock(max(length, 1), self.p.fps.num, self.p.fps.den))
                _prop(el, "explica:color", f"#{color}")
                self.producers[pid] = (pid, kid, el)
                elems.append(el)
            return pid
        raise ProjectError(f"unsupported clip source kind {kind!r}")

    def _av_chain(self, pid: str, path: Path, frames: int, name: str, clip_type: str) -> ET.Element:
        el = ET.Element("chain", id=pid, **{"in": "0", "out": str(mlt_out(0, frames))})
        _prop(el, "length", str(frames))
        _prop(el, "eof", "pause")
        _prop(el, "resource", str(path))
        _prop(el, "mlt_service", "avformat-novalidate")
        _prop(el, "seekable", "1")
        kid = self._kid()
        _prop(el, "kdenlive:clipname", name)
        _prop(el, "kdenlive:clip_type", clip_type)
        _prop(el, "kdenlive:folderid", "-1")
        _prop(el, "kdenlive:id", str(kid))
        _prop(el, "kdenlive:file_size", str(path.stat().st_size))
        _prop(el, "kdenlive:file_hash", kdenlive_file_hash(path))
        _prop(el, "kdenlive:duration", _clock(frames, self.p.fps.num, self.p.fps.den))
        self.producers[pid] = (pid, kid, el)
        return el

    def _title_producer(self, pid: str, title: dict, length: int, name: str, caption_box: bool = False) -> ET.Element:
        el = ET.Element("producer", id=pid, **{"in": "0", "out": str(mlt_out(0, length))})
        _prop(el, "length", str(length))
        _prop(el, "eof", "pause")
        _prop(el, "resource", "")
        _prop(el, "progressive", "1")
        _prop(el, "aspect_ratio", "1")
        _prop(el, "seekable", "1")
        _prop(el, "mlt_service", "kdenlivetitle")
        _prop(el, "xmldata", title_xmldata(self.p, title, length, caption_box=caption_box))
        kid = self._kid()
        _prop(el, "kdenlive:clipname", name)
        _prop(el, "kdenlive:clip_type", "2")
        _prop(el, "kdenlive:folderid", "-1")
        _prop(el, "kdenlive:id", str(kid))
        _prop(el, "kdenlive:duration", _clock(length, self.p.fps.num, self.p.fps.den))
        self.producers[pid] = (pid, kid, el)
        return el

    def _caption_producer(self, cap: dict, elems: list[ET.Element]) -> str:
        pid = f"caption_{cap['id']}"
        length = int(cap["end"]) - int(cap["start"])
        el = self._title_producer(pid, caption_title(self.p, cap), length, name=f"caption {cap['id']}", caption_box=True)
        _prop(el, "explica:caption_id", cap["id"])
        elems.append(el)
        return pid


def export_kdenlive(project: Project, scene_clip_resolver, out_path: Path | None = None, force: bool = False) -> dict:
    out_path = out_path or project.root / "export" / f"{project.m['id']}.kdenlive"
    sync = project.m.get("sync", {}).get("kdenlive")
    warnings: list[str] = []
    if out_path.exists() and sync and Path(sync["path"]) == Path(project.rel(out_path)):
        current = sha256_file(out_path)
        if current != sync["sha256"] and not force:
            alt = out_path.with_name(out_path.stem + f".agent-r{project.revision}.kdenlive")
            warnings.append(f"{out_path.name} was modified since the last sync (GUI edit?); wrote {alt.name} instead. Run sync_from_kdenlive first, or force=true to overwrite.")
            out_path = alt
    exporter = KdenliveExporter(project, scene_clip_resolver)
    root = exporter.build()
    ET.indent(root, space=" ")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text('<?xml version="1.0" encoding="utf-8"?>\n' + ET.tostring(root, encoding="unicode") + "\n", encoding="utf-8")
    digest = sha256_file(out_path)
    if not warnings:
        project.m.setdefault("sync", {})["kdenlive"] = {"path": project.rel(out_path), "sha256": digest, "revision": project.revision, "exported_at": __import__("time").strftime("%Y-%m-%dT%H:%M:%S%z"), "timeline_fingerprint": timeline_fingerprint(project.m)}
        project._write_manifest()
    return {"path": str(out_path), "sha256": digest, "revision": project.revision, "warnings": warnings, "producers": len(exporter.producers)}


# ---------------------------------------------------------------------- read-back
def read_kdenlive(path: Path, project: Project) -> dict:
    """Parse a .kdenlive (MLT XML) into explica's timeline model. Returns {tracks, captions, titles, unmodelled, revision}."""
    tree = ET.parse(path)
    root = tree.getroot()
    fps = project.fps
    producers: dict[str, dict] = {}
    for el in root:
        if el.tag in ("producer", "chain"):
            props = {p.get("name"): (p.text or "") for p in el.findall("property")}
            producers[el.get("id")] = {"tag": el.tag, "props": props, "in": el.get("in"), "out": el.get("out")}
    playlists: dict[str, ET.Element] = {el.get("id"): el for el in root.findall("playlist")}
    tractors: dict[str, ET.Element] = {el.get("id"): el for el in root.findall("tractor")}
    # sequence tractor = the one with kdenlive:uuid
    seq = next((t for t in tractors.values() if any(p.get("name") == "kdenlive:uuid" for p in t.findall("property"))), None)
    if seq is None:
        raise ProjectError("no Kdenlive sequence tractor found (is this a Kdenlive 1.1 document?)")
    seq_props = {p.get("name"): (p.text or "") for p in seq.findall("property")}
    track_refs = [tr.get("producer") for tr in seq.findall("track")][1:]  # skip black_track
    result_tracks: list[dict] = []
    captions: list[dict] = []
    titles: dict[str, dict] = {}
    unmodelled: list[str] = []
    track_models = {t["id"]: t for t in project.m["tracks"]}
    for idx, tref in enumerate(track_refs):
        tr = tractors.get(tref)
        if tr is None:
            unmodelled.append(f"track {tref} missing")
            continue
        tprops = {p.get("name"): (p.text or "") for p in tr.findall("property")}
        is_audio = tprops.get("kdenlive:audio_track") == "1"
        tid = tprops.get("explica:track_id")
        if tid not in track_models:
            # a track added in the GUI: name it by position
            tid = f"{'A' if is_audio else 'V'}{idx + 1}-gui"
            unmodelled.append(f"track {tref} ({tprops.get('kdenlive:track_name', '?')}) not in manifest; imported as {tid}")
        track = {"id": tid, "kind": "audio" if is_audio else "video", "name": tprops.get("kdenlive:track_name", tid), "clips": []}
        for sub in tr.findall("track"):
            pl = playlists.get(sub.get("producer"))
            if pl is None:
                continue
            cursor = 0
            for child in pl:
                if child.tag == "blank":
                    cursor += int(child.get("length"))
                    continue
                if child.tag != "entry":
                    continue
                pid = child.get("producer")
                pin = parse_mlt_time(child.get("in", "0"), fps)
                pout = parse_mlt_time(child.get("out", "0"), fps)
                length = length_from_mlt(pin, pout)
                eprops = {p.get("name"): (p.text or "") for p in child.findall("property")}
                prod = producers.get(pid, {"props": {}})
                pp = prod["props"]
                gain = 0.0
                for f in child.findall("filter"):
                    fp = {p.get("name"): (p.text or "") for p in f.findall("property")}
                    if fp.get("mlt_service") == "volume" and "level" in fp:
                        try:
                            gain = float(str(fp["level"]).split("=")[-1].split(";")[0])
                        except ValueError:
                            unmodelled.append(f"keyframed volume on clip at {cursor} in {tid}")
                    else:
                        unmodelled.append(f"effect {fp.get('mlt_service')} on clip at {cursor} in {tid}")
                if "explica:caption_id" in eprops or "explica:caption_id" in pp:
                    cid = eprops.get("explica:caption_id") or pp.get("explica:caption_id")
                    text = _title_text(pp.get("xmldata", ""))
                    captions.append({"id": cid, "start": cursor, "end": cursor + length, "text": text})
                    cursor += length
                    continue
                source = _source_from_producer(pp, pid)
                if source["kind"] == "title":
                    tref_id = source["ref"]
                    titles[tref_id] = _title_fields(pp.get("xmldata", ""), project)
                clip_id = eprops.get("explica:clip_id") or f"{tid}-{pid}-{cursor}"
                clip = {"id": clip_id, "source": source, "start": cursor, "in": pin, "out": pin + length, "gain_db": gain, "enabled": True}
                if source["kind"] == "color":
                    clip["color"] = source.pop("color", "#000000")
                track["clips"].append(clip)
                cursor += length
        if tr.findall("transition"):
            unmodelled.append(f"same-track mix/transition on {tid}")
        result_tracks.append(track)
    for tr_el in seq.findall("transition"):
        tp = {p.get("name"): (p.text or "") for p in tr_el.findall("property")}
        if tp.get("internal_added") != "237":
            unmodelled.append(f"composition {tp.get('mlt_service')} between tracks {tp.get('a_track')}-{tp.get('b_track')}")
    guides = []
    try:
        guides = json.loads(seq_props.get("kdenlive:sequenceproperties.guides", "[]") or "[]")
    except ValueError:
        pass
    return {
        "revision": int(seq_props.get("explica:revision", "0") or 0),
        "tracks": result_tracks,
        "captions": sorted(captions, key=lambda c: c["start"]),
        "titles": titles,
        "guides": guides,
        "unmodelled": sorted(set(unmodelled)),
    }


def _source_from_producer(pp: dict, pid: str) -> dict:
    if "explica:scene_id" in pp:
        return {"kind": "scene", "ref": pp["explica:scene_id"]}
    if "explica:asset_id" in pp:
        return {"kind": "asset", "ref": pp["explica:asset_id"]}
    if "explica:title_id" in pp:
        return {"kind": "title", "ref": pp["explica:title_id"]}
    if pp.get("mlt_service") == "kdenlivetitle":
        return {"kind": "title", "ref": f"gui-{pid}"}
    if pp.get("mlt_service") == "color":
        return {"kind": "color", "ref": None, "color": "#" + pp.get("resource", "0x000000ff")[2:8]}
    return {"kind": "asset", "ref": f"gui-{pid}", "resource": pp.get("resource")}


def _title_text(xmldata: str) -> str:
    if not xmldata:
        return ""
    try:
        doc = ET.fromstring(unescape(xmldata) if xmldata.startswith("&lt;") else xmldata)
    except ET.ParseError:
        m = re.search(r"<content[^>]*>(.*?)</content>", xmldata, re.S)
        return unescape(m.group(1)) if m else ""
    texts = [c.text or "" for c in doc.iter("content") if c.text]
    return "\n".join(texts)


def _title_fields(xmldata: str, project: Project) -> dict:
    fields = {"text": _title_text(xmldata)}
    try:
        doc = ET.fromstring(unescape(xmldata) if xmldata.startswith("&lt;") else xmldata)
        for item in doc.iter("item"):
            if item.get("type") == "QGraphicsTextItem":
                content = item.find("content")
                pos = item.find("position")
                if content is not None:
                    fields["size"] = int(float(content.get("font-pixel-size", 64)))
                    rgba = content.get("font-color", "255,255,255,255").split(",")
                    fields["color"] = "#" + "".join(f"{int(v):02x}" for v in rgba[:3])
                    fields["weight"] = "bold" if int(content.get("font-weight", 400)) >= 600 else "normal"
                    box_w = int(float(content.get("box-width", project.width * 0.84)))
                    fields["box_width"] = box_w
                    if pos is not None:
                        fields["x"] = int(float(pos.get("x", 0))) + box_w // 2
                        fields["y"] = int(float(pos.get("y", 0))) + int(fields["size"] * 0.8)
                break
    except (ET.ParseError, ValueError):
        pass
    return fields


def timeline_fingerprint(manifest: dict) -> str:
    """Hash of the montage-relevant manifest parts (tracks, captions, titles): what a GUI session can change."""
    data = json.dumps({"tracks": manifest.get("tracks"), "captions": manifest.get("captions"), "titles": manifest.get("titles")}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def sync_from_kdenlive(project: Project, path: Path, expected_revision: int | None = None, dry_run: bool = False, force: bool = False) -> OpResult:
    """Reconcile GUI edits into the manifest. Refuses silently-diverged states (A19)."""
    parsed = read_kdenlive(path, project)
    sync = project.m.get("sync", {}).get("kdenlive", {})
    warnings: list[str] = []
    if parsed["revision"] and sync and parsed["revision"] != sync.get("revision"):
        warnings.append(f"the .kdenlive was exported at revision {parsed['revision']} but the last sync records revision {sync.get('revision')}")
    current_fp = timeline_fingerprint(project.m)
    if sync.get("timeline_fingerprint") and sync["timeline_fingerprint"] != current_fp and not force:
        raise ProjectError(
            f"divergent state: the manifest timeline changed (revision {project.revision}) after the .kdenlive was exported/synced at revision {sync.get('revision')}, "
            "and syncing would overwrite those changes. Restore the export checkpoint (project_history) or re-export, or pass force=true. Nothing was changed."
        )
    before = {"tracks": project.m["tracks"], "captions": project.m["captions"], "titles": project.m["titles"]}
    new_tracks = []
    for t in project.m["tracks"]:
        found = next((pt for pt in parsed["tracks"] if pt["id"] == t["id"]), None)
        new_tracks.append({**t, "clips": found["clips"] if found else []})
    for pt in parsed["tracks"]:
        if pt["id"] not in {t["id"] for t in project.m["tracks"]}:
            new_tracks.append(pt)
    new_titles = dict(project.m["titles"])
    for tid, fields in parsed["titles"].items():
        new_titles[tid] = {**new_titles.get(tid, {}), **fields}
    after = {"tracks": new_tracks, "captions": parsed["captions"], "titles": new_titles}
    diff = _json_diff(before, after)
    warnings.extend(f"unmodelled in manifest (kept only in Kdenlive file): {u}" for u in parsed["unmodelled"])
    tl_warnings = project._validate_timeline(new_tracks, parsed["captions"])
    hard = [w for w in tl_warnings if w.startswith("ERROR")]
    if hard:
        raise ProjectError("GUI state does not validate: " + "; ".join(hard))
    warnings.extend(tl_warnings)

    def apply() -> list[str]:
        project.m["tracks"] = new_tracks
        project.m["captions"] = parsed["captions"]
        project.m["titles"] = new_titles
        project.m.setdefault("sync", {})["kdenlive"] = {"path": project.rel(path), "sha256": sha256_file(path), "revision": project.revision + 1, "synced_at": __import__("time").strftime("%Y-%m-%dT%H:%M:%S%z"), "unmodelled": parsed["unmodelled"], "timeline_fingerprint": timeline_fingerprint({"tracks": new_tracks, "captions": parsed["captions"], "titles": new_titles})}
        return [project.rel(path)]

    res = project._mutate("kdenlive.sync", expected_revision, dry_run, apply, warnings=warnings, diff=diff, summary=f"sync from {path.name} ({len(diff)} changes)")
    res.data["guides"] = parsed["guides"]
    res.data["unmodelled"] = parsed["unmodelled"]
    return res
