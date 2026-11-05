"""Offline ShadowWalk corridor-keeping metrics.  Synthetic fixtures only."""
import json
import math
import os
import subprocess
import sys

import pytest

from blindnav_pc.eval_shadowwalk import (FALLBACK_FIXTURE, resolve_corridor, run_eval,
                                         summarize_frames)
from blindnav_pc.shadowwalk import LEFT, RIGHT, STOP, STRAIGHT, UNKNOWN

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
HINTS = {STRAIGHT, LEFT, RIGHT, STOP, UNKNOWN}


def _linear_p95(values):
    xs = sorted(values)
    pos = 0.95 * (len(xs) - 1)
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    weight = pos - lo
    return xs[lo] * (1.0 - weight) + xs[hi] * weight


def _assert_safety_report(metrics, summary):
    assert metrics["auto_go"] is False
    assert metrics["illegal_hints"] == 0
    assert metrics["w1_curb_recall_gate"] == "not_claimed"
    assert metrics["w1_near_field_recall_gate"] == "not_claimed"
    assert metrics["pure_monocular"] == "supervised_low_speed_constrained_demo"
    assert set(metrics["states"]) == HINTS
    assert "CROSS" not in summary
    assert "可以过街" not in summary
    assert "已经通过" not in summary
    assert "路缘" in summary and "尚未" in summary
    assert "低速" in summary


def test_straight_keep_has_no_error_and_no_stop(tmp_path):
    out = tmp_path / "out"
    metrics = run_eval("straight_keep", str(out))
    summary = (out / "summary.md").read_text(encoding="utf-8")
    _assert_safety_report(metrics, summary)
    assert metrics["mode"] == "synthetic_fixture"
    assert metrics["fallback"] is False
    assert metrics["n_frames"] == 8
    assert metrics["lateral_offset_mae_m"] == pytest.approx(0.0)
    assert metrics["lateral_offset_p95_m"] == pytest.approx(0.0)
    assert metrics["heading_drift_deg"] == pytest.approx(0.0)
    assert metrics["correction_count"] == 0
    assert metrics["flip_count"] == 0
    assert metrics["unknown_rate"] == 0.0
    assert metrics["stop_rate"] == 0.0
    assert metrics["coverage_fail_rate"] == 0.0
    assert metrics["mean_coverage"] == pytest.approx(1.0)
    assert all(frame["hint"] == STRAIGHT and frame["reason"] == "centred" for frame in metrics["frames"])
    written = json.loads((out / "metrics.json").read_text(encoding="utf-8"))
    assert written["lateral_offset_mae_m"] == 0.0
    assert "走廊" in summary


def test_mild_drift_counts_corrections_and_one_flip(tmp_path):
    metrics = run_eval("mild_drift", str(tmp_path / "out"))
    offsets = [0.0, 0.5, 0.6, 0.5, 0.6]
    assert [frame["hint"] for frame in metrics["frames"]] == [STRAIGHT, LEFT, LEFT, RIGHT, RIGHT]
    assert metrics["correction_count"] == 4
    assert metrics["flip_count"] == 1
    assert metrics["flip_rate"] == pytest.approx(0.25)
    assert metrics["lateral_offset_mae_m"] == pytest.approx(sum(offsets) / len(offsets))
    assert metrics["lateral_offset_p95_m"] == pytest.approx(_linear_p95(offsets))
    assert metrics["heading_drift_deg"] == pytest.approx(0.0)
    assert metrics["unknown_count"] == 0 and metrics["stop_count"] == 0
    assert metrics["coverage_fail_count"] == 0
    assert metrics["lateral_offset_p95_m"] >= metrics["lateral_offset_mae_m"]


def test_coverage_unknown_and_stop_rates(tmp_path):
    metrics = run_eval("coverage_and_stop", str(tmp_path / "out"))
    reasons = [frame["reason"] for frame in metrics["frames"]]
    assert reasons == [
        "centred", "walkable_unseen", "corridor_unseen", "off_corridor", "yaw_too_large", "no_pose",
    ]
    assert metrics["n_frames"] == 6
    assert metrics["unknown_count"] == 3
    assert metrics["stop_count"] == 2
    assert metrics["coverage_fail_count"] == 1
    assert metrics["unknown_rate"] == pytest.approx(0.5)
    assert metrics["stop_rate"] == round(2 / 6, 6)
    assert metrics["coverage_fail_rate"] == round(1 / 6, 6)
    finite = [0.0, 0.0, 0.0, 1.2, 0.0]
    assert metrics["n_offset_frames"] == 5
    assert metrics["lateral_offset_mae_m"] == pytest.approx(sum(finite) / len(finite))
    assert metrics["lateral_offset_p95_m"] == pytest.approx(_linear_p95(finite))
    assert metrics["heading_drift_deg"] == pytest.approx(8.0)
    assert metrics["heading_drift_net_deg"] == pytest.approx(40.0)
    gap = metrics["frames"][2]
    assert gap["coverage"] < 0.5 and gap["n_points"] >= 50
    assert metrics["frames"][1]["hint"] == UNKNOWN
    assert metrics["frames"][3]["hint"] == STOP
    assert metrics["frames"][5]["offset_m"] is None


