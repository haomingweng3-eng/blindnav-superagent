"""Tests for the negative-evidence fusion report tool (``corridor_evidence_report``).

No model inference, no torch, no real SANPO data: the metric derivations are exercised on
synthetic ``per_frame`` tables, and the failure-overlay path is exercised end-to-end on a
tiny synthetic SANPO-Real directory layout (raw RGB + GT depth/segmentation + poses) under
pytest's ``tmp_path``.  The files the tool writes are contract-checked against the frozen
spec §5 (tables, 23-frame ledger denominator, PR body sections, deterministic balanced
failure selection, readable overlays).
"""
import csv
import gzip
import json
import math
import os
import sys

import cv2
import numpy as np
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "tools")))

import corridor_evidence_report as cer  # noqa: E402

SESSIONS = ("sessAAAA0000", "sessBBBB1111")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
def _raw_row(**kw):
    """One canonical per_frame row (all frozen fields present unless overridden)."""
    base = {
        "condition": "annotated", "arm": "A", "session": SESSIONS[0], "frame": 0,
        "hint": cer.STRAIGHT, "reason": "centred", "coverage": 0.6, "n_points": 100,
        "walkable_offset_m": 0.0, "yaw_err_deg": 0.0, "calib_ok": True, "calib_reason": "",
        "gt_walk_pts_swept": 100, "danger_ground": False, "danger_any": False,
        "gt_danger_ground_pts": 0, "gt_danger_any_pts": 0,
        "pr15_road_path_band": 0, "pr15_curb_path_band": 0, "pr15_event": False,
        "pr15_23": False, "false_free": False, "false_free_any": False,
        "correct_block": False, "unknown_on_danger": False, "false_stop": False,
        "false_stop_any": False, "unknown_when_gt_walkable": False,
        "walkable_guidance": True,
        "hazard_points": 0, "non_walkable_points": 0, "unknown_points": 0,
        "walkable_swept_points": 100, "hazard_bins": 0, "non_walkable_bins": 0,
        "unknown_bins": 0, "temporal_streak": 0,
    }
    base.update(kw)
    return base


def _norm(rows):
    return cer.normalize_rows(rows)


def _standard_rows(n_frames=30):
    """Deterministic full table: 2 conditions x A/B/C x 2 sessions x n_frames.

    The fixed PR #15 23-frame set is exactly session 0 frames 0..22 (pr15_event on the
    annotated/A rows, pr15_23 flagged on every condition/arm row of those frames, as the
    real eval writes it).  Danger / failure patterns are spread over both sessions.
    """
    rows = []
    for cond in ("annotated", "production"):
        for arm in ("A", "B", "C"):
            for sess in SESSIONS:
                for frame in range(n_frames):
                    danger_ground = (frame % 6 == 0)          # GT excluded-ground danger
                    danger_any = danger_ground or (frame % 9 == 3)   # + object occupancy
                    hint = cer.STRAIGHT
                    if danger_ground and arm in ("B", "C"):
                        hint = (cer.STRAIGHT if frame % 24 == 6
                                else (cer.STOP if frame % 12 == 0 else cer.UNKNOWN))
                    elif not danger_ground and arm in ("B", "C") and frame % 5 == 0:
                        hint = cer.STOP
                    elif not danger_ground and arm == "A" and frame % 7 == 0:
                        hint = cer.UNKNOWN
                    in23 = (sess == SESSIONS[0] and frame < 23)
                    is_pr15_event = in23 and cond == "annotated" and arm == "A"
                    walkable_guidance = hint in cer.GUIDANCE_COMMANDS
                    rows.append(_raw_row(
                        condition=cond, arm=arm, session=sess, frame=frame, hint=hint,
                        reason=("centred" if hint == cer.STRAIGHT else hint.lower()),
                        danger_ground=danger_ground, danger_any=danger_any,
                        gt_danger_ground_pts=(10 if danger_ground else 0),
                        gt_danger_any_pts=(10 if danger_any else 0),
                        gt_walk_pts_swept=(0 if danger_ground else 100),
                        pr15_event=is_pr15_event,
                        pr15_23=in23,
                        walkable_guidance=walkable_guidance,
                        false_free=bool(danger_ground and walkable_guidance),
                        false_free_any=bool(danger_any and walkable_guidance),
                        correct_block=bool(danger_ground and hint == cer.STOP),
                        unknown_on_danger=bool(danger_ground and hint == cer.UNKNOWN),
                        false_stop=bool((not danger_ground) and hint == cer.STOP),
                        false_stop_any=bool((not danger_any) and hint == cer.STOP),
                        unknown_when_gt_walkable=bool((not danger_ground)
                                                      and hint == cer.UNKNOWN),
                        coverage=(0.9 if hint == cer.STRAIGHT else 0.2),
                    ))
    return rows


# ---------------------------------------------------------------------------
# Row normalisation / derived flags
# ---------------------------------------------------------------------------
def _flagless_row(**kw):
    """Row with the derived flag columns omitted (the eval may only write raw GT counts)."""
    raw = _raw_row(**kw)
    for key in ("false_free", "false_free_any", "correct_block", "unknown_on_danger",
                "false_stop", "false_stop_any", "unknown_when_gt_walkable",
                "walkable_guidance"):
        raw.pop(key, None)
    return raw


def _points_only_row(**kw):
    """Row with the derived flag/boolean columns omitted so only GT point counts remain."""
    raw = _flagless_row(**kw)
    raw.pop("danger_ground", None)
    raw.pop("danger_any", None)
    return raw


def _ground_danger_only_row(**kw):
    """Row with ONLY the primary ground-danger point count (no any-danger field at all)."""
    raw = _points_only_row(**kw)
    raw.pop("gt_danger_any_pts", None)
    return raw


