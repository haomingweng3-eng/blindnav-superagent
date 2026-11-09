"""Tests for the Issue #18 A/B/C runner's pure helpers (no dataset, no model inference).

Pins the frame-set reporting labels, the PR #17 arm-A reference cross-check, failure-case
selection and the per-frame CSV column contract that downstream reports depend on.
"""
import csv
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "tools")))

import responsibility_split_eval as rse  # noqa: E402
import responsibility_split_metrics as rsm  # noqa: E402


# ---------------------------------------------------------------------------
# Column contract
# ---------------------------------------------------------------------------
def test_per_frame_columns_cover_every_metric_flag():
    flag_names = set(rse.PER_FRAME_FIELDS)
    for view, _ in rsm.GT_DANGER_VIEWS:
        for suffix in ("miss", "miss_straight", "turn_guidance_unevaluable"):
            assert f"{view}_{suffix}" in flag_names, f"{view}_{suffix}"
        assert f"danger_blocked_{view}" in flag_names
    for required in ("effective_command", "safety_state", "guidance", "confirmed_clear",
                     "clear_weak", "true_false_stop", "unnecessary_observe",
                     "unknown_on_confirmed_clear", "guidance_on_confirmed_clear",
                     "forward_guidance", "warning", "pr15_23", "gt_hint_A_oracle",
                     "path_status", "obstacle_state", "obstacle_clearance_m"):
        assert required in flag_names


def test_gt_walk_bins_ok_counts_full_validated_band():
    fwd = np.concatenate([np.linspace(1.5 + b + 0.1, 1.5 + b + 0.9, 5) for b in range(5)])
    ground = np.ones(len(fwd), dtype=bool)
    raw = np.full(len(fwd), 3)          # sidewalk
    swept = np.ones(len(fwd), dtype=bool)
    bins_ok, walk = rse.gt_walk_bins_ok(fwd, ground, raw, swept)
    assert bins_ok == 5 and walk == len(fwd)
    bins_ok2, _ = rse.gt_walk_bins_ok(fwd, ground, np.full(len(fwd), 1), swept)
    assert bins_ok2 == 0               # road is not walkable GT


# ---------------------------------------------------------------------------
# Fixed PR #15 23-frame reporting label
# ---------------------------------------------------------------------------
def test_pr15_23_frames_uses_gt_oracle_label_only():
    rows = [
        {"condition": "annotated", "arm": "A", "session": "s1", "frame": 1,
         "pr15_event": True, "gt_hint_A_oracle": "STRAIGHT"},
        {"condition": "annotated", "arm": "A", "session": "s1", "frame": 2,
         "pr15_event": True, "gt_hint_A_oracle": "STOP"},
        {"condition": "annotated", "arm": "B", "session": "s1", "frame": 3,
         "pr15_event": True, "gt_hint_A_oracle": "STRAIGHT"},
        {"condition": "production", "arm": "A", "session": "s1", "frame": 4,
         "pr15_event": True, "gt_hint_A_oracle": "STRAIGHT"},
    ]
    assert rse.pr15_23_frames(rows) == [("s1", 1)]


# ---------------------------------------------------------------------------
# PR #17 reference cross-check (arm A == PR #17's evaluated fusion policy)
# ---------------------------------------------------------------------------
def _arm_a_row(session="s1", frame=0, hint="STOP", hazard_bins=2, nw_bins=0, unk_bins=0,
               walk_swept=10, danger_ground=True, danger_any=True, pr15=False):
    return {"condition": "annotated", "arm": "A", "session": session, "frame": frame,
            "legacy_hint": hint, "legacy_hazard_bins": hazard_bins,
            "legacy_non_walkable_bins": nw_bins, "legacy_unknown_bins": unk_bins,
            "legacy_walkable_swept_points": walk_swept,
            "danger_ground": danger_ground, "danger_any": danger_any,
            "pr15_event": pr15}


def _write_reference(path, rows):
    fields = ["condition", "arm", "session", "frame", "hint", "hazard_bins",
              "non_walkable_bins", "unknown_bins", "walkable_swept_points",
              "danger_ground", "danger_any", "pr15_event"]
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def test_reference_check_accepts_identical_arm_a(tmp_path):
    path = tmp_path / "ref.csv"
    rows = [_arm_a_row(frame=0), _arm_a_row(frame=1, hint="STRAIGHT", hazard_bins=0,
                                            danger_ground=False, danger_any=False)]
    _write_reference(path, [
        {"condition": "annotated", "arm": "B", "session": "s1", "frame": 0, "hint": "STOP",
         "hazard_bins": 2, "non_walkable_bins": 0, "unknown_bins": 0,
         "walkable_swept_points": 10, "danger_ground": "True", "danger_any": "True",
         "pr15_event": "False"},
        {"condition": "annotated", "arm": "B", "session": "s1", "frame": 1,
         "hint": "STRAIGHT", "hazard_bins": 0, "non_walkable_bins": 0, "unknown_bins": 0,
         "walkable_swept_points": 10, "danger_ground": "False", "danger_any": "False",
         "pr15_event": "False"},
    ])
    report = rse.check_pr17_reference(rows, str(path))
    assert report["verified"] and report["mismatches"] == 0
    assert report["compared_rows"] == 2


def test_reference_check_detects_policy_drift(tmp_path):
    path = tmp_path / "ref.csv"
    _write_reference(path, [
        {"condition": "annotated", "arm": "B", "session": "s1", "frame": 0, "hint": "STOP",
         "hazard_bins": 2, "non_walkable_bins": 0, "unknown_bins": 0,
         "walkable_swept_points": 10, "danger_ground": "True", "danger_any": "True",
         "pr15_event": "False"}])
    report = rse.check_pr17_reference([_arm_a_row(frame=0, hint="UNKNOWN")], str(path))
    assert not report["verified"]
    assert report["mismatch_examples"][0]["field"] == "hint"


# ---------------------------------------------------------------------------
# Failure-case evidence selection
# ---------------------------------------------------------------------------
def _failure_row(arm="B", kind="road_miss_straight", session="s1", frame=0):
    row = {"condition": "annotated", "arm": arm, "session": session, "frame": frame,
           "effective_command": "STRAIGHT", "safety_state": "CLEAR", "reason": "centred",
           "gt_road_pts_swept": 7, "gt_curb_pts_swept": 0, "gt_danger_obstacle_pts": 0,
           "confirmed_clear": False}
    for view, _ in rsm.GT_DANGER_VIEWS:
        for suffix in ("miss", "miss_straight", "turn_guidance_unevaluable"):
            row[f"{view}_{suffix}"] = False
        row[f"danger_blocked_{view}"] = False
    row["true_false_stop"] = False
    row["unnecessary_observe"] = False
    row[f"{kind}"] = True
    return row


def test_select_failure_cases_prefers_b_c_and_respects_cap():
    rows = [_failure_row(arm="A", frame=0), _failure_row(arm="B", frame=1),
            _failure_row(arm="C", frame=2), _failure_row(arm="B", frame=3)]
    cases = rse.select_failure_cases(rows, max_total=1)
    assert len(cases) == 1
    assert cases[0]["arm"] in ("B", "C")
    assert cases[0]["session"] == "s1"


def test_select_failure_cases_dedupes_frames():
    rows = [_failure_row(arm="B", frame=1), _failure_row(arm="C", frame=1)]
    cases = rse.select_failure_cases(rows, max_total=5)
    assert len(cases) == 1
