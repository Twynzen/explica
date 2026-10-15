"""A07: the pilot's numbers are computed, consistent and displayable without a rounding paradox."""

from pathlib import Path

from explica.pilot import attention
from explica.pilot.attention_math import compute, rounded, softmax
from explica.project import Project
from explica.scene.model import validate_scene


def test_softmax_sums_to_one_and_is_monotonic():
    step = compute()
    assert abs(sum(step.weights) - 1.0) < 1e-12
    assert step.weights[1] > step.weights[0] == step.weights[2]
    assert softmax([0.0, 0.0]) == [0.5, 0.5]


def test_displayed_weights_sum_to_one_at_two_decimals():
    step = compute()
    w = rounded(step.weights)
    assert round(sum(w), 2) == 1.00


def test_output_is_weighted_sum_of_values():
    step = compute()
    for j in range(2):
        assert abs(step.output[j] - sum(step.weights[i] * step.v[i][j] for i in range(3))) < 1e-12
    assert rounded(step.output) == [0.44, 0.56]


def test_scores_use_sqrt_dk():
    step = compute()
    assert abs(step.scores[0] - 1.0 / 2**0.5) < 1e-12


def test_pilot_scenes_validate_and_build_without_tts(tmp_path: Path):
    durations = {b: 120 for b in ("beat1", "beat2", "beat3", "beat4", "beat5")}
    for scene in attention.build_scenes(compute(), durations):
        assert validate_scene(scene) == [], scene["id"]
    summary = attention.build(tmp_path / "pilot", piper_model=None)
    p = Project.load(tmp_path / "pilot")
    assert len(p.m["scenes"]) == 5 and summary["voice"].startswith("none")
    v = p.validate()
    assert v["ok"], v
    assert not [w for w in v["warnings"] if "overlaps" in w], v["warnings"]  # captions never cover diagrams
    assert len(p.m["captions"]) == 7