def test_derive_flags_when_only_gt_counts_are_present():
    r = cer.normalize_row(_flagless_row(hint=cer.STRAIGHT, danger_ground=True,
                                        danger_any=True, gt_walk_pts_swept=80))
    assert r["walkable_guidance"] is True
    assert r["false_free"] is True
    assert r["correct_block"] is False
    assert r["unknown_on_danger"] is False
    assert r["false_stop"] is False

    r = cer.normalize_row(_flagless_row(hint=cer.STOP, danger_ground=False,
                                  gt_walk_pts_swept=80))
    assert r["false_stop"] is True
    assert r["false_free"] is False

    r = cer.normalize_row(_flagless_row(hint=cer.UNKNOWN, danger_ground=False,
                                  gt_walk_pts_swept=80))
    assert r["unknown_when_gt_walkable"] is True
    assert r["walkable_guidance"] is False

    # Below the >= 5 GT-walkable-points event threshold: neither false-stop nor UNKNOWN loss.
    r = cer.normalize_row(_flagless_row(hint=cer.STOP, danger_ground=False,
                                  gt_walk_pts_swept=4))
    assert r["false_stop"] is False
    assert cer.normalize_row(_flagless_row(hint=cer.UNKNOWN, danger_ground=False,
                                     gt_walk_pts_swept=4))["unknown_when_gt_walkable"] is False


def test_explicit_flag_columns_win_over_derivation():
    r = cer.normalize_row(_raw_row(hint=cer.STRAIGHT, danger_ground=True,
                                  danger_any=True, false_free=False))
    assert r["false_free"] is False
    r = cer.normalize_row(_raw_row(hint=cer.UNKNOWN, danger_ground=False,
                                  danger_any=False, gt_walk_pts_swept=100,
                                  unknown_when_gt_walkable=False))
    assert r["unknown_when_gt_walkable"] is False


def test_normalize_rejects_rows_missing_condition_or_arm():
    bad = _raw_row()
    bad["condition"] = ""
    with pytest.raises(ValueError):
        cer.normalize_rows([bad])


# ---------------------------------------------------------------------------
# Metric aggregation: numerator/denominator for every table metric
# ---------------------------------------------------------------------------
def test_summarize_reports_explicit_numerators_and_denominators():
    rows = _norm([
        _raw_row(frame=0, hint=cer.STRAIGHT, danger_ground=True, danger_any=True, gt_walk_pts_swept=0,
                 false_free=True, false_free_any=True, walkable_guidance=True),
        _raw_row(frame=1, hint=cer.STOP, danger_ground=True, danger_any=True, gt_walk_pts_swept=0,
                 correct_block=True, walkable_guidance=False),
        _raw_row(frame=2, hint=cer.STOP, danger_ground=False, danger_any=False, gt_walk_pts_swept=80,
                 false_stop=True, false_stop_any=True, walkable_guidance=False, coverage=0.2),
        _raw_row(frame=3, hint=cer.UNKNOWN, danger_ground=False, danger_any=False, gt_walk_pts_swept=80,
                 unknown_when_gt_walkable=True, walkable_guidance=False, coverage=0.9),
        _raw_row(frame=4, hint=cer.STRAIGHT, danger_ground=False, danger_any=False, gt_walk_pts_swept=10,
                 coverage=0.7),
    ])
    m = cer.summarize_subset(rows)

    # danger denominator = 2 GT-danger frames; false-free admitted 1.
    assert m["false_free"] == {"numerator": 1, "denominator": 2, "rate": 0.5,
                               "denominator_is_danger_frames": True,
                               "denominator_absent_no_gt_danger": False}
    # false-stop denominator = no-danger frames with >=5 GT walkable swept points (3).
    assert m["false_stop"] == {"numerator": 1, "denominator": 3, "rate": pytest.approx(1 / 3)}
    assert m["unknown"] == {"numerator": 1, "denominator": 5, "rate": 0.2}
    assert m["guidance_coverage"] == {"numerator": 2, "denominator": 5, "rate": 0.4}
    cov = m["coverage"]
    assert cov["frames_ge_min_coverage"] == 4 and cov["frames"] == 5
    assert cov["rate"] == pytest.approx(0.8)
    assert cov["mean_coverage"] == pytest.approx((0.6 + 0.6 + 0.2 + 0.9 + 0.7) / 5)
    dh = m["danger_handling"]
    assert dh["danger_frames"] == 2 and dh["stop"] == 1 and dh["unknown"] == 0
    assert dh["walkable"] == 1 and dh["blocking_recall"] == pytest.approx(0.5)
    assert m["frames"] == 5
    # Amendment v1.1: the secondary any-negative dimension falls back to danger_ground here
    # (no object occupancy in this fixture), so both denominators are 2.
    assert m["danger_handling_any"] == {
        "danger_frames": 2, "stop": 1, "unknown": 0, "walkable": 1,
        "blocking_recall": pytest.approx(0.5),
        "stop_rate": pytest.approx(0.5), "unknown_rate": 0.0,
    }
    assert m["false_free_any"] == {"numerator": 1, "denominator": 2, "rate": 0.5,
                                   "denominator_absent_no_gt_danger": False}
    assert m["false_stop_any"] == {"numerator": 1, "denominator": 3,
                                   "rate": pytest.approx(1 / 3)}


