import os
import sys

import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "tools")))

from mobilio_sanpo_ab import (  # noqa: E402
    candidate_frame_keys,
    make_candidate_row,
    resolve_condition_depth,
)
from blindnav_pc.mobilio_surface import SurfaceRoute  # noqa: E402


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


def test_candidate_row_exports_surface_ray_diagnostics():
    gt_ref = {"plane_ok": True}
    decision = type("D", (), {"state": "UNKNOWN", "guidance": None,
                               "reason": "surface:no_candidate_ray", "angle_rad": None})()
    surface = SurfaceRoute("OBSERVE", 0.1, 3.0, 2, 3, 1, False, "non_walkable",
                           termination_distance_m=3.2, last_walkable_distance_m=2.8,
                           termination_cell=(4, 5), last_walkable_cell=(3, 5))
    row = make_candidate_row("production", "mobilio", "s", 1, gt_ref=gt_ref,
                             decision=decision, surface_route=surface)
    assert row["surface_termination_distance_m"] == 3.2
    assert row["surface_last_walkable_distance_m"] == 2.8
    assert row["surface_termination_cell_ix"] == 4
    assert row["surface_last_walkable_cell_iz"] == 5
