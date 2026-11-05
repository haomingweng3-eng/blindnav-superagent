"""Tests for the A/B/C corridor negative-evidence evaluation harness (measurement only).

No model inference is exercised.  These tests pin the frozen pieces of SPEC section 3-4 and
the reporting-only SPEC amendment v1.1 (dual danger views) that the harness owns: the
CLASS_* -> ROLE_* role map, the decision swept-body / GT danger scoring (PRIMARY
``danger_ground`` and SECONDARY ``danger_any``), the PR #15 road/curb event definitions, the
per-row flags with their explicit denominators, the fixed PR #15 23-frame set recovery,
action-flip pairs, the ``summary.json`` aggregation and the CLI contract.
"""
import math
import os
import sys
from types import SimpleNamespace

import numpy as np
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "tools")))

import corridor_evidence_eval as cee  # noqa: E402


# ---------------------------------------------------------------------------
# Fixture row: every field core_summary() reads, with sane defaults.
# ---------------------------------------------------------------------------
def _row(**kw):
    base = {
        "condition": "annotated", "arm": "A", "session": "s", "frame": 0,
        "hint": cee.STRAIGHT, "coverage": 0.6,
        "gt_danger_swept": False, "gt_danger_pts_swept": 0, "gt_walk_pts_swept": 0,
        "danger_ground": False, "danger_any": False,
        "false_free": False, "false_free_any": False,
        "correct_block": False, "correct_block_ground": False, "correct_block_any": False,
        "unknown_on_danger": False, "unknown_on_danger_ground": False,
        "unknown_on_danger_any": False,
        "false_stop": False, "false_stop_ground": False, "false_stop_any": False,
        "unknown_when_gt_walkable": False,
        "walkable_guidance": True,
        "pr15_event": False, "pr15_road_path_band": 0, "pr15_curb_path_band": 0,
        "action_flip_vs_A": False, "action_flip_pair": "",
        "hazard_points": 0, "non_walkable_points": 0, "unknown_points": 0,
        "walkable_swept_points": 0,
        "hazard_bins": 0, "non_walkable_bins": 0, "unknown_bins": 0,
    }
    base.update(kw)
    return base


# ---------------------------------------------------------------------------
# Role map: CLASS_* -> ROLE_* (SPEC section 3)
# ---------------------------------------------------------------------------
def test_role_map_maps_five_classes_exactly():
    cls = np.array([[0, 1, 2, 3, 4, 4, 1]], np.uint8)
    roles = cee.role_map_from_classes(cls)
    assert roles.shape == cls.shape
    assert roles.tolist() == [[cee.ROLE_IGNORE, cee.ROLE_WALKABLE, cee.ROLE_HAZARD,
                               cee.ROLE_NON_WALKABLE, cee.ROLE_NON_WALKABLE,
                               cee.ROLE_NON_WALKABLE, cee.ROLE_WALKABLE]]
    assert roles.dtype == np.uint8


def test_role_values_match_frozen_corridor_evidence_constants():
    # The module may legitimately not be on disk yet for pure-helper tests; when it is,
    # the local fallback values must be the frozen SPEC values and stay in sync.
    ce = pytest.importorskip("blindnav_pc.corridor_evidence")
    assert (ce.ROLE_IGNORE, ce.ROLE_WALKABLE, ce.ROLE_HAZARD,
            ce.ROLE_NON_WALKABLE) == (0, 1, 2, 3)
    assert (cee.ROLE_IGNORE, cee.ROLE_WALKABLE, cee.ROLE_HAZARD,
            cee.ROLE_NON_WALKABLE) == (0, 1, 2, 3)


def test_walkable_mask_is_exactly_class_safe():
    cls = np.array([[0, 1, 2, 1, 3, 4]], np.uint8)
    mask = cee.walkable_mask_from_classes(cls)
    assert mask.dtype == np.uint8
    assert mask.tolist() == [[0, 1, 0, 1, 0, 0]]