def test_summarize_false_free_pr15_gate_and_road_curb_ignored():
    rows = _norm([
        _raw_row(frame=0, hint=cer.STRAIGHT, danger_ground=True, danger_any=True, pr15_event=True,
                 pr15_road_path_band=12, pr15_curb_path_band=0, false_free=True),
        _raw_row(frame=1, hint=cer.LEFT, danger_ground=True, danger_any=True, pr15_event=True,
                 pr15_road_path_band=0, pr15_curb_path_band=8, false_free=True),
        _raw_row(frame=2, hint=cer.STRAIGHT, danger_ground=False, danger_any=False, pr15_event=False,
                 pr15_road_path_band=9, pr15_curb_path_band=6),
        _raw_row(frame=3, hint=cer.STOP, danger_ground=True, danger_any=True, pr15_event=False,
                 pr15_road_path_band=0, pr15_curb_path_band=0),
    ])
    m = cer.summarize_subset(rows)
    # PR #15 gate: 2 false-free / 2 PR #15 event frames.
    assert m["false_free_pr15_gate"] == {"numerator": 2, "denominator": 2, "rate": 1.0}
    # road/curb ignored use the PR #15 event-frame denominators (>= 5 points).
    assert m["road_in_path_ignored"] == {"numerator": 2, "denominator": 2, "rate": 1.0}
    assert m["curb_in_path_ignored"] == {"numerator": 1, "denominator": 2, "rate": 0.5}
    assert m["event_frames"]["pr15_event"] == 2
    assert m["event_frames"]["pr15_road_path_band"] == 2
    assert m["event_frames"]["pr15_curb_path_band"] == 2


def test_action_flip_pairs_are_frame_matched():
    a_rows = _norm([
        _raw_row(arm="A", frame=0, hint=cer.STRAIGHT),
        _raw_row(arm="A", frame=1, hint=cer.STRAIGHT),
        _raw_row(arm="A", frame=2, hint=cer.LEFT),
        _raw_row(arm="A", frame=3, hint=cer.LEFT),
    ])
    b_rows = _norm([
        _raw_row(arm="B", frame=0, hint=cer.STRAIGHT),
        _raw_row(arm="B", frame=1, hint=cer.STOP),
        _raw_row(arm="B", frame=2, hint=cer.LEFT),
        _raw_row(arm="B", frame=3, hint=cer.STRAIGHT),
    ])
    flips = cer.action_flips(b_rows, a_rows)
    assert flips["numerator"] == 2
    assert flips["denominator"] == 4
    assert flips["rate"] == 0.5
    assert flips["pairs"] == {"LEFT->STRAIGHT": 1, "STRAIGHT->STOP": 1}
    # A row for a frame missing from the baseline is not joined.
    extra = _norm([_raw_row(arm="B", frame=99, hint=cer.STOP)])
    assert cer.action_flips(extra, a_rows)["denominator"] == 0


def test_aggregate_action_flips_compare_each_arm_with_arm_a():
    rows = _norm([
        _raw_row(arm="A", frame=0, hint=cer.STRAIGHT),
        _raw_row(arm="A", frame=1, hint=cer.STRAIGHT),
        _raw_row(arm="B", frame=0, hint=cer.STOP),
        _raw_row(arm="B", frame=1, hint=cer.STRAIGHT),
        _raw_row(arm="C", frame=0, hint=cer.UNKNOWN),
        _raw_row(arm="C", frame=1, hint=cer.LEFT),
    ])
    agg = cer.aggregate(rows)
    b = agg["overall"][("annotated", "B")]["action_flips"]
    c = agg["overall"][("annotated", "C")]["action_flips"]
    assert (b["numerator"], b["denominator"]) == (1, 2)
    assert (c["numerator"], c["denominator"]) == (2, 2)
    assert agg["overall"][("annotated", "A")]["action_flips"]["numerator"] == 0


# ---------------------------------------------------------------------------
# PR #15 fixed 23-frame set (reporting label) and the remain table
# ---------------------------------------------------------------------------
def _twenty_three_rows():
    rows = []
    for sess in SESSIONS:
        for frame in range(25):
            for cond in ("annotated", "production"):
                for arm in ("A", "B", "C"):
                    in23 = sess == SESSIONS[0] and frame < 23
                    hint = cer.STRAIGHT
                    if in23:
                        if arm == "B":
                            hint = (cer.STOP if frame < 2
                                    else (cer.UNKNOWN if frame < 5 else cer.STRAIGHT))
                        elif arm == "C":
                            hint = (cer.STOP if frame == 0
                                    else (cer.UNKNOWN if frame < 3 else cer.STRAIGHT))
                    rows.append(_raw_row(
                        condition=cond, arm=arm, session=sess, frame=frame, hint=hint,
                        pr15_event=in23 and cond == "annotated" and arm == "A",
                        pr15_23=in23,
                        danger_ground=(frame == 24), danger_any=(frame == 24),
                        walkable_guidance=hint in cer.GUIDANCE_COMMANDS,
                    ))
    return rows


def test_pr15_23_set_is_recovered_from_explicit_label_and_derived_rows():
    rows = _norm(_twenty_three_rows())
    explicit = cer.pr15_23_frame_set(rows)
    assert len(explicit) == 23
    assert explicit == sorted((SESSIONS[0], f) for f in range(23))

    # Without the explicit label column the set is recomputed from annotated/A pr15_event rows.
    derived_rows = [dict(r) for r in rows]
    for r in derived_rows:
        r["pr15_23"] = False
    derived = cer.pr15_23_frame_set(derived_rows)
    assert derived == explicit


def test_23_frame_remain_table_uses_denominator_23_and_all_arms():
    rows = _norm(_twenty_three_rows())
    keys = cer.pr15_23_frame_set(rows)
    agg = cer.aggregate(rows, keys)
    for cond in ("annotated", "production"):
        a = agg["overall"][(cond, "A")]["pr15_23"]
        b = agg["overall"][(cond, "B")]["pr15_23"]
        c = agg["overall"][(cond, "C")]["pr15_23"]
        assert a["frames"] == b["frames"] == c["frames"] == 23
        assert a == {**a, "walkable": 23, "stop": 0, "unknown": 0, "matched": 23, "missing": 0}
        assert (b["walkable"], b["stop"], b["unknown"]) == (18, 2, 3)
        assert (c["walkable"], c["stop"], c["unknown"]) == (20, 1, 2)
    # denominator 23 even when a session/frame slice is missing rows.
    subset = [r for r in rows if r["session"] == SESSIONS[0] and r["frame"] < 5]
    m = cer.summarize_subset(subset, keys)
    assert m["pr15_23"]["frames"] == 23
    assert m["pr15_23"]["missing"] == 18


