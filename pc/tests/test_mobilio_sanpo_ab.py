import os
import sys

import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "tools")))

from mobilio_sanpo_ab import (  # noqa: E402
    candidate_frame_keys,
    make_candidate_row,
    resolve_condition_depth,
)


def test_candidate_and_baseline_keys_are_identical():
    baseline = [{"session": "s", "frame": 1}, {"session": "s", "frame": 2}]
    candidate = [{"session": "s", "frame": 1}, {"session": "s", "frame": 2}]
    assert candidate_frame_keys(baseline) == candidate_frame_keys(candidate)


def test_production_depth_resolver_does_not_read_gt():
    fit = type("Fit", (), {"ok": True, "a": 1.0, "b": 0.0, "up": None,
                            "confidence": 0.7})()
    resolved = resolve_condition_depth(
        "production", q_dav2=np.ones((2, 2)), fit=fit,
        gt_depth=np.full((2, 2), 99.0))
    assert not np.all(resolved.depth_m == 99.0)
    assert resolved.source == "dav2_calibrated"


def test_candidate_row_contains_same_gt_denominators_as_baseline():
    gt_ref = {"plane_ok": True, "gt_road_in_path": True, "gt_curb_in_path": False,
              "gt_occupancy_in_path": False, "confirmed_clear": False}
    decision = type("D", (), {"state": "BLOCKED", "guidance": None,
                               "reason": "test", "angle_rad": 0.0})()
    row = make_candidate_row("production", "mobilio", "s", 1, gt_ref=gt_ref,
                             decision=decision)
    assert row["session"] == "s" and row["frame"] == 1
    assert row["gt_road_in_path"] is True
