import json
import os
from pathlib import Path

import pytest

from explica.project import LockedError, Project, ProjectError, RevisionConflict
from explica.scene.model import SceneError

from .conftest import sample_scene


def test_create_and_load(tmp_path: Path):
    p = Project.create(tmp_path / "p", "Mi proyecto")
    assert p.revision == 1
    q = Project.load(p.root)
    assert q.m["id"] == "mi-proyecto"
    assert [t["id"] for t in q.m["tracks"]] == ["V1", "V2", "V3", "V4", "A1", "A2", "A3"]
    with pytest.raises(ProjectError):
        Project.create(tmp_path / "p", "again")


def test_scene_patch_dry_run_then_apply(project: Project):
    rev = project.revision
    ops = [{"op": "set", "path": "objects.title.text", "value": "Nuevo"}]
    dry = project.scene_patch("s1", ops, expected_revision=rev, dry_run=True)
    assert dry.dry_run and dry.diff and project.revision == rev
    assert project.load_scene("s1")["objects"][0]["text"] == "Hola"
    res = project.scene_patch("s1", ops, expected_revision=rev)
    assert res.ok and res.revision == rev + 1 and res.checkpoint
    assert project.load_scene("s1")["objects"][0]["text"] == "Nuevo"
    assert project.m["scenes"]["s1"]["sha256"] == res.data["scene_hash"]
    assert "scenes/s1.json" in res.affected_files


def test_revision_conflict(project: Project):
    with pytest.raises(RevisionConflict):
        project.scene_patch("s1", [{"op": "set", "path": "objects.title.text", "value": "x"}], expected_revision=project.revision + 5)


def test_scene_patch_rejects_invalid_result(project: Project):
    with pytest.raises(SceneError):
        project.scene_patch("s1", [{"op": "add_object", "object": {"id": "bad", "type": "nope"}}])
    assert project.load_scene("s1")["objects"][-1]["id"] == "n"  # untouched


def test_undo_restores_previous_state(project: Project):
    before = project.load_scene("s1")
    project.scene_patch("s1", [{"op": "set", "path": "objects.title.text", "value": "cambiado"}])
    assert project.load_scene("s1") != before
    res = project.undo()
    assert res.ok and res.op == "project.undo"
    after = project.load_scene("s1")
    assert after == before
    # revision keeps increasing; history records the restore
    assert project.revision > 2
    assert project.m["history"][-1]["op"] == "project.restore"


def test_checkpoint_restore_explicit(project: Project):
    cid = project.checkpoint("manual-1")
    project.scene_patch("s1", [{"op": "set_duration", "frames": 99}])
    assert project.m["scenes"]["s1"]["duration_frames"] == 99
    project.restore(cid)
    assert project.m["scenes"]["s1"]["duration_frames"] == 60
    assert any(c["id"] == cid for c in project.list_checkpoints())


def test_timeline_patch_validation(timeline_project: Project):
    p = timeline_project
    q = p.timeline_query(0, 100)
    assert [t["id"] for t in q["tracks"]] == ["V1", "V2", "V3", "V4", "A1", "A2", "A3"]
    v2 = next(t for t in q["tracks"] if t["id"] == "V2")
    assert v2["clips"][0]["source_info"]["path"] == "scenes/s1.json"
    with pytest.raises(ProjectError):  # overlap on the same track
        p.timeline_patch([{"op": "add_clip", "track": "V2", "clip": {"id": "c2", "source": {"kind": "scene", "ref": "s1"}, "start": 20, "in": 0, "out": 30}}])
    with pytest.raises(ProjectError):  # beyond source duration
        p.timeline_patch([{"op": "trim_clip", "id": "c1", "out": 61}])
    with pytest.raises(ProjectError):  # caption overlap
        p.timeline_patch([{"op": "add_caption", "id": "cap3", "start": 30, "end": 50, "text": "x"}])
    with pytest.raises(ProjectError):  # audio asset on a video track rejected at source-kind level
        p.timeline_patch([{"op": "add_clip", "track": "A1", "clip": {"id": "bad", "source": {"kind": "scene", "ref": "s1"}, "start": 0, "in": 0, "out": 10}}])
    res = p.timeline_patch([{"op": "trim_clip", "id": "c1", "out": 50}, {"op": "move_clip", "id": "ttl", "start": 5}], dry_run=True)
    assert res.dry_run and len(res.diff) == 2


def test_split_and_shift(timeline_project: Project):
    p = timeline_project
    p.timeline_patch([{"op": "split_clip", "id": "c1", "at": 30}])
    t, left = p.find_clip("c1")
    _, right = p.find_clip("c1-b")
    assert (left["in"], left["out"], left["start"]) == (0, 20, 10)
    assert (right["in"], right["out"], right["start"]) == (20, 60, 30)
    p.timeline_patch([{"op": "shift", "from_frame": 30, "delta": 15}])
    _, right = p.find_clip("c1-b")
    assert right["start"] == 45
    assert next(c for c in p.m["captions"] if c["id"] == "cap2")["start"] == 55


def test_validate_reports_caption_overlap_with_diagram(timeline_project: Project):
    p = timeline_project
    # move a token into the caption box area
    p.scene_patch("s1", [{"op": "set", "path": "objects.t1.y", "value": 1650}])
    v = p.validate()
    assert v["ok"]
    assert any("overlaps s1.t1" in w for w in v["warnings"])


def test_lock_blocks_concurrent_writer(project: Project, tmp_path: Path):
    lock = project.root / ".lock"
    lock.write_text(json.dumps({"pid": os.getpid(), "owner": "other"}))
    with pytest.raises(LockedError):
        project.scene_patch("s1", [{"op": "set", "path": "objects.title.text", "value": "x"}])
    lock.unlink()
    # stale lock from a dead pid is cleared automatically
    lock.write_text(json.dumps({"pid": 999999, "owner": "dead"}))
    assert project.scene_patch("s1", [{"op": "set", "path": "objects.title.text", "value": "y"}]).ok


def test_add_scene_duplicate_rejected(project: Project):
    with pytest.raises(ProjectError):
        project.add_scene(sample_scene())


def test_inspect_contains_limits_and_history(project: Project):
    info = project.inspect()
    assert info["revision"] == project.revision
    assert info["limits"] and info["history_tail"]
    assert info["interval_convention"].startswith("[start, end)")