def test_contract_warnings_flag_a_wrong_23_frame_count():
    rows = _norm([_raw_row(frame=f, pr15_23=(f < 3)) for f in range(3)])
    warnings = cer.contract_warnings(rows, cer.pr15_23_frame_set(rows))
    assert any("23-frame set recovered **3**" in w for w in warnings)


# ---------------------------------------------------------------------------
# Mechanical failure-layer classifier
# ---------------------------------------------------------------------------
def _layer_row(**kw):
    return cer.normalize_row(_raw_row(**kw))


def test_layer_classifier_segmentation_geometry_decision_temporal():
    # production: GT danger present but v5 supplies no negative-evidence points -> segmentation
    assert cer.classify_failure_layer(_layer_row(
        condition="production", hint=cer.STRAIGHT, danger_ground=True, danger_any=True,
        hazard_points=0, non_walkable_points=0)) == "segmentation"
    # annotated frames can never be segmentation; with GT oracle geometry supplied, GT danger
    # that the binned counters do not see is a decision-layer representation loss.
    assert cer.classify_failure_layer(_layer_row(
        condition="annotated", hint=cer.STRAIGHT, danger_ground=True, danger_any=True,
        hazard_points=0, non_walkable_points=0)) == "decision"
    # negative evidence present in the swept body but below support/gating -> decision
    assert cer.classify_failure_layer(_layer_row(
        condition="production", hint=cer.STRAIGHT, danger_ground=True, danger_any=True,
        hazard_points=3)) == "decision"
    assert cer.classify_failure_layer(_layer_row(
        condition="annotated", hint=cer.STRAIGHT, danger_ground=True, danger_any=True,
        non_walkable_points=12)) == "decision"
    # geometry: calibration not ok / no metric depth / GT walkable not projected
    assert cer.classify_failure_layer(_layer_row(
        calib_ok=False, hint=cer.UNKNOWN, gt_walk_pts_swept=100)) == "geometry"
    assert cer.classify_failure_layer(_layer_row(
        calib_ok=True, calib_reason="no_metric_depth",
        hint=cer.UNKNOWN, gt_walk_pts_swept=100)) == "geometry"
    assert cer.classify_failure_layer(_layer_row(
        calib_ok=True, hint=cer.UNKNOWN, n_points=3, gt_walk_pts_swept=100)) == "geometry"
    # temporal: arm C only, blocking evidence present but unconfirmed
    assert cer.classify_failure_layer(_layer_row(
        arm="C", hint=cer.UNKNOWN, reason="hazard_confirming",
        hazard_points=12)) == "temporal"
    assert cer.classify_failure_layer(_layer_row(
        arm="C", hint=cer.UNKNOWN, reason="non_walkable_confirming",
        non_walkable_points=12)) == "temporal"
    assert cer.classify_failure_layer(_layer_row(
        arm="C", hint=cer.UNKNOWN, reason="evidence_conflict",
        hazard_points=2, temporal_streak=2)) == "temporal"
    # no GT danger and no negative evidence: the decision layer mis-fired
    assert cer.classify_failure_layer(_layer_row(
        condition="annotated", hint=cer.STOP, danger_ground=False, danger_any=False,
        reason="off_corridor")) == "decision"


def test_layer_classifier_never_segmentation_for_annotated():
    for hint in (cer.STRAIGHT, cer.LEFT, cer.RIGHT, cer.STOP, cer.UNKNOWN):
        layer = cer.classify_failure_layer(_layer_row(
            condition="annotated", hint=hint, danger_ground=True, danger_any=True,
            hazard_points=0, non_walkable_points=0, gt_walk_pts_swept=100))
        assert layer in ("geometry", "decision", "temporal")
        assert layer != "segmentation"


# ---------------------------------------------------------------------------
# Deterministic, balanced failure-frame selection
# ---------------------------------------------------------------------------
def _failure_rows():
    rows = []
    for cond in ("annotated", "production"):
        for i in range(6):
            # false-free frames of final arm B
            rows.append(_raw_row(condition=cond, arm="B", session=SESSIONS[i % 2], frame=i,
                                 hint=cer.STRAIGHT, danger_ground=True, danger_any=True,
                                 gt_walk_pts_swept=0, false_free=True, false_free_any=True,
                                 hazard_points=1, non_walkable_points=2))
        for i in range(6):
            # false-stop frames of final arm C
            rows.append(_raw_row(condition=cond, arm="C", session=SESSIONS[i % 2],
                                 frame=10 + i, hint=cer.STOP, danger_ground=False,
                                 danger_any=False, gt_walk_pts_swept=80,
                                 false_stop=True, false_stop_any=True))
        for i in range(6):
            # unknown-when-GT-walkable frames of final arm C
            rows.append(_raw_row(condition=cond, arm="C", session=SESSIONS[(i + 1) % 2],
                                 frame=20 + i, hint=cer.UNKNOWN, danger_ground=False,
                                 danger_any=False, gt_walk_pts_swept=80,
                                 unknown_when_gt_walkable=True, temporal_streak=1))
        for i in range(6):
            # object-occupancy-only any-negative danger (secondary dimension): no GT
            # excluded-ground danger, but GT object occupancy in the swept body.
            rows.append(_raw_row(condition=cond, arm="B", session=SESSIONS[i % 2],
                                 frame=30 + i, hint=cer.STRAIGHT, danger_ground=False,
                                 danger_any=True, gt_walk_pts_swept=100,
                                 false_free_any=True, hazard_points=4,
                                 hazard_bins=0, walkable_guidance=True))
    return _norm(rows)