# ---------------------------------------------------------------------------
# Swept-body geometry / GT danger scoring
# ---------------------------------------------------------------------------
def test_body_forward_lateral_uses_pose_heading():
    fwd, lat = cee.body_forward_lateral(np.array([1.0]), np.array([2.0]), 0.0)
    assert fwd[0] == pytest.approx(2.0)          # straight ahead
    assert lat[0] == pytest.approx(1.0)          # body right
    # Heading rotated +90 deg (right): map +x becomes body-forward, map +z body-left.
    fwd, lat = cee.body_forward_lateral(np.array([1.0, 0.0]), np.array([0.0, 2.0]),
                                        math.pi / 2.0)
    assert fwd.tolist() == pytest.approx([1.0, 0.0])
    assert lat.tolist() == pytest.approx([0.0, -2.0])


def test_swept_body_mask_uses_forward_range_and_half_width():
    fwd = np.array([1.4, 1.5, 3.0, 6.0, 6.1])
    lat = np.array([0.0, 0.2, -0.4, 0.41, 0.0])
    mask = cee.swept_body_mask(fwd, lat, cee.SW_CFG, near_m=1.5, far_m=6.0,
                               half_width_m=0.4)
    assert mask.tolist() == [False, True, True, False, False]


def test_default_swept_half_width_is_body_plus_margin():
    # Frozen ShadowWalkConfig defaults: body_width_m 0.5 / body_margin_m 0.15 -> 0.4 m.
    if not hasattr(cee.SW_CFG, "swept_half_width_m"):
        pytest.skip("evidence-core ShadowWalkConfig fields not on disk yet")
    assert cee.SW_CFG.swept_half_width_m == pytest.approx(0.4)
    fwd = np.array([3.0, 3.0])
    lat = np.array([0.39, 0.41])
    assert cee.swept_body_mask(fwd, lat, cee.SW_CFG).tolist() == [True, False]


def test_gt_danger_counts_ground_and_obstacle_only_inside_swept_body():
    raw = np.array([1, 2, 3, 4, 7, 6, 30, 1, 4, 27], np.uint8)
    height = np.array([0.0, 0.0, 0.0, 0.5, 1.0, 0.0, 0.0, 0.5, 2.5, 0.0])
    swept = np.array([True, True, True, True, True, True, True, False, True, True])
    ground, obstacle = cee.gt_danger_counts(raw, height, swept, cee.SW_CFG)
    # danger ground ids: 1, 2, 30 -> 3; id 1 at 0.5 m is not ground, walkable 3/6
    # and id 27 are excluded.
    # danger obstacles: 4 (0.5 m), 7 (1.0 m) -> 2; 4 at 2.5 m is above the 2.0 m cap.
    assert ground == 3
    assert obstacle == 2
    none_ground, none_obstacle = cee.gt_danger_counts(raw, height, np.zeros_like(swept),
                                                       cee.SW_CFG)
    assert (none_ground, none_obstacle) == (0, 0)


def test_gt_danger_any_points_adds_object_occupancy_and_nonwalk_class_ground():
    # Points (inside the swept body): road ground (danger id), id 7 lying on the ground
    # (a GT non-walkable-class ground point, not a danger id), water (danger id), safe
    # ground, and obstacle id 4 (0.9 m).
    raw = np.array([1, 7, 16, 3, 4], np.uint8)
    height = np.array([0.0, 0.0, 0.0, 0.0, 0.9])
    swept = np.array([True, True, True, True, True])
    # GT role per point: road/id7/water -> NON_WALKABLE role, safe 3 -> WALKABLE.
    gt_role = np.array([cee.ROLE_NON_WALKABLE, cee.ROLE_NON_WALKABLE,
                        cee.ROLE_NON_WALKABLE, cee.ROLE_WALKABLE,
                        cee.ROLE_NON_WALKABLE], np.uint8)
    any_pts = cee.gt_danger_any_points(raw, height, swept, gt_role, cee.SW_CFG)
    assert any_pts == 4                       # ids 1, 16 (ground ids), 4 (obstacle), 7
    # Without the GT non-walkable-class dimension only ids 1/16 (ground) + 4 (obstacle)
    # count; the id-7 ground point is added only by the non-walkable-class dimension.
    any_ids_only = cee.gt_danger_any_points(raw, height, swept, None, cee.SW_CFG)
    assert any_ids_only == 3


def test_gt_walk_count_requires_safe_id_ground_and_swept_body():
    raw = np.array([3, 5, 6, 17, 1, 3], np.uint8)
    height = np.array([0.0, 0.0, 0.0, 0.0, 0.0, 0.5])   # last one is not ground
    swept = np.array([True, True, True, True, True, True])
    assert cee.gt_walk_count(raw, height, swept, cee.SW_CFG) == 4
    assert cee.gt_walk_count(raw, height, np.zeros(6, bool), cee.SW_CFG) == 0


