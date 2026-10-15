import json
import shutil
from pathlib import Path

import pytest

from explica.project import Project
from explica.timebase import Fps

HAVE_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
HAVE_MELT = shutil.which("melt") is not None

needs_ffmpeg = pytest.mark.skipif(not HAVE_FFMPEG, reason="ffmpeg/ffprobe not installed")
needs_melt = pytest.mark.skipif(not HAVE_MELT, reason="melt (MLT) not installed")


def sample_scene(scene_id: str = "s1", duration: int = 60) -> dict:
    return {
        "schema": "explica/scene@1",
        "id": scene_id,
        "duration_frames": duration,
        "background": "#000000",
        "width": 1080,
        "height": 1920,
        "decimal_separator": ",",
        "objects": [
            {"id": "title", "type": "text", "text": "Hola", "x": 540, "y": 300, "size": 72, "weight": "bold"},
            {"id": "t1", "type": "token", "text": "El", "x": 300, "y": 900},
            {"id": "t2", "type": "token", "text": "gato", "x": 700, "y": 900, "glow": 1.0},
            {"id": "arc", "type": "arc", "from": {"ref": "t2"}, "to": {"ref": "t1"}, "lift": 150, "weight": 0.8},
            {"id": "f", "type": "formula", "text": "s_{i} = q·k_{i} / √{d_{k}}", "x": 540, "y": 1200, "size": 56},
            {"id": "v", "type": "vector", "values": [0.5, -1.25], "x": 540, "y": 1400, "label": "q"},
            {"id": "n", "type": "number", "value": 1.0, "x": 540, "y": 1700, "prefix": "Σ = "},
        ],
        "animations": [
            {"id": "a1", "target": "title", "property": "opacity", "from": 0, "to": 1, "start": 0, "end": 20, "easing": "ease_out"},
            {"id": "a2", "target": "arc", "property": "progress", "from": 0, "to": 1, "start": 20, "end": 50},
            {"id": "a3", "target": "camera", "property": "zoom", "from": 1, "to": 1.2, "start": 30, "end": 60},
        ],
    }


@pytest.fixture
def project(tmp_path: Path) -> Project:
    p = Project.create(tmp_path / "proj", "Test Project", Fps(30, 1), 1080, 1920, preview=(270, 480), project_id="test-project")
    p.add_scene(sample_scene())
    return Project.load(p.root)


@pytest.fixture
def timeline_project(project: Project) -> Project:
    project.timeline_patch([
        {"op": "add_clip", "track": "V1", "clip": {"id": "bg", "source": {"kind": "color", "ref": None}, "color": "#101010", "start": 0, "in": 0, "out": 90}},
        {"op": "add_clip", "track": "V2", "clip": {"id": "c1", "source": {"kind": "scene", "ref": "s1"}, "start": 10, "in": 0, "out": 60}},
        {"op": "add_caption", "id": "cap1", "start": 5, "end": 40, "text": "Primer caption"},
        {"op": "add_caption", "id": "cap2", "start": 40, "end": 80, "text": "Segundo caption"},
    ])
    project.set_title("main", {"text": "Título", "y": 250, "size": 72})
    project.timeline_patch([{"op": "add_clip", "track": "V3", "clip": {"id": "ttl", "source": {"kind": "title", "ref": "main"}, "start": 0, "in": 0, "out": 45}}])
    return Project.load(project.root)


def write_json(path: Path, data: dict) -> Path:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    return path