def test_failure_selection_is_deterministic_balanced_and_covers_conditions_kinds():
    rows = _failure_rows()
    picked_a = cer.select_failure_frames(rows, max_total=8)
    picked_b = cer.select_failure_frames(rows, max_total=8)
    assert [(p["condition"], p["session"], p["frame"]) for p in picked_a] == \
           [(p["condition"], p["session"], p["frame"]) for p in picked_b]
    assert len(picked_a) == 8
    assert {p["condition"] for p in picked_a} == {"annotated", "production"}
    assert {p["kind"] for p in picked_a} == set(cer.KIND_ORDER)
    assert len({(p["condition"], p["session"], p["frame"]) for p in picked_a}) == 8
    session_counts = {}
    for p in picked_a:
        session_counts[p["session"]] = session_counts.get(p["session"], 0) + 1
    assert max(session_counts.values()) - min(session_counts.values()) <= 2
    # arm preference: B preferred when both final arms flag the same frame
    assert all(p["row"]["arm"] in cer.FINAL_ARMS for p in picked_a)


def test_failure_selection_merges_b_and_c_on_the_same_frame():
    rows = _norm([
        _raw_row(condition="production", arm="B", frame=0, hint=cer.STOP,
                 danger_ground=False, danger_any=False, gt_walk_pts_swept=80, false_stop=True),
        _raw_row(condition="production", arm="C", frame=0, hint=cer.UNKNOWN,
                 danger_ground=False, danger_any=False, gt_walk_pts_swept=80,
                 unknown_when_gt_walkable=True),
    ])
    picked = cer.select_failure_frames(rows, max_total=5)
    assert len(picked) == 1
    assert picked[0]["frame"] == 0
    assert picked[0]["row"]["arm"] == "B"          # deterministic B-first representative
    assert picked[0]["kinds"] == ["false_stop", "unknown_when_gt_walkable"]


def test_failure_selection_respects_max_total_and_empty_input():
    assert cer.select_failure_frames([], max_total=5) == []
    rows = _norm([_raw_row(frame=f, arm="B", danger_ground=True, danger_any=True, hint=cer.STRAIGHT,
                           false_free=True)
                  for f in range(3)])
    assert len(cer.select_failure_frames(rows, max_total=2)) == 2


# ---------------------------------------------------------------------------
# Overlay geometry (pure projection / swept-body outline)
# ---------------------------------------------------------------------------
def test_project_ground_point_to_pixel_matches_expected_camera_geometry():
    K = np.array([[100.0, 0.0, 64.0], [0.0, 100.0, 48.0], [0.0, 0.0, 1.0]])
    up = np.array([0.0, -1.0, 0.0])
    h = 1.35
    u, v = cer.project_level_point_to_pixel(0.0, 3.0, K, up, h)
    assert u == pytest.approx(64.0)
    assert v == pytest.approx(48.0 + 100.0 * 1.35 / 3.0)
    u2, _ = cer.project_level_point_to_pixel(0.4, 3.0, K, up, h)
    assert u2 == pytest.approx(64.0 + 100.0 * 0.4 / 3.0)
    # Behind the camera is not projectable.
    u3, v3 = cer.project_level_point_to_pixel(0.0, -1.0, K, up, h)
    assert math.isnan(u3) and math.isnan(v3)


def test_swept_body_outline_is_a_closed_ring_with_expected_pixel_widths():
    K = np.array([[100.0, 0.0, 64.0], [0.0, 100.0, 48.0], [0.0, 0.0, 1.0]])
    up = np.array([0.0, -1.0, 0.0])
    h = 1.35
    ring = cer.swept_body_outline_pixels(K, up, h)
    assert ring.shape[1] == 2 and len(ring) >= 8
    assert np.isfinite(ring).all()
    near_u = ring[:9, 0]
    far_u = ring[9 + 9: 9 + 9 + 9, 0]           # far edge block (right edge is 9, far edge 9)
    near_span = near_u.max() - near_u.min()
    # far edge: the block of 9 points at the far distance
    far_span = far_u.max() - far_u.min() if len(far_u) else 0.0
    assert near_span > far_span > 0
    assert near_span == pytest.approx(2 * 100.0 * cer.SWEPT_HALF_WIDTH_M / cer.NEAR_M, rel=0.05)


# ---------------------------------------------------------------------------
# Consistency cross-check against the frozen summary.json contract
# ---------------------------------------------------------------------------
def _consistent_summary(agg):
    summary = {"meta": {"tool": "test"}, "summary": {}, "per_session": {}}
    for (cond, arm), m in agg["overall"].items():
        summary["summary"][f"{cond}/{arm}"] = {
            "false_free": m["false_free"],
            "false_free_any": m["false_free_any"],
            "false_free_pr15_gate": m["false_free_pr15_gate"],
            "false_stop": m["false_stop"],
            "false_stop_any": m["false_stop_any"],
            "unknown": m["unknown"],
            "coverage": m["coverage"],
            "guidance_coverage": m["guidance_coverage"],
            "danger_handling": m["danger_handling"],
            "danger_handling_any": m["danger_handling_any"],
            "pr15_23": {"frames": m["pr15_23"]["frames"],
                        "walkable_guidance": m["pr15_23"]["walkable"],
                        "stop": m["pr15_23"]["stop"],
                        "unknown": m["pr15_23"]["unknown"]},
            "action_flips": {"frames": m["action_flips"]["denominator"],
                             "rate": m["action_flips"]["rate"],
                             "pairs": m["action_flips"]["pairs"]},
        }
    for (cond, arm, sid), m in agg["per_session"].items():
        summary["per_session"][f"{cond}/{arm}/{sid}"] = {
            "frames": m["frames"], "unknown_rate": m["unknown"]["rate"]}
    return summary