def test_danger_swept_threshold_is_five_points():
    assert not cee.danger_swept_flag(0)
    assert not cee.danger_swept_flag(cee.MIN_EVENT_POINTS - 1)
    assert cee.MIN_EVENT_POINTS == 5
    assert cee.danger_swept_flag(cee.MIN_EVENT_POINTS)
    assert cee.danger_swept_flag(99)


# ---------------------------------------------------------------------------
# PR #15-compat road/curb in-path events
# ---------------------------------------------------------------------------
def test_pr15_event_counts_exact_pr15_ranges_and_body_width():
    # 5 road points inside the 0.6 m map-frame body / 1.5-6 m forward range.
    raw = np.array([1, 1, 1, 1, 1, 2, 2, 2, 2, 1, 1], np.uint8)
    height = np.zeros(len(raw))
    map_fwd = np.array([2.0, 2.0, 2.0, 2.0, 2.0, 3.0, 3.0, 3.0, 3.0, 1.4, 6.1])
    map_lat = np.zeros(len(raw))
    ev = cee.pr15_event_counts(raw, height, map_fwd, map_lat, cee.SW_CFG)
    assert ev["road_path_band"] == 5
    assert ev["curb_path_band"] == 4        # curb needs 2.5 m forward
    assert ev["road_event"] is True
    assert ev["curb_event"] is False
    assert ev["event"] is True

    # The 0.6 m body gate: lateral 0.6 is inside, 0.61 outside (exact PR #15 definition).
    raw2 = np.array([1, 1], np.uint8)
    ev2 = cee.pr15_event_counts(
        raw2, np.zeros(2), np.array([2.0, 2.0]), np.array([0.6, 0.61]), cee.SW_CFG)
    assert ev2["road_path_band"] == 1
    # Nothing in path -> not an event.
    ev3 = cee.pr15_event_counts(
        np.array([1, 2], np.uint8), np.zeros(2),
        np.array([0.0, 0.0]), np.array([0.0, 0.0]), cee.SW_CFG)
    assert ev3["event"] is False


# ---------------------------------------------------------------------------
# Per-row flags and their denominators (SPEC amendment v1.1 dual danger view)
# ---------------------------------------------------------------------------
def test_row_flags_primary_ground_and_secondary_any_views():
    # Ground danger: primary false-free / correct block / unknown-on-danger.
    flags = cee.row_flags(cee.STRAIGHT, True, True, 0)
    assert flags["false_free"] is True and flags["false_free_any"] is True
    assert flags["walkable_guidance"] is True

    flags = cee.row_flags(cee.STOP, True, True, 0)
    assert flags["correct_block"] is True and flags["correct_block_ground"] is True
    assert flags["correct_block_any"] is True and flags["false_free"] is False

    flags = cee.row_flags(cee.UNKNOWN, True, True, 0)
    assert flags["unknown_on_danger"] is True
    assert flags["unknown_on_danger_ground"] is True
    assert flags["unknown_on_danger_any"] is True

    # Object-occupancy-only danger (secondary view only): the primary flags stay false.
    flags = cee.row_flags(cee.STRAIGHT, False, True, 0)
    assert flags["false_free"] is False
    assert flags["false_free_any"] is True
    flags = cee.row_flags(cee.STOP, False, True, 100)
    assert flags["correct_block"] is False and flags["correct_block_any"] is True

    for hint in (cee.LEFT, cee.RIGHT):
        assert cee.row_flags(hint, False, False, 0)["walkable_guidance"] is True
        assert cee.row_flags(hint, True, True, 0)["false_free"] is True


