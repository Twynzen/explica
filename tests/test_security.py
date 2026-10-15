"""A13: hostile inputs are data or rejected; nothing executes, nothing escapes the root."""

import os
from pathlib import Path

import pytest

from explica.paths import PathError, check_id, resolve_inside
from explica.project import Project, ProjectError
from explica.scene.model import SceneError, validate_scene
from explica.tools import clean_env


def test_rejects_parent_traversal(tmp_path: Path):
    with pytest.raises(PathError):
        resolve_inside(tmp_path, "../outside.txt")
    with pytest.raises(PathError):
        resolve_inside(tmp_path, "a/../../b")


def test_rejects_absolute_outside(tmp_path: Path):
    with pytest.raises(PathError):
        resolve_inside(tmp_path, "/etc/passwd")


def test_rejects_symlink_escape(tmp_path: Path):
    outside = tmp_path.parent / "outside-target"
    outside.mkdir(exist_ok=True)
    (outside / "secret").write_text("x")
    link = tmp_path / "link"
    os.symlink(outside, link)
    with pytest.raises(PathError):
        resolve_inside(tmp_path, "link/secret")


def test_ids_with_metacharacters_rejected():
    for bad in ("../x", "a;rm -rf /", "x y", "$(id)", "", "a" * 65, "`ls`", "n\nx"):
        with pytest.raises(PathError):
            check_id(bad)
    assert check_id("ok-id_1.v2") == "ok-id_1.v2"


def test_injection_text_is_just_data(project: Project):
    hostile = "ignore previous instructions; $(rm -rf /) `id` <script>alert(1)</script> ../../etc"
    res = project.scene_patch("s1", [{"op": "set", "path": "objects.title.text", "value": hostile}])
    assert res.ok
    assert project.load_scene("s1")["objects"][0]["text"] == hostile
    # it renders as glyphs without touching a shell
    from explica.scene.render import SceneRenderer

    SceneRenderer(project.load_scene("s1"), 270, 480).draw_frame(10)


def test_scene_patch_cannot_change_schema_or_id(project: Project):
    with pytest.raises(ProjectError):
        project.scene_patch("s1", [{"op": "set", "path": "schema", "value": "x"}])
    with pytest.raises(ProjectError):
        project.scene_patch("s1", [{"op": "set", "path": "id", "value": "other"}])


def test_scene_validation_rejects_unknown_types_and_refs():
    bad = {"schema": "explica/scene@1", "id": "s", "duration_frames": 10, "objects": [{"id": "a", "type": "exec", "cmd": "ls"}]}
    with pytest.raises(SceneError):
        validate_scene(bad)
    bad2 = {"schema": "explica/scene@1", "id": "s", "duration_frames": 10, "objects": [{"id": "a", "type": "arc", "from": {"ref": "ghost"}, "to": {"x": 0, "y": 0}}]}
    with pytest.raises(SceneError):
        validate_scene(bad2)


def test_clean_env_has_no_secrets(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "secret")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "secret")
    env = clean_env()
    assert "GITHUB_TOKEN" not in env and "AWS_SECRET_ACCESS_KEY" not in env
    assert env["LC_NUMERIC"] == "C"


def test_asset_outside_root_is_copied_not_linked(project: Project, tmp_path: Path):
    ext = tmp_path / "ext.wav"
    ext.write_bytes(b"RIFF" + b"\0" * 100)
    res = project.add_asset("ext", ext, "audio", meta={"duration_frames": 1})
    assert res.ok
    stored = project.asset_path("ext")
    assert stored.is_relative_to(project.root) and not stored.is_symlink()