def test_cross_check_accepts_a_consistent_summary_and_flags_a_mismatch():
    rows = _norm(_standard_rows())
    keys = cer.pr15_23_frame_set(rows)
    agg = cer.aggregate(rows, keys)
    summary = _consistent_summary(agg)
    assert cer.cross_check(agg, summary) == []

    bad = json.loads(json.dumps(summary))
    bad["summary"]["annotated/B"]["false_free"]["numerator"] += 7
    warnings = cer.cross_check(agg, bad)
    assert any("summary.json mismatch annotated/B.false_free.numerator" in w
               for w in warnings)
    assert cer.cross_check(agg, None) != []      # missing summary is surfaced, not hidden


# ---------------------------------------------------------------------------
# Amendment v1.1: two danger denominators side by side
# ---------------------------------------------------------------------------
def test_secondary_danger_denominator_counts_object_occupancy():
    rows = _norm([
        # GT excluded-ground danger, walkable guidance -> primary false-free
        _raw_row(frame=0, hint=cer.STRAIGHT, danger_ground=True, danger_any=True,
                 gt_danger_ground_pts=12, gt_danger_any_pts=12, gt_walk_pts_swept=0,
                 false_free=True, false_free_any=True),
        # object-occupancy-only danger, walkable guidance -> secondary false-free only
        _raw_row(frame=1, hint=cer.STRAIGHT, danger_ground=False, danger_any=True,
                 gt_danger_ground_pts=0, gt_danger_any_pts=9, gt_walk_pts_swept=100,
                 false_free=False, false_free_any=True, hazard_points=9),
        # no danger at all
        _raw_row(frame=2, hint=cer.STRAIGHT, danger_ground=False, danger_any=False,
                 gt_danger_ground_pts=0, gt_danger_any_pts=0, gt_walk_pts_swept=100,
                 false_free=False, false_free_any=False),
    ])
    m = cer.summarize_subset(rows)
    # Primary denominator is GT-ground danger only (1 frame).
    assert m["false_free"] == {"numerator": 1, "denominator": 1, "rate": 1.0,
                               "denominator_is_danger_frames": True,
                               "denominator_absent_no_gt_danger": False}
    # Secondary denominator is any-negative danger (2 frames: ground + object occupancy).
    assert m["false_free_any"] == {"numerator": 2, "denominator": 2, "rate": 1.0,
                                   "denominator_absent_no_gt_danger": False}
    dh, dh_any = m["danger_handling"], m["danger_handling_any"]
    assert dh["danger_frames"] == 1 and dh["walkable"] == 1
    assert dh_any["danger_frames"] == 2 and dh_any["walkable"] == 2
    # false-stop denominators follow the same primary/secondary split: frames 1 (object-only
    # any-danger) and 2 (no danger) are no-ground-danger, only frame 2 is no-any-danger.
    assert m["false_stop"]["denominator"] == 2
    assert m["false_stop"]["numerator"] == 0
    assert m["false_stop_any"]["denominator"] == 1
    assert m["false_stop_any"]["numerator"] == 0


def test_derived_secondary_danger_from_point_counts():
    rows = _norm([
        _points_only_row(frame=0, hint=cer.STRAIGHT, gt_danger_ground_pts=9,
                         gt_danger_any_pts=9, gt_walk_pts_swept=0),
        _points_only_row(frame=1, hint=cer.STRAIGHT, gt_danger_ground_pts=0,
                         gt_danger_any_pts=9, gt_walk_pts_swept=100),
        _points_only_row(frame=2, hint=cer.STRAIGHT, gt_danger_ground_pts=0,
                         gt_danger_any_pts=0, gt_walk_pts_swept=100),
    ])
    # >= 5 points in the swept body makes a danger frame; 0 does not.
    assert [r["danger_ground"] for r in rows] == [True, False, False]
    assert [r["danger_any"] for r in rows] == [True, True, False]
    m = cer.summarize_subset(rows)
    assert m["false_free"] == {"numerator": 1, "denominator": 1, "rate": 1.0,
                               "denominator_is_danger_frames": True,
                               "denominator_absent_no_gt_danger": False}
    assert m["false_free_any"]["numerator"] == 2 and m["false_free_any"]["denominator"] == 2


def test_contract_warns_when_danger_any_falls_back_to_ground():
    rows = _norm([_ground_danger_only_row(frame=f, hint=cer.STRAIGHT,
                                          gt_danger_ground_pts=10) for f in range(3)])
    warnings = cer.contract_warnings(rows, cer.pr15_23_frame_set(rows))
    assert any("no `danger_any`" in w for w in warnings)
    # When the secondary column is genuinely equal on every row, the fallback is not claimed
    # but the lack of object-occupancy frames is still surfaced.
    rows2 = _norm([_raw_row(frame=f, hint=cer.STRAIGHT, danger_ground=True,
                            danger_any=True) for f in range(3)])
    warnings2 = cer.contract_warnings(rows2, cer.pr15_23_frame_set(rows2))
    assert any("no object/" in w for w in warnings2)


def test_cross_check_detects_a_secondary_danger_mismatch():
    rows = _norm(_standard_rows())
    keys = cer.pr15_23_frame_set(rows)
    agg = cer.aggregate(rows, keys)
    summary = _consistent_summary(agg)
    assert cer.cross_check(agg, summary) == []
    bad = json.loads(json.dumps(summary))
    bad["summary"]["annotated/B"]["danger_handling_any"]["stop"] += 5
    warnings = cer.cross_check(agg, bad)
    assert any("danger_handling_any" in w for w in warnings)


