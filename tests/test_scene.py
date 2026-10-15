import json
from pathlib import Path

import pytest

from explica.scene.formula import parse
from explica.scene.model import SceneError, animate, base_state, ease, scene_hash, state_at, validate_scene
from explica.scene.render import SceneRenderer, render_frame_png, render_scene, scene_bounds
from explica.timebase import Fps

from .conftest import needs_ffmpeg, sample_scene


def test_validate_ok_and_warnings():
    scene = sample_scene()
    assert validate_scene(scene) == []
    scene["animations"][0]["end"] = 999
    assert any("after scene duration" in w for w in validate_scene(scene))


def test_validate_errors():
    scene = sample_scene()
    scene["objects"].append({"id": "title", "type": "text"})
    with pytest.raises(SceneError):
        validate_scene(scene)
    scene = sample_scene()
    scene["animations"][0]["target"] = "nope"
    with pytest.raises(SceneError):
        validate_scene(scene)


def test_easing_bounds():
    for name in ("linear", "ease_in", "ease_out", "ease_in_out", "smooth", "step"):
        assert ease(name, 0.0) == 0.0
        assert ease(name, 1.0) == 1.0
        assert 0.0 <= ease(name, 0.5) <= 1.0


def test_animation_before_start_uses_from_and_holds_to_after_end():
    scene = sample_scene()
    s0 = state_at(scene, 0)
    assert s0["objects"]["title"]["opacity"] == 0
    s_end = state_at(scene, 20)
    assert s_end["objects"]["title"]["opacity"] == 1
    s_late = state_at(scene, 59)
    assert s_late["objects"]["title"]["opacity"] == 1
    assert 1.0 < s_late["camera"]["zoom"] <= 1.2


def test_nested_property_animation():
    scene = sample_scene()
    scene["animations"].append({"id": "vv", "target": "v", "property": "values.0", "from": 0.0, "to": 2.0, "start": 0, "end": 10})
    assert state_at(scene, 5)["objects"]["v"]["values"][0] == pytest.approx(1.0)
    assert state_at(scene, 10)["objects"]["v"]["values"][0] == 2.0


def test_scene_hash_stable():
    a, b = sample_scene(), sample_scene()
    assert scene_hash(a) == scene_hash(b)
    b["objects"][0]["text"] = "x"
    assert scene_hash(a) != scene_hash(b)


def test_formula_parse():
    nodes = parse("s_{i} = q·k_{i} / √{d_{k}}")
    kinds = [n.kind for n in nodes]
    assert "sub" in kinds and "sqrt" in kinds
    sqrt = next(n for n in nodes if n.kind == "sqrt")
    assert any(c.kind == "sub" for c in sqrt.children)


def test_bounds_have_every_object_id():
    scene = sample_scene()
    b = scene_bounds(scene, 30)
    for obj in scene["objects"]:
        assert obj["id"] in b, obj["id"]
        bb = b[obj["id"]]["bbox"]
        assert bb["x1"] >= bb["x0"] and bb["y1"] >= bb["y0"]
    assert b["camera"]["zoom"] == 1.0


def test_safe_area_report_flags_edge_objects():
    scene = sample_scene()
    scene["objects"].append({"id": "edge", "type": "text", "text": "borde", "x": 20, "y": 20, "size": 40})
    r = SceneRenderer(scene)
    assert any("edge" in w for w in r.safe_area_report(30))


def test_render_png_and_determinism(tmp_path: Path):
    scene = sample_scene()
    a = render_frame_png(scene, 30, tmp_path / "a.png", 270, 480)
    b = render_frame_png(scene, 30, tmp_path / "b.png", 270, 480)
    assert a.read_bytes() == b.read_bytes()
    assert a.stat().st_size > 1000


@needs_ffmpeg
def test_render_scene_mp4_and_alpha_mov(tmp_path: Path):
    from explica.media import probe

    scene = sample_scene(duration=12)
    info = render_scene(scene, tmp_path / "s.mp4", 270, 480, Fps(30, 1))
    assert info["frames"] == 12
    pr = probe(tmp_path / "s.mp4")
    v = next(s for s in pr["streams"] if s["codec_type"] == "video")
    assert (v["width"], v["height"]) == (270, 480)
    assert int(v["nb_frames"]) == 12
    sidecar = json.loads((tmp_path / "s.mp4.json").read_text())
    assert sidecar["scene_hash"] == scene_hash(scene)
    assert "title" in sidecar["object_ids"]
    scene["background"] = None
    info2 = render_scene(scene, tmp_path / "s.mov", 270, 480, Fps(30, 1))
    assert info2["transparent"] is True
    v2 = next(s for s in probe(tmp_path / "s.mov")["streams"] if s["codec_type"] == "video")
    assert v2["codec_name"] == "qtrle" and v2["pix_fmt"] == "argb"


def test_base_state_defaults_applied():
    st = base_state(sample_scene())
    tok = st["objects"]["t1"]
    assert tok["pad_x"] == 28 and tok["opacity"] == 1.0
    assert st["camera"] == {"x": 540, "y": 960, "zoom": 1.0}
    assert animate(st, [], 0)["objects"]["t1"]["x"] == 300