def test_row_flags_false_stop_and_unknown_when_gt_walkable_thresholds():
    # >= 5 GT walkable points in the swept body is the event threshold.
    assert cee.row_flags(cee.STOP, False, False, 5)["false_stop"] is True
    assert cee.row_flags(cee.STOP, False, False, 5)["false_stop_ground"] is True
    assert cee.row_flags(cee.STOP, False, False, 5)["false_stop_any"] is True
    assert cee.row_flags(cee.STOP, False, False, 4)["false_stop"] is False
    # STOP with primary danger is a correct block, never a false stop.
    assert cee.row_flags(cee.STOP, True, True, 100)["false_stop"] is False
    # Object-occupancy-only danger: a STOP is a false stop under the primary view but not
    # under the secondary view (dual reporting).
    flags = cee.row_flags(cee.STOP, False, True, 10)
    assert flags["false_stop"] is True and flags["false_stop_any"] is False

    assert cee.row_flags(cee.UNKNOWN, False, False, 5)["unknown_when_gt_walkable"] is True
    assert cee.row_flags(cee.UNKNOWN, False, False, 4)["unknown_when_gt_walkable"] is False
    assert cee.row_flags(cee.UNKNOWN, True, True, 100)["unknown_when_gt_walkable"] is True


# ---------------------------------------------------------------------------
# PR #15 23-frame set
# ---------------------------------------------------------------------------
def test_pr15_23_frames_recovered_from_annotated_arm_a_only():
    rows = [
        _row(condition="annotated", arm="A", session="s1", frame=10, hint=cee.STRAIGHT,
             pr15_event=True),
        _row(condition="annotated", arm="A", session="s1", frame=11, hint=cee.LEFT,
             pr15_event=True, walkable_guidance=True),          # not STRAIGHT -> excluded
        _row(condition="annotated", arm="A", session="s2", frame=20, hint=cee.STRAIGHT,
             pr15_event=False),                                 # no event -> excluded
        _row(condition="production", arm="A", session="s1", frame=10, hint=cee.STRAIGHT,
             pr15_event=True),                                  # other condition
        _row(condition="annotated", arm="B", session="s1", frame=10, hint=cee.STRAIGHT,
             pr15_event=True),                                  # other arm
    ]
    frames = cee.pr15_23_frames(rows)
    assert frames == [("s1", 10)]
    assert cee.PR15_23_EXPECTED == 23


# ---------------------------------------------------------------------------
# Action flips vs arm A
# ---------------------------------------------------------------------------
def _aligned_rows():
    return [
        _row(condition="annotated", arm="A", session="s", frame=0, hint=cee.STRAIGHT),
        _row(condition="annotated", arm="B", session="s", frame=0, hint=cee.STOP,
             walkable_guidance=False, correct_block=True, correct_block_ground=True,
             correct_block_any=True, danger_ground=True, danger_any=True,
             gt_danger_swept=True, gt_danger_pts_swept=9),
        _row(condition="annotated", arm="C", session="s", frame=0, hint=cee.STRAIGHT),
        _row(condition="annotated", arm="A", session="s", frame=1, hint=cee.LEFT,
             walkable_guidance=True),
        _row(condition="annotated", arm="B", session="s", frame=1, hint=cee.UNKNOWN,
             walkable_guidance=False),
        _row(condition="annotated", arm="C", session="s", frame=1, hint=cee.LEFT,
             walkable_guidance=True),
    ]


def test_action_flips_compare_each_arm_with_a_on_the_same_frame():
    rows = cee.compute_action_flips(_aligned_rows())
    by = {(r["arm"], r["frame"]): r for r in rows}
    assert by[("A", 0)]["action_flip_vs_A"] is False
    assert by[("B", 0)]["action_flip_vs_A"] is True
    assert by[("B", 0)]["action_flip_pair"] == "STRAIGHT->STOP"
    assert by[("C", 0)]["action_flip_vs_A"] is False
    assert by[("B", 1)]["action_flip_pair"] == "LEFT->UNKNOWN"
    assert by[("C", 1)]["action_flip_vs_A"] is False


def test_action_flip_summary_pairs():
    rows = cee.compute_action_flips(_aligned_rows())
    summary, per_session = cee.summarize(rows, set(), conditions=("annotated",))
    sab = summary["annotated/B"]
    assert sab["action_flips"]["frames"] == 2
    assert sab["action_flips"]["rate"] == pytest.approx(1.0)
    assert sab["action_flips"]["pairs"] == {"STRAIGHT->STOP": 1, "LEFT->UNKNOWN": 1}
    assert summary["annotated/A"]["action_flips"]["pairs"] == {}
    assert per_session["annotated/B/s"]["action_flips"]["frames"] == 2