# ---------------------------------------------------------------------------
# Report tables / delta paragraph / PR body skeleton
# ---------------------------------------------------------------------------
def test_tables_md_and_csv_contain_every_required_metric():
    rows = _norm(_standard_rows())
    keys = cer.pr15_23_frame_set(rows)
    agg = cer.aggregate(rows, keys)
    meta = cer.build_meta(rows, None, "/tmp/eval")
    md = cer.build_tables_md(agg, meta, [], keys)
    assert "false-free" in md and "false-stop" in md
    assert "danger blocking recall" in md
    assert "23-frame" in md and "denominator **23**" in md
    assert "road-in-path ignored" in md and "curb-in-path ignored" in md
    # Amendment v1.1: both danger denominators are shown side by side, never one-sided.
    assert "false-free ground n/den" in md and "false-free any n/den" in md
    assert "false-stop ground n/den" in md and "false-stop any n/den" in md
    assert "danger recall ground" in md and "danger recall any" in md
    assert "GT excluded-ground danger (PR #15 class)" in md
    assert "GT any-negative danger (incl. object/vegetation occupancy)" in md
    assert "## 2. Per-session breakdown" in md
    assert "## 3. B vs A / C vs B / C vs A" in md
    assert "B vs A" in md and "C vs B" in md and "C vs A" in md
    assert "DESKTOP_REAL_DATA" in md

    csv_rows = cer.build_tables_csv_rows(agg)
    assert csv_rows
    for metric in ("false_free", "false_free_any", "false_stop", "false_stop_any",
                   "unknown", "coverage_ge_min", "guidance_coverage", "action_flips_vs_A",
                   "danger_blocking_recall", "danger_blocking_recall_any",
                   "danger_ground_frames", "danger_any_frames", "pr15_23_walkable",
                   "road_in_path_ignored", "curb_in_path_ignored"):
        assert any(r["metric"] == metric and r["scope"] == "overall" for r in csv_rows)
    assert any(r["scope"].startswith("session:") for r in csv_rows)
    ff = [r for r in csv_rows if r["metric"] == "false_free" and r["scope"] == "overall"]
    assert all(r["denominator"] != "" and r["rate"] != "" for r in ff)


def test_delta_paragraph_is_generated_from_numbers_only():
    rows = _norm(_standard_rows())
    keys = cer.pr15_23_frame_set(rows)
    agg = cer.aggregate(rows, keys)
    paragraphs = "\n".join(cer.build_delta_paragraphs(agg))
    assert "B vs A" in paragraphs and "C vs B" in paragraphs and "C vs A" in paragraphs
    assert "false-free" in paragraphs and "danger blocking recall" in paragraphs
    assert "pp" in paragraphs and "TODO(Lead)" in paragraphs
    # The paragraph must be identical on a second call (pure function of the table).
    assert paragraphs == "\n".join(cer.build_delta_paragraphs(agg))


def test_report_body_has_required_pr_sections_and_numbers():
    rows = _norm(_standard_rows())
    keys = cer.pr15_23_frame_set(rows)
    agg = cer.aggregate(rows, keys)
    meta = cer.build_meta(rows, None, "/tmp/eval")
    body = cer.build_report_body(agg, meta, ["a contract warning"], None, keys)
    for section in ("## Problem", "## Evidence Before", "## Change", "## Evidence After",
                    "## Regression", "## Evidence Level", "## Remaining Risks"):
        assert section in body
    assert "DESKTOP_REAL_DATA" in body
    assert "TODO(Lead)" in body
    assert "a contract warning" in body
    assert "Not rendered in this run" in body
    assert "23-frame" in body


# ---------------------------------------------------------------------------
# Synthetic SANPO-Real directory layout for the ``--save-failures`` end-to-end path
# ---------------------------------------------------------------------------
def _write_synthetic_sanpo(root, sessions=SESSIONS, n_poses=10):
    """Tiny but structurally real SANPO-Real layout (RGB + GT depth/seg + poses)."""
    from blindnav_pc.ground import pixel_rays

    h, w = 192, 256
    fx = fy = 120.0
    cx, cy = 128.0, 96.0
    cam_h = 1.35
    K = np.array([[fx, 0.0, cx], [0.0, fy, cy], [0.0, 0.0, 1.0]])
    rays = pixel_rays(K, w, h, 1)
    y = rays[..., 1]
    depth = np.where(y > 1e-3, cam_h / np.maximum(y, 1e-6), 0.0)
    depth = np.where((depth > 0.5) & (depth < 12.0), depth, 0.0).astype(np.float32)

    sem = np.full((h, w), 3, np.uint8)                 # sidewalk (walkable)
    sem[150:190, 90:170] = 1                           # road in the near field
    sem[140:150, 90:170] = 2                           # curb boundary
    sem[60:100, 30:60] = 4                             # building -> non-walkable class

    for sid in sessions:
        sdir = os.path.join(root, sid)
        ldir = os.path.join(sdir, "camera_chest", "left")
        for sub in ("video_frames", "depth_maps", "segmentation_masks"):
            os.makedirs(os.path.join(ldir, sub), exist_ok=True)
        desc = {
            "session_type": "real",
            "camera_height_m": cam_h,
            "session_camera_details": [{
                "fps": 15.0,
                "left_camera_params": {"fx": fx, "fy": fy, "cx": cx, "cy": cy,
                                       "image_width": w, "image_height": h},
            }],
        }
        with open(os.path.join(sdir, "description.json"), "w", encoding="utf-8") as fh:
            json.dump(desc, fh)
        with open(os.path.join(sdir, "camera_chest", "fixed_camera_poses.csv"), "w",
                  encoding="utf-8") as fh:
            fh.write("tracking_state,pos_x,pos_y,pos_z,q_x,q_y,q_z,q_w\n")
            for _ in range(n_poses):
                fh.write("READY,0.0,0.0,0.0,0.0,0.0,0.0,1.0\n")
        for i in range(n_poses):
            img = np.zeros((h, w, 3), np.uint8)
            img[..., 0] = i * 5
            img[..., 1] = 80
            img[..., 2] = 120
            cv2.imwrite(os.path.join(ldir, "video_frames", f"{i:06d}.png"), img)
            with gzip.open(os.path.join(ldir, "depth_maps", f"{i:06d}.float16.gz"),
                           "wb") as fh:
                header = np.array([h, w], np.float16)
                fh.write(np.concatenate([header, depth.reshape(-1)]).astype(np.float16).tobytes())
            mask = np.stack([np.zeros_like(sem), np.zeros_like(sem), sem], axis=-1)
            cv2.imwrite(os.path.join(ldir, "segmentation_masks", f"{i:06d}.png"), mask)
    return ldir