def test_preset_and_corridor_json_keep_the_centre(tmp_path):
    preset = run_eval("straight60", str(tmp_path / "preset"))
    assert preset["mode"] == "synthetic_preset"
    assert preset["corridor"]["name"] == "straight60"
    assert preset["lateral_offset_mae_m"] == pytest.approx(0.0)
    assert preset["stop_rate"] == 0.0 and preset["unknown_rate"] == 0.0

    arc = run_eval("left_arc80", str(tmp_path / "arc"))
    assert arc["n_frames"] >= 2
    assert arc["auto_go"] is False
    assert arc["illegal_hints"] == 0
    assert set(frame["hint"] for frame in arc["frames"]) <= HINTS

    corridor = {
        "name": "custom_straight",
        "width_m": 2.5,
        "points": [[0.0, 0.0], [0.0, 40.0]],
    }
    path = tmp_path / "corridor.json"
    path.write_text(json.dumps(corridor), encoding="utf-8")
    custom = run_eval(str(path), str(tmp_path / "custom"))
    assert custom["mode"] == "corridor_json"
    assert custom["fallback"] is False
    assert custom["corridor"]["name"] == "custom_straight"
    assert custom["lateral_offset_mae_m"] == pytest.approx(0.0)
    assert all(frame["hint"] == STRAIGHT for frame in custom["frames"])


def test_session_json_and_metric_helper(tmp_path):
    session = {
        "name": "two_step",
        "corridor": "straight60",
        "frames": [
            {"s_m": 2.0, "offset_m": 0.4, "yaw_error_deg": 4.0, "points": "band"},
            {"s_m": 3.0, "offset_m": -0.4, "yaw_error_deg": -2.0, "points": "band"},
        ],
    }
    path = tmp_path / "session.json"
    path.write_text(json.dumps(session), encoding="utf-8")
    metrics = run_eval(str(path), str(tmp_path / "out"))
    assert metrics["mode"] == "session_json"
    assert [frame["hint"] for frame in metrics["frames"]] == [LEFT, RIGHT]
    assert metrics["flip_count"] == 1
    assert metrics["correction_count"] == 2
    assert metrics["lateral_offset_mae_m"] == pytest.approx(0.4)
    assert metrics["heading_drift_deg"] == pytest.approx(3.0)
    assert metrics["heading_drift_net_deg"] == pytest.approx(-6.0)
    manual = summarize_frames([
        {"hint": LEFT, "text": "稍向左", "reason": "lateral", "offset_m": 0.4,
         "yaw_error_deg": 4.0, "coverage": 1.0},
        {"hint": RIGHT, "text": "稍向右", "reason": "lateral", "offset_m": -0.4,
         "yaw_error_deg": -2.0, "coverage": 1.0},
    ])
    assert manual["flip_count"] == 1
    assert manual["lateral_offset_mae_m"] == pytest.approx(0.4)
    assert manual["heading_drift_deg"] == pytest.approx(3.0)


def test_missing_session_falls_back_to_synthetic_fixture(tmp_path):
    missing = tmp_path / "no_such_session.json"
    out = tmp_path / "out"
    metrics = run_eval(str(missing), str(out))
    summary = (out / "summary.md").read_text(encoding="utf-8")
    assert metrics["mode"] == "synthetic_fallback"
    assert metrics["fallback"] is True
    assert metrics["session_absent"] is True
    assert metrics["fixture"] == FALLBACK_FIXTURE
    assert metrics["n_frames"] == 8
    assert metrics["lateral_offset_mae_m"] == pytest.approx(0.0)
    assert "合成夹具" in summary
    assert (out / "metrics.json").is_file()
    _assert_safety_report(metrics, summary)


def test_unknown_corridor_name_is_rejected():
    with pytest.raises(ValueError):
        resolve_corridor("not_a_corridor")


def test_cli_module_writes_report(tmp_path):
    out = tmp_path / "cli"
    proc = subprocess.run(
        [sys.executable, "-m", "blindnav_pc.eval_shadowwalk",
         "--corridor", "straight_keep", "--out", str(out)],
        cwd=ROOT, check=False, capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert "lateral_mae=" in proc.stdout
    assert (out / "metrics.json").is_file()
    assert (out / "summary.md").is_file()
    bad = subprocess.run(
        [sys.executable, "-m", "blindnav_pc.eval_shadowwalk",
         "--corridor", "nope", "--out", str(tmp_path / "bad")],
        cwd=ROOT, check=False, capture_output=True, text=True,
    )
    assert bad.returncode == 2
