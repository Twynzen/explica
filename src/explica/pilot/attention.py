"""Build the original pilot project: «Atención: un paso» (single head, toy numbers, Spanish).

Beats (script in `SENTENCES`): each sentence becomes one narration clip (A1),
one caption (V4, timed by the *measured* audio duration, never estimated from
text) and the scene of its beat (V2) spans the sentences of that beat. Music is
an original synthesized bed (A2). Everything is editable afterwards: scene
sources in scenes/*.json, cuts and titles in the exported .kdenlive.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..audio import generate_music, piper_available, synthesize_voice
from ..media import duration_frames
from ..project import Project
from ..timebase import Fps
from .attention_math import compute, rounded

W, H = 1080, 1920
FPS = Fps(30, 1)

# (beat_id, [sentences])  — kept short for mobile captions (≤ 120 chars each)
# (beat_id, [(caption_text, tts_text)]) — the caption is what the viewer reads; the TTS text spells
# letters/symbols the way a Spanish speaker says them so the placeholder voice stays intelligible.
SENTENCES: list[tuple[str, list[tuple[str, str]]]] = [
    ("beat1", [("Tres tokens de juguete: El, gato, duerme.", "Tres tokens de juguete: El, gato, duerme."),
               ("¿A qué atiende la última posición?", "¿A qué atiende la última posición?")]),
    ("beat2", [("Cada token aporta una clave, k; «duerme» aporta la consulta, q.", "Cada token aporta una clave, ka. Duerme aporta la consulta, cu.")]),
    ("beat3", [("Puntuación: q por k, entre raíz de d sub k.", "Puntuación: cu por ka, entre raíz de de sub ka."),
               ("Softmax las convierte en pesos que suman uno.", "Softmax las convierte en pesos que suman uno.")]),
    ("beat4", [("Cada peso multiplica un valor, v; la suma es la salida de «duerme».", "Cada peso multiplica un valor, uve. La suma es la salida de duerme.")]),
    ("beat5", [("Una cabeza simplificada; el modelo la repite en muchas capas.", "Una cabeza simplificada. El modelo la repite en muchas capas.")]),
]

TOKEN_X = [240, 540, 840]
TOKEN_Y = 620
BEAT_TITLES = {
    "beat1": "Atención: un paso",
    "beat2": "Claves y consulta",
    "beat3": "Puntuaciones → pesos",
    "beat4": "Combinar valores",
    "beat5": "Una cabeza, muchas capas",
}


def _fade(aid: str, target: str, start: int, dur: int = 12, prop: str = "opacity", frm=0, to=1, easing="ease_out") -> dict:
    return {"id": aid, "target": target, "property": prop, "from": frm, "to": to, "start": start, "end": start + dur, "easing": easing}


def _tokens(step, glow_idx: int | None = None, y: int = TOKEN_Y) -> list[dict]:
    objs = []
    for i, (t, x) in enumerate(zip(step.tokens, TOKEN_X, strict=True)):
        objs.append({"id": f"tok{i + 1}", "type": "token", "text": t, "x": x, "y": y, "size": 60, "glow": 1.0 if glow_idx == i else 0.0})
    return objs


def _vector(oid: str, values, x: int, y: int, label: str, size: int = 46) -> dict:
    return {"id": oid, "type": "vector", "values": list(values), "x": x, "y": y, "label": label, "size": size, "decimals": 2, "label_size": 40}


def build_scenes(step, durations: dict[str, int]) -> list[dict]:
    """Scene sources for the five beats; `durations` are frames per beat (from measured narration)."""
    w = rounded(step.weights)
    s = rounded(step.scores)
    out = rounded(step.output)
    scenes = []

    # ---- beat 1: tokens appear, title ----------------------------------------
    d = durations["beat1"]
    objs = [
        {"id": "title", "type": "text", "text": BEAT_TITLES["beat1"], "x": W // 2, "y": 180, "size": 68, "weight": "bold"},
        {"id": "subtitle", "type": "text", "text": "ejemplo de juguete · tokenización ilustrativa", "x": W // 2, "y": 270, "size": 40, "color": "#bbbbbb"},
        *_tokens(step),
        {"id": "pos1", "type": "text", "text": "1", "x": TOKEN_X[0], "y": 720, "size": 40, "color": "#888888"},
        {"id": "pos2", "type": "text", "text": "2", "x": TOKEN_X[1], "y": 720, "size": 40, "color": "#888888"},
        {"id": "pos3", "type": "text", "text": "3", "x": TOKEN_X[2], "y": 720, "size": 40, "color": "#888888"},
        {"id": "question", "type": "text", "text": "¿Qué mira la posición 3?", "x": W // 2, "y": 1000, "size": 56, "weight": "bold"},
        {"id": "mask", "type": "text", "text": "máscara causal: puede mirar las posiciones 1, 2 y 3", "x": W // 2, "y": 1100, "size": 38, "color": "#bbbbbb", "max_width": 900},
    ]
    anims = [_fade("a_title", "title", 0), _fade("a_sub", "subtitle", 6)]
    for i in range(3):
        anims.append(_fade(f"a_tok{i + 1}", f"tok{i + 1}", 15 + i * 10))
        anims.append(_fade(f"a_pos{i + 1}", f"pos{i + 1}", 15 + i * 10))
    half = max(50, d // 2)
    anims.append(_fade("a_q", "question", half))
    anims.append(_fade("a_mask", "mask", half + 10))
    anims.append(_fade("a_glow3", "tok3", half, 15, "glow", 0, 1))
    scenes.append(_scene("beat1", d, objs, anims))

    # ---- beat 2: keys under tokens, query above «duerme» ---------------------
    d = durations["beat2"]
    objs = [
        {"id": "title", "type": "text", "text": BEAT_TITLES["beat2"], "x": W // 2, "y": 180, "size": 68, "weight": "bold"},
        *_tokens(step, glow_idx=2),
        _vector("k1", step.k[0], TOKEN_X[0], 760, "k₁"),
        _vector("k2", step.k[1], TOKEN_X[1], 760, "k₂"),
        _vector("k3", step.k[2], TOKEN_X[2], 760, "k₃"),
        _vector("q", step.q, TOKEN_X[2], 330, "q"),
        {"id": "dk", "type": "formula", "text": "d_{k} = 2", "x": W // 2, "y": 1120, "size": 52, "color": "#bbbbbb"},
        {"id": "note", "type": "text", "text": "cada clave y la consulta son vectores de 2 números", "x": W // 2, "y": 1220, "size": 38, "color": "#bbbbbb", "max_width": 900},
    ]
    anims = [_fade("a_title", "title", 0)]
    for i in range(3):
        anims.append(_fade(f"a_k{i + 1}", f"k{i + 1}", 12 + i * 10))
    half = max(50, d // 2)
    anims += [_fade("a_q", "q", half), _fade("a_dk", "dk", half + 8), _fade("a_note", "note", half + 16)]
    scenes.append(_scene("beat2", d, objs, anims))

    # ---- beat 3: scores → softmax → weights (bars + arcs) ---------------------
    d = durations["beat3"]
    half = max(60, d // 2)
    objs = [
        {"id": "title", "type": "text", "text": BEAT_TITLES["beat3"], "x": W // 2, "y": 180, "size": 68, "weight": "bold"},
        *_tokens(step, glow_idx=2),
        {"id": "formula", "type": "formula", "text": "s_{i} = q·k_{i} / √{d_{k}}", "x": W // 2, "y": 330, "size": 58},
        {"id": "s1", "type": "number", "value": s[0], "x": TOKEN_X[0], "y": 790, "size": 50, "prefix": "s₁ = "},
        {"id": "s2", "type": "number", "value": s[1], "x": TOKEN_X[1], "y": 790, "size": 50, "prefix": "s₂ = "},
        {"id": "s3", "type": "number", "value": s[2], "x": TOKEN_X[2], "y": 790, "size": 50, "prefix": "s₃ = "},
        {"id": "softmax", "type": "formula", "text": "w_{i} = softmax(s)_{i}", "x": W // 2, "y": 930, "size": 54, "color": "#dddddd"},
        {"id": "bar1", "type": "bar", "x": TOKEN_X[0], "y": 1360, "w": 90, "h_max": 260, "value": 0.0, "label": "w₁", "size": 44},
        {"id": "bar2", "type": "bar", "x": TOKEN_X[1], "y": 1360, "w": 90, "h_max": 260, "value": 0.0, "label": "w₂", "size": 44},
        {"id": "bar3", "type": "bar", "x": TOKEN_X[2], "y": 1360, "w": 90, "h_max": 260, "value": 0.0, "label": "w₃", "size": 44},
        {"id": "sum", "type": "number", "value": 0.0, "x": W // 2, "y": 1460, "size": 50, "prefix": "suma = ", "color": "#bbbbbb"},
        {"id": "arc1", "type": "arc", "from": {"ref": "tok3"}, "to": {"ref": "tok1"}, "lift": 170, "weight": w[0], "progress": 0.0},
        {"id": "arc2", "type": "arc", "from": {"ref": "tok3"}, "to": {"ref": "tok2"}, "lift": 110, "weight": w[1], "progress": 0.0},
        {"id": "arc3", "type": "arc", "from": {"ref": "tok3", "dx": -40}, "to": {"ref": "tok3", "dx": 40}, "lift": 70, "weight": w[2], "progress": 0.0},
    ]
    anims = [_fade("a_title", "title", 0), _fade("a_formula", "formula", 6)]
    for i in range(3):
        anims.append(_fade(f"a_s{i + 1}", f"s{i + 1}", 20 + i * 10))
    anims.append(_fade("a_softmax", "softmax", half))
    for i in range(3):
        anims.append({"id": f"a_bar{i + 1}", "target": f"bar{i + 1}", "property": "value", "from": 0.0, "to": w[i], "start": half + 10, "end": half + 40, "easing": "ease_out"})
        anims.append({"id": f"a_arc{i + 1}", "target": f"arc{i + 1}", "property": "progress", "from": 0.0, "to": 1.0, "start": half + 15 + i * 5, "end": half + 40 + i * 5, "easing": "ease_in_out"})
    anims.append({"id": "a_sum", "target": "sum", "property": "value", "from": 0.0, "to": round(sum(w), 2), "start": half + 10, "end": half + 40, "easing": "ease_out"})
    scenes.append(_scene("beat3", d, objs, anims))

    # ---- beat 4: weighted sum of values ------------------------------------------
    d = durations["beat4"]
    half = max(60, d // 2)
    objs = [
        {"id": "title", "type": "text", "text": BEAT_TITLES["beat4"], "x": W // 2, "y": 180, "size": 68, "weight": "bold"},
        *_tokens(step, glow_idx=2),
        _vector("v1", step.v[0], TOKEN_X[0], 760, "v₁"),
        _vector("v2", step.v[1], TOKEN_X[1], 760, "v₂"),
        _vector("v3", step.v[2], TOKEN_X[2], 760, "v₃"),
        {"id": "w1", "type": "number", "value": w[0], "x": TOKEN_X[0], "y": 960, "size": 46, "prefix": "× ", "color": "#dddddd"},
        {"id": "w2", "type": "number", "value": w[1], "x": TOKEN_X[1], "y": 960, "size": 46, "prefix": "× ", "color": "#dddddd"},
        {"id": "w3", "type": "number", "value": w[2], "x": TOKEN_X[2], "y": 960, "size": 46, "prefix": "× ", "color": "#dddddd"},
        {"id": "formula", "type": "formula", "text": "salida = Σ w_{i}·v_{i}", "x": W // 2, "y": 1080, "size": 56},
        _vector("out", out, W // 2, 1250, "salida", size=50),
        {"id": "arrow1", "type": "arrow", "x": TOKEN_X[0], "y": 1010, "x2": W // 2 - 90, "y2": 1180, "width": 3, "color": "#888888", "progress": 0.0},
        {"id": "arrow2", "type": "arrow", "x": TOKEN_X[1], "y": 1010, "x2": W // 2, "y2": 1180, "width": 3, "color": "#888888", "progress": 0.0},
        {"id": "arrow3", "type": "arrow", "x": TOKEN_X[2], "y": 1010, "x2": W // 2 + 90, "y2": 1180, "width": 3, "color": "#888888", "progress": 0.0},
    ]
    anims = [_fade("a_title", "title", 0)]
    for i in range(3):
        anims.append(_fade(f"a_v{i + 1}", f"v{i + 1}", 10 + i * 8))
        anims.append(_fade(f"a_w{i + 1}", f"w{i + 1}", 30 + i * 8))
    anims.append(_fade("a_formula", "formula", half))
    for i in range(3):
        anims.append({"id": f"a_arrow{i + 1}", "target": f"arrow{i + 1}", "property": "progress", "from": 0.0, "to": 1.0, "start": half + 8 + i * 4, "end": half + 28 + i * 4, "easing": "ease_in_out"})
    anims.append(_fade("a_out", "out", half + 30, 15))
    scenes.append(_scene("beat4", d, objs, anims))

    # ---- beat 5: recap, zoom out to layers -------------------------------------------
    d = durations["beat5"]
    half = max(50, d // 2)
    objs = [
        {"id": "title", "type": "text", "text": BEAT_TITLES["beat5"], "x": W // 2, "y": 180, "size": 68, "weight": "bold"},
        *_tokens(step, glow_idx=2),
        {"id": "arc1", "type": "arc", "from": {"ref": "tok3"}, "to": {"ref": "tok1"}, "lift": 170, "weight": w[0]},
        {"id": "arc2", "type": "arc", "from": {"ref": "tok3"}, "to": {"ref": "tok2"}, "lift": 110, "weight": w[1]},
        {"id": "arc3", "type": "arc", "from": {"ref": "tok3", "dx": -40}, "to": {"ref": "tok3", "dx": 40}, "lift": 70, "weight": w[2]},
        {"id": "box", "type": "rect", "x": W // 2, "y": 640, "w": 860, "h": 300, "rx": 30, "stroke": "#888888", "stroke_width": 3, "opacity": 0.0},
        {"id": "layer1", "type": "rect", "x": W // 2, "y": 1060, "w": 700, "h": 90, "rx": 20, "stroke": "#aaaaaa", "stroke_width": 3},
        {"id": "layer2", "type": "rect", "x": W // 2, "y": 1180, "w": 700, "h": 90, "rx": 20, "stroke": "#aaaaaa", "stroke_width": 3},
        {"id": "layer3", "type": "rect", "x": W // 2, "y": 1300, "w": 700, "h": 90, "rx": 20, "stroke": "#aaaaaa", "stroke_width": 3},
        {"id": "dots", "type": "text", "text": "…", "x": W // 2, "y": 1400, "size": 60, "color": "#aaaaaa"},
        {"id": "recap", "type": "text", "text": "una cabeza · un paso · números de juguete", "x": W // 2, "y": 920, "size": 40, "color": "#bbbbbb", "max_width": 900},
        {"id": "layers_label", "type": "text", "text": "muchas capas", "x": W // 2, "y": 1060, "size": 44, "weight": "bold"},
    ]
    anims = [_fade("a_title", "title", 0), _fade("a_box", "box", 20, 15), _fade("a_recap", "recap", 25)]
    for i in range(3):
        anims.append(_fade(f"a_layer{i + 1}", f"layer{i + 1}", half + i * 8))
    anims += [_fade("a_dots", "dots", half + 30), _fade("a_layers_label", "layers_label", half)]
    anims.append({"id": "a_zoom", "target": "camera", "property": "zoom", "from": 1.0, "to": 0.9, "start": half, "end": d, "easing": "ease_in_out"})
    scenes.append(_scene("beat5", d, objs, anims))
    return scenes


def _scene(sid: str, duration: int, objects: list[dict], animations: list[dict]) -> dict:
    return {
        "schema": "explica/scene@1",
        "id": sid,
        "title": BEAT_TITLES[sid],
        "duration_frames": int(duration),
        "background": "#000000",
        "width": W,
        "height": H,
        "font": "Liberation Sans",
        "decimal_separator": ",",
        "camera": {"x": W // 2, "y": H // 2, "zoom": 1.0},
        "objects": objects,
        "animations": animations,
        "notes": "Toy example; see pilot/README.md for sources and simplifications.",
    }


def build(root: Path, piper_model: Path | None = None, pad_frames: int = 9, gap_frames: int = 4, length_scale: float = 0.95) -> dict:
    """Create the full pilot project at `root`. Returns a summary with measured durations."""
    step = compute()
    assert abs(sum(step.weights) - 1.0) < 1e-9
    root = Path(root)
    p = Project.create(root, "Atención: un paso", FPS, W, H, preview=(540, 960), project_id="pilot-atencion")
    p.m["provenance"] = {
        "notes": "Original pilot. Script, numbers and visuals written for this project; no third-party audio or animation assets.",
        "licenses": ["narration: Piper TTS placeholder (local), replace with a human recording via `explica asset replace voice-<n> file.wav`", "music: generated by explica (CC0)"],
        "sources": ["https://arxiv.org/abs/1706.03762", "https://www.3blue1brown.com/lessons/attention"],
    }
    p._write_manifest()
    summary: dict = {"root": str(root), "beats": [], "voice": None}
    # ---- narration per sentence (measured) -------------------------------------------
    voice_ok = piper_available() and piper_model is not None and Path(piper_model).exists()
    cursor = 0
    beat_durations: dict[str, int] = {}
    a1_clips: list[dict] = []
    captions: list[dict] = []
    n = 0
    for beat_id, sentences in SENTENCES:
        beat_start = cursor
        for sent, tts_text in sentences:
            n += 1
            aid = f"voice-{n:02d}"
            wav = root / "assets" / "voice" / f"{aid}.wav"
            if voice_ok:
                info = synthesize_voice(tts_text, wav, Path(piper_model), length_scale=length_scale)
                frames = duration_frames(wav, FPS, "ceil")
                p.add_asset(aid, wav, "audio", license="placeholder synthetic narration (Piper, local model)", provenance=f"explica pilot · {info['model']}", copy_into=None, meta={"duration_frames": frames, "text": sent, "tts_text": tts_text})
            else:
                frames = max(45, int(len(sent) * 2.2))  # silent placeholder; flagged in summary
            a1_clips.append({"id": f"A1-{aid}", "source": {"kind": "asset", "ref": aid}, "start": cursor, "in": 0, "out": frames, "gain_db": 0.0, "enabled": True})
            captions.append({"id": f"cap-{n:02d}", "start": cursor, "end": cursor + frames, "text": sent})
            cursor += frames + gap_frames
        beat_durations[beat_id] = cursor - beat_start + pad_frames
        cursor += pad_frames
        summary["beats"].append({"id": beat_id, "start": beat_start, "frames": beat_durations[beat_id]})
    total = cursor
    summary["voice"] = "piper" if voice_ok else "none (silent placeholder durations; install explica[tts] and pass a model)"
    # ---- scenes -------------------------------------------------------------------------------
    for scene in build_scenes(step, beat_durations):
        p.add_scene(scene)
    # ---- music bed --------------------------------------------------------------------------
    music = generate_music(root / "assets" / "music" / "bed.wav", total / FPS.value + 1.0, seed=11, gain_db=-20.0)
    p.add_asset("music-bed", Path(music["path"]), "audio", license="CC0 (generated by explica)", provenance="ffmpeg lavfi synthesis, seed 11", copy_into=None, meta={"duration_frames": duration_frames(Path(music["path"]), FPS, "floor")})
    # ---- titles ------------------------------------------------------------------------------
    p.set_title("credit", {"text": "Fuentes: Vaswani et al. 2017 · 3Blue1Brown, Attention", "y": int(H * 0.94), "size": 34, "color": "#999999", "weight": "normal"})
    p.set_title("label", {"text": "explica · piloto original", "y": int(H * 0.06), "size": 32, "color": "#777777", "weight": "normal"})
    # ---- timeline -----------------------------------------------------------------------------
    ops_list: list[dict] = [{"op": "add_clip", "track": "V1", "clip": {"id": "V1-bg", "source": {"kind": "color", "ref": None}, "color": "#000000", "start": 0, "in": 0, "out": total}}]
    for b in summary["beats"]:
        ops_list.append({"op": "add_clip", "track": "V2", "clip": {"id": f"V2-{b['id']}", "source": {"kind": "scene", "ref": b["id"]}, "start": b["start"], "in": 0, "out": b["frames"]}})
    ops_list.append({"op": "add_clip", "track": "V3", "clip": {"id": "V3-label", "source": {"kind": "title", "ref": "label"}, "start": 0, "in": 0, "out": total}})
    last = summary["beats"][-1]
    ops_list.append({"op": "add_clip", "track": "V3", "clip": {"id": "V3-credit", "source": {"kind": "title", "ref": "credit"}, "start": last["start"], "in": 0, "out": last["frames"]}})
    # V3 has two overlapping clips (label + credit) → put the credit on V4? No: captions live on V4. Use V3 for label only and a second
    # title row inside the label is simpler, so merge: credit replaces label during the last beat.
    ops_list[-2]["clip"]["out"] = last["start"]
    for c in a1_clips:
        if voice_ok:
            ops_list.append({"op": "add_clip", "track": "A1", "clip": c})
    ops_list.append({"op": "add_clip", "track": "A2", "clip": {"id": "A2-music", "source": {"kind": "asset", "ref": "music-bed"}, "start": 0, "in": 0, "out": total, "gain_db": -6.0}})
    for cap in captions:
        ops_list.append({"op": "add_caption", **cap})
    res = p.timeline_patch(ops_list)
    summary["warnings"] = res.warnings
    summary["revision"] = Project.load(root).revision
    summary["duration_frames"] = total
    summary["math"] = {"scores": rounded(step.scores, 3), "weights": rounded(step.weights, 3), "output": rounded(step.output, 3), "sum_weights": sum(step.weights)}
    (root / "PILOT-SUMMARY.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False))
    return summary


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="Build the explica attention pilot project")
    ap.add_argument("root")
    ap.add_argument("--piper-model", help="path to a Piper .onnx voice (e.g. es_MX-ald-medium.onnx)")
    args = ap.parse_args(argv)
    summary = build(Path(args.root), Path(args.piper_model) if args.piper_model else None)
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