def _small_failure_rows():
    """Rows whose failing frames are all within the synthetic 10-frame SANPO session."""
    rows = []
    for cond in ("annotated", "production"):
        for arm in ("B", "C"):
            for frame in range(6):
                rows.append(_raw_row(condition=cond, arm=arm, session=SESSIONS[frame % 2],
                                     frame=frame, hint=cer.STRAIGHT,
                                     danger_ground=True, danger_any=True,
                                     gt_danger_ground_pts=10, gt_danger_any_pts=10,
                                     gt_walk_pts_swept=0, false_free=True,
                                     false_free_any=True, hazard_points=2,
                                     non_walkable_points=0, yaw_err_deg=2.0,
                                     coverage=0.8, calib_ok=True, calib_reason="ok"))
    return rows


def test_save_failures_renders_at_least_five_overlays_on_a_synthetic_session():
    rows = _norm(_small_failure_rows())
    fail_dir = _tmp_failures_dir()
    manifest = cer.save_failures(rows, fail_dir, data_root=_synthetic_root(), max_total=8)
    assert len(manifest) >= 5
    # both conditions can flag the same (kind, session, frame): filenames must stay unique
    assert len({e["overlay"] for e in manifest}) == len(manifest)
    assert len({e["raw"] for e in manifest if e["raw"]}) == len([e for e in manifest if e["raw"]])
    for entry in manifest:
        assert entry["condition"] in ("annotated", "production")
        assert entry["kind"] in cer.KIND_ORDER
        assert entry["layer"] in cer.LAYERS
        assert entry["overlay"].endswith("_overlay.jpg")
        ov_path = os.path.join(fail_dir, entry["overlay"])
        assert os.path.isfile(ov_path)
        img = cv2.imread(ov_path, cv2.IMREAD_COLOR)
        assert img is not None and img.shape[0] > 0 and img.shape[1] > 0
        # the overlay is not a plain copy of the raw frame (tints / outline / text drawn)
        assert float(img.std()) > 1.0
        if entry["raw"]:
            assert os.path.isfile(os.path.join(fail_dir, entry["raw"]))
        for key in ("gt", "evidence"):
            assert isinstance(entry[key], dict)
    with open(os.path.join(fail_dir, "failures.json"), encoding="utf-8") as fh:
        assert len(json.load(fh)) == len(manifest)


# ---------------------------------------------------------------------------
# CLI end-to-end: tables + PR body written from a synthetic eval output dir
# ---------------------------------------------------------------------------
def _write_eval_dir(eval_dir, rows, summary=None):
    os.makedirs(eval_dir, exist_ok=True)
    fields = list(cer.ROW_FIELDS)
    with open(os.path.join(eval_dir, "per_frame.csv"), "w", encoding="utf-8",
              newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in fields})
    with open(os.path.join(eval_dir, "summary.json"), "w", encoding="utf-8") as fh:
        json.dump(summary or {"meta": {}, "summary": {}, "per_session": {}}, fh)
    return eval_dir


def test_cli_writes_report_tables_and_pr_body(tmp_path):
    rows = _standard_rows()
    eval_dir = _write_eval_dir(str(tmp_path / "eval"), rows)
    out_dir = str(tmp_path / "report")
    rc = cer.main(["--out-dir", eval_dir, "--out", out_dir])
    assert rc == 0
    for name in ("report_tables.md", "report_tables.csv", "report_body.md"):
        assert os.path.isfile(os.path.join(out_dir, name))
    body = open(os.path.join(out_dir, "report_body.md"), encoding="utf-8").read()
    for section in ("## Problem", "## Evidence Before", "## Change", "## Evidence After",
                    "## Regression", "## Evidence Level", "## Remaining Risks"):
        assert section in body
    tables = open(os.path.join(out_dir, "report_tables.md"), encoding="utf-8").read()
    assert "23-frame walkable/STOP/UNKNOWN (of 23)" in tables
    assert "PR #15 fixed 23-frame set recovered: 23 frames" in tables
    with open(os.path.join(out_dir, "report_tables.csv"), encoding="utf-8") as fh:
        csv_rows = list(csv.DictReader(fh))
    assert any(r["metric"] == "false_free" and r["scope"] == "overall" for r in csv_rows)


def test_cli_rejects_a_missing_eval_dir(tmp_path):
    with pytest.raises(SystemExit):
        cer.main(["--out-dir", str(tmp_path / "nope"), "--out", str(tmp_path / "out")])


# ---------------------------------------------------------------------------
# Synthetic-data helpers used by the failure-overlay test (module scope cache)
# ---------------------------------------------------------------------------
_SYNTH_ROOT = None
_SYNTH_FAIL_DIR = None


def _synthetic_root():
    global _SYNTH_ROOT
    if _SYNTH_ROOT is None:
        import tempfile
        _SYNTH_ROOT = tempfile.mkdtemp(prefix="nef_sanpo_")
        _write_synthetic_sanpo(_SYNTH_ROOT)
    return _SYNTH_ROOT


def _tmp_failures_dir():
    global _SYNTH_FAIL_DIR
    if _SYNTH_FAIL_DIR is None:
        import tempfile
        _SYNTH_FAIL_DIR = tempfile.mkdtemp(prefix="nef_fail_")
    return _SYNTH_FAIL_DIR