# ---------------------------------------------------------------------------
# Summary aggregation: frozen + amendment keys, explicit dual denominators
# ---------------------------------------------------------------------------
def test_core_summary_frozen_and_amendment_key_structure():
    frozen = {"frames", "hint_counts", "false_free", "false_free_pr15_gate",
              "false_stop", "unknown", "coverage", "guidance_coverage",
              "danger_handling", "pr15_23", "action_flips", "events", "evidence"}
    amendment = {"false_free_any", "false_stop_any", "danger_handling_ground",
                 "danger_handling_any"}
    s = cee.core_summary([_row()], set())
    assert frozen <= set(s)
    assert amendment <= set(s)
    assert set(s["hint_counts"]) == set(cee.COMMANDS)
    for key in ("danger_handling", "danger_handling_ground", "danger_handling_any"):
        assert {"danger_frames", "stop", "unknown", "walkable",
                "blocking_recall"} <= set(s[key])


def test_core_summary_uses_the_specified_dual_denominators():
    rows = [
        # ground + any danger, walkable guidance: primary and secondary false-free
        _row(frame=0, hint=cee.STRAIGHT, danger_ground=True, danger_any=True,
             gt_danger_swept=True, gt_danger_pts_swept=8, false_free=True,
             false_free_any=True, walkable_guidance=True, pr15_event=True,
             pr15_road_path_band=20),
        # ground + any danger -> UNKNOWN
        _row(frame=1, hint=cee.UNKNOWN, danger_ground=True, danger_any=True,
             gt_danger_swept=True, gt_danger_pts_swept=8, walkable_guidance=False,
             unknown_on_danger=True, unknown_on_danger_ground=True,
             unknown_on_danger_any=True),
        # ground + any danger -> STOP
        _row(frame=2, hint=cee.STOP, danger_ground=True, danger_any=True,
             gt_danger_swept=True, gt_danger_pts_swept=8, walkable_guidance=False,
             correct_block=True, correct_block_ground=True, correct_block_any=True),
        # object-occupancy-only danger (any) -> STOP: FALSE STOP under the primary view,
        # NOT a false stop under the secondary view (dual reporting).
        _row(frame=3, hint=cee.STOP, danger_ground=False, danger_any=True,
             gt_walk_pts_swept=10, walkable_guidance=False, false_stop=True,
             false_stop_ground=True, coverage=0.4),
        # no danger, GT walkable -> UNKNOWN
        _row(frame=4, hint=cee.UNKNOWN, gt_walk_pts_swept=10, walkable_guidance=False,
             unknown_when_gt_walkable=True, coverage=0.8),
        # no danger, no GT walkable -> LEFT
        _row(frame=5, hint=cee.LEFT, walkable_guidance=True, coverage=0.3),
    ]
    s = cee.core_summary(rows, {("s", 0), ("s", 5)})
    assert s["frames"] == 6
    assert s["danger_handling_ground"]["danger_frames"] == 3
    assert s["danger_handling_any"]["danger_frames"] == 4
    # PRIMARY ground view.
    assert s["false_free"] == {"numerator": 1, "denominator": 3,
                               "rate": pytest.approx(1 / 3)}
    # SECONDARY any view.
    assert s["false_free_any"] == {"numerator": 1, "denominator": 4,
                                   "rate": pytest.approx(1 / 4)}
    # PR #15 gate denominator = pr15_event frames.
    assert s["false_free_pr15_gate"] == {"numerator": 1, "denominator": 1,
                                         "rate": pytest.approx(1.0)}
    # false-stop denominators differ: ground view includes the object-only danger frame.
    assert s["false_stop"] == {"numerator": 1, "denominator": 2,
                               "rate": pytest.approx(0.5)}
    assert s["false_stop_any"] == {"numerator": 0, "denominator": 1,
                                   "rate": pytest.approx(0.0)}
    assert s["unknown"] == {"numerator": 2, "denominator": 6,
                            "rate": pytest.approx(1 / 3)}
    # coverage: >= min_coverage (0.5) over all frames; mean over all frames.
    assert s["coverage"]["frames_ge_min_coverage"] == 4
    assert s["coverage"]["frames"] == 6
    assert s["coverage"]["rate"] == pytest.approx(4 / 6)
    assert s["coverage"]["mean_coverage"] == pytest.approx((0.6 + 0.6 + 0.6 + 0.4 + 0.8
                                                            + 0.3) / 6)
    assert s["guidance_coverage"] == {"numerator": 2, "denominator": 6,
                                      "rate": pytest.approx(1 / 3)}
    assert s["danger_handling_ground"]["stop"] == 1
    assert s["danger_handling_ground"]["unknown"] == 1
    assert s["danger_handling_ground"]["walkable"] == 1
    assert s["danger_handling_ground"]["blocking_recall"] == pytest.approx(2 / 3)
    assert s["danger_handling_any"]["blocking_recall"] == pytest.approx(2 / 4)
    # Legacy key is the PRIMARY ground view.
    assert s["danger_handling"] == s["danger_handling_ground"]
    # PR #15 23-frame reporting label.
    assert s["pr15_23"] == {"frames": 2, "walkable_guidance": 2, "stop": 0,
                            "unknown": 0}
    assert s["events"]["road_in_path_ignored"] == 1      # STRAIGHT + road event band
    assert s["events"]["unknown_when_gt_walkable"] == 1
    assert s["events"]["pr15_event_frames"] == 1
    assert s["events"]["danger_ground_frames"] == 3
    assert s["events"]["danger_any_frames"] == 4
    assert s["hint_counts"][cee.STOP] == 2
    assert s["hint_counts"][cee.UNKNOWN] == 2


def test_core_summary_handles_empty_slice_without_division_errors():
    s = cee.core_summary([], set())
    assert s["frames"] == 0
    assert s["false_free"]["rate"] is None
    assert s["false_free_any"]["rate"] is None
    assert s["false_stop"]["rate"] is None
    assert s["false_stop_any"]["rate"] is None
    assert s["unknown"]["rate"] is None
    assert s["coverage"]["mean_coverage"] is None
    assert s["danger_handling_ground"]["blocking_recall"] is None
    assert s["danger_handling_any"]["blocking_recall"] is None


def test_summarize_keys_for_all_conditions_and_arms_and_sessions():
    rows = []
    for condition in cee.CONDITIONS:
        for arm in cee.ARMS:
            for frame in (0, 1):
                rows.append(_row(condition=condition, arm=arm, session="s1", frame=frame,
                                 hazard_points=3 if arm != "A" else 0,
                                 hazard_bins=1 if arm != "A" else 0))
    summary, per_session = cee.summarize(rows, set())
    assert set(summary) == {f"{c}/{a}" for c in cee.CONDITIONS for a in cee.ARMS}
    assert set(per_session) == {f"{c}/{a}/s1" for c in cee.CONDITIONS for a in cee.ARMS}
    assert summary["production/B"]["frames"] == 2
    assert per_session["annotated/C/s1"]["frames"] == 2
    # Evidence-channel totals are reported per condition/arm (A stays 0).
    assert summary["annotated/A"]["evidence"]["hazard_points_total"] == 0
    assert summary["annotated/B"]["evidence"]["hazard_points_total"] == 6
    assert summary["annotated/B"]["evidence"]["frames_with_blocking_evidence"] == 2


# ---------------------------------------------------------------------------
# Row construction / evidence channels
# ---------------------------------------------------------------------------
def test_make_row_copies_gt_reference_and_derives_flags():
    hint = SimpleNamespace(
        hint=cee.STOP, reason="hazard_in_path", coverage=0.75, n_points=123,
        walkable_offset_m=0.1, yaw_error_rad=math.radians(3.0),
        temporal_streak=3, hazard_points=12, hazard_bins=2,
        non_walkable_points=0, non_walkable_bins=0, unknown_points=1, unknown_bins=0,
        walkable_swept_points=40)
    gt_ref = {
        "plane_ok": True, "gt_walk_pts_swept": 41, "gt_danger_ground_pts": 8,
        "gt_danger_obstacle_pts": 0, "gt_danger_any_pts": 9, "gt_danger_pts_swept": 9,
        "danger_ground": True, "danger_any": True, "gt_danger_swept": True,
        "pr15_road_path_band": 0, "pr15_curb_path_band": 6, "pr15_event": True,
        "gt_hint_A_oracle": cee.STRAIGHT,
    }
    row = cee.make_row("production", "C", "sess", 7, hint, calib_ok=True,
                       calib_reason="", yaw_err_deg=2.5, rotation_only=False,
                       gt_ref=gt_ref, cfg=cee.SW_CFG)
    assert row["condition"] == "production" and row["arm"] == "C"
    assert row["frame"] == 7 and row["hint"] == cee.STOP
    assert row["coverage"] == 0.75 and row["n_points"] == 123
    assert row["yaw_correction_deg"] == pytest.approx(3.0)
    assert row["calib_ok"] is True
    assert row["temporal_streak"] == 3 and row["hazard_bins"] == 2
    assert row["danger_ground"] is True and row["danger_any"] is True
    assert row["gt_danger_ground_pts"] == 8 and row["gt_danger_any_pts"] == 9
    assert row["correct_block"] is True and row["correct_block_ground"] is True
    assert row["correct_block_any"] is True and row["false_free"] is False
    assert row["pr15_event"] is True and row["pr15_23"] is False
    assert row["action_flip_vs_A"] is False and row["action_flip_pair"] == ""


def test_no_geometry_row_is_unknown_with_zero_channels():
    row = cee.make_no_geometry_row("annotated", "B", "sess", 3, calib_ok=False,
                                   calib_reason="no_gt_plane", yaw_err_deg=1.0,
                                   rotation_only=False, gt_ref=cee.empty_gt_reference(),
                                   cfg=cee.SW_CFG)
    assert row["hint"] == cee.UNKNOWN and row["reason"] == "no_metric_depth"
    assert row["n_points"] == 0 and row["coverage"] == 0.0
    assert row["gt_plane_ok"] is False
    assert row["danger_ground"] is False and row["danger_any"] is False
    assert row["unknown_when_gt_walkable"] is False
    for name in cee._EVIDENCE_FIELDS:
        assert row[name] == 0


# ---------------------------------------------------------------------------
# CLI contract / output columns
# ---------------------------------------------------------------------------
def test_parse_args_defaults_and_overrides():
    args = cee.parse_args(["--v5-checkpoint", "/tmp/v5.pt"])
    assert args.conditions == list(cee.CONDITIONS)
    assert args.temporal_confirm_frames == 3
    assert args.max_width == 640
    assert args.limit_per_session == 0
    assert args.seed == 0
    assert args.sessions is None and args.device is None

    args = cee.parse_args([
        "--v5-checkpoint", "/tmp/v5.pt", "--data-root", "/tmp/data",
        "--sessions", "s1", "s2", "--conditions", "production",
        "--temporal-confirm-frames", "5", "--max-width", "320",
        "--limit-per-session", "2", "--device", "cpu", "--seed", "7",
        "--out", "/tmp/out"])
    assert args.sessions == ["s1", "s2"]
    assert args.conditions == ["production"]
    assert args.temporal_confirm_frames == 5
    assert args.max_width == 320
    assert args.limit_per_session == 2
    assert args.device == "cpu"
    assert args.seed == 7
    assert args.out == "/tmp/out"


def test_per_frame_fields_cover_the_documented_schema():
    required = {
        "condition", "arm", "session", "frame", "hint", "reason", "coverage",
        "n_points", "coverage_bins_ok", "calib_ok", "calib_reason", "yaw_err_deg",
        "walkable_offset_m", "gt_walk_pts_swept",
        "gt_danger_ground_pts", "gt_danger_any_pts", "gt_danger_pts_swept",
        "danger_ground", "danger_any", "gt_danger_swept",
        "pr15_road_path_band", "pr15_curb_path_band", "pr15_event", "pr15_23",
        "false_free", "false_free_any", "correct_block", "correct_block_ground",
        "correct_block_any", "unknown_on_danger", "unknown_on_danger_ground",
        "unknown_on_danger_any", "false_stop", "false_stop_ground", "false_stop_any",
        "unknown_when_gt_walkable", "walkable_guidance", "gt_hint_A_oracle",
        "hazard_points", "non_walkable_points", "unknown_points",
        "walkable_swept_points", "hazard_bins", "non_walkable_bins", "unknown_bins",
        "temporal_streak", "action_flip_vs_A", "action_flip_pair",
    }
    assert required <= set(cee.PER_FRAME_FIELDS)
    assert len(cee.PER_FRAME_FIELDS) == len(set(cee.PER_FRAME_FIELDS))


def test_arm_configs_match_spec():
    cfgs = cee._arm_configs(3)
    assert set(cfgs) == {"A", "B", "C"}
    try:
        assert cfgs["B"].temporal_confirm_frames == 0
        assert cfgs["C"].temporal_confirm_frames == 3
    except AttributeError:
        pytest.skip("evidence-core ShadowWalkConfig fields not on disk yet")
