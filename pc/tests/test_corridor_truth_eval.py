"""Tests for the four-arm corridor truth-loss harness (measurement code only).

The heavy model inference is not exercised; these tests pin the label remap, the
arm-mask construction, the physical boundary band, the route-tangent / rotation-only
logic and the numerator/denominator aggregation.
"""
import math
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "tools")))

import corridor_truth_eval as cte  # noqa: E402


# ---------------------------------------------------------------------------
# SANPO remap / arm masks
# ---------------------------------------------------------------------------
def test_gt_remap_maps_sanpo_ids_to_v5_classes():
    seg = np.array([[0, 1, 2, 3, 5, 6, 17, 27, 30, 7]], np.uint8)
    cls = cte.gt_remap(seg)
    assert cls.tolist() == [[0, cte.CLASS_COND, cte.CLASS_BOUND, cte.CLASS_SAFE,
                             cte.CLASS_SAFE, cte.CLASS_SAFE, cte.CLASS_SAFE,
                             0, cte.CLASS_COND, cte.CLASS_NON]]


def test_arm_masks_use_gt_for_a_c_and_v5_for_b_d():
    gt = np.zeros((2, 4), np.uint8)
    gt[0, :2] = cte.CLASS_SAFE
    gt[0, 2:] = cte.CLASS_COND
    gt[1, :2] = cte.CLASS_BOUND
    v5 = np.zeros_like(gt)
    v5[0, :3] = cte.CLASS_SAFE          # v5 over-extends onto one road pixel
    v5[1, 0] = cte.CLASS_BOUND
    walk_a, bound_a = cte.make_arm_classes("A", gt, v5)
    walk_b, bound_b = cte.make_arm_classes("B", gt, v5)
    walk_c, bound_c = cte.make_arm_classes("C", gt, v5)
    walk_d, bound_d = cte.make_arm_classes("D", gt, v5)
    np.testing.assert_array_equal(walk_a, walk_c)
    np.testing.assert_array_equal(walk_b, walk_d)
    np.testing.assert_array_equal(bound_a, bound_c)
    np.testing.assert_array_equal(bound_b, bound_d)
    assert walk_a[0, :2].all() and not walk_a[0, 2:].any()
    assert walk_b[0, 2]                      # v5's Road-as-Sidewalk pixel
    assert bound_a[1, :2].all() and not bound_b[1, 1:].any()


# ---------------------------------------------------------------------------
# Uncertain boundary band
# ---------------------------------------------------------------------------
def _band_fixture():
    h = w = 120
    walk = np.zeros((h, w), np.uint8)
    walk[:, : w // 2] = 1                     # left half walkable, right half road
    boundary_label = np.zeros((h, w), np.uint8)
    depth = np.full((h, w), 4.0, np.float32)  # 4 m everywhere -> r = 0.25*fx/4 px
    return walk, boundary_label, depth


def test_uncertain_band_is_physical_and_centred_on_the_boundary():
    walk, boundary, depth = _band_fixture()
    w = walk.shape[1]
    fx = 400.0                                # r = 0.25 * 400 / 4 = 25 px
    band = cte.uncertain_band(walk, boundary, depth, fx, band_m=0.25)
    assert band.sum() > 0
    # The band must straddle the walkable/non-walkable seam at x = w/2.
    seam = band[:, w // 2 - 2: w // 2 + 2]
    assert seam.mean() > 0.9
    # Everything far from the seam is outside the band.
    assert not band[:, w // 2 - 60: w // 2 - 40].any()
    # Band half-width in pixels matches the physical conversion at 4 m.
    row = band[60]
    left_edge = np.nonzero(row)[0].min()
    assert abs((w // 2 - left_edge) - 25) <= 2


def test_uncertain_band_is_empty_without_a_boundary():
    walk = np.ones((20, 20), np.uint8)
    band = cte.uncertain_band(walk, np.zeros_like(walk), np.full((20, 20), 3.0, np.float32), 300.0)
    assert not band.any()


# ---------------------------------------------------------------------------
# Route geometry / rotation-only detection
# ---------------------------------------------------------------------------
def test_route_tangents_follow_a_straight_line():
    pos = np.stack([np.zeros(20), np.arange(20) * 0.5], axis=1)
    tang = cte.route_tangents(pos)
    assert np.allclose(tang, np.array([0.0, 1.0]))


def test_route_tangents_follow_a_curve():
    s = np.linspace(0.0, math.pi / 2.0, 60)
    pos = np.stack([np.sin(s), np.cos(s)], axis=1) * 5.0
    tang = cte.route_tangents(pos)
    assert np.allclose(np.linalg.norm(tang, axis=1), 1.0)
    assert not np.allclose(tang[0], tang[-1])


def test_rotation_only_flag_separates_camera_rotation_from_route_turn():
    n = 8
    headings = np.zeros(n)
    tangents = np.stack([np.zeros(n), np.ones(n)], axis=1)
    # A camera rotation without the walking direction turning.
    headings[4:] = math.radians(15.0)
    flags = cte.rotation_only_flags(headings, tangents, dt_s=1.0 / 3.0)
    assert flags[4:7].all() and not flags[:4].any()
    # A route turn that the camera follows is not a rotation-only event.
    tangents2 = tangents.copy()
    tangents2[4:] = np.array([math.sin(math.radians(20.0)), math.cos(math.radians(20.0))])
    headings2 = np.zeros(n)
    headings2[4:] = math.radians(20.0)
    flags2 = cte.rotation_only_flags(headings2, tangents2, dt_s=1.0 / 3.0)
    assert not flags2[4:].any()


# ---------------------------------------------------------------------------
# Aggregation with explicit numerators/denominators
# ---------------------------------------------------------------------------
def _row(**kw):
    base = {
        "mode": cte.MODE_ORACLE, "arm": "A", "session": "s", "frame": 0,
        "hint": cte.STRAIGHT, "reason": "centred", "coverage": 0.6, "n_points": 100,
        "calib_ok": True, "rotation_only": False, "wrong_correction": False,
        "road_pts_band": 10, "road_pts_walkable": 0, "curb_pts_band": 10,
        "curb_pts_uncertain": 0, "gt_walk_pts_band": 100,
        "false_safe_road": True, "missed_curb": True, "unknown_when_gt_walkable": False,
        "coverage_bins_ok": True, "band_correction": False, "arm": "A",
        "road_path_band": 10, "road_path_walkable": 0,
        "curb_path_band": 10, "gt_band_offset_m": 0.0, "gt_hint": cte.STRAIGHT,
    }
    base.update(kw)
    return base


def test_summarize_reports_numerators_and_denominators():
    rows = [
        _row(frame=0, arm="A", hint=cte.UNKNOWN, coverage=0.0,
             false_safe_road=False, missed_curb=False, unknown_when_gt_walkable=True),
        _row(frame=1, arm="A", road_pts_band=20, road_pts_walkable=5,
             road_path_band=20, road_path_walkable=5,
             curb_pts_band=20, curb_pts_uncertain=5),
        _row(frame=2, arm="A", hint=cte.LEFT, rotation_only=True, wrong_correction=True,
             false_safe_road=False, missed_curb=False),
    ]
    summary = cte.summarize(rows)
    s = summary[f"{cte.MODE_ORACLE}/A"]
    assert s["frames"] == 3
    assert s["road_as_sidewalk"] == {"numerator": 5, "denominator": 40, "rate": 5 / 40}
    assert s["curb_recall"] == {"numerator": 5, "denominator": 40, "rate": 5 / 40}
    assert s["unknown_n"] == 1 and s["unknown_rate"] == pytest.approx(1 / 3)
    assert s["corrections"]["left_right_count"] == 1
    assert s["corrections"]["wrong_correction_count"] == 1
    assert s["failing_frames"] == 3
    assert s["failure_layers"] == {"state_machine": 1, "segmentation": 1, "motion": 1}
    assert s["failure_reasons"] == {"unknown_when_gt_walkable": 1,
                                    "road_in_path_walkable": 1,
                                    "rotation_wrong_correction": 1}
    assert s["corridor_coverage"]["frames_ge_min_coverage"] == 3
    assert s["corridor_coverage"]["mean_coverage_known_frames"] == pytest.approx(0.4)


def test_classify_layer_prioritises_segmentation_over_state_machine():
    assert cte.classify_layer(_row()) == "state_machine"
    assert cte.classify_layer(_row(road_path_walkable=3)) == "segmentation"
    assert cte.classify_layer(_row(hint=cte.UNKNOWN, n_points=10)) == "geometry"
    assert cte.classify_layer(_row(wrong_correction=True)) == "motion"
    assert cte.classify_layer(_row(calib_ok=False)) == "geometry"


def test_in_corridor_band_uses_margin_and_range():
    fwd = np.array([1.0, 2.0, 5.0, 7.0])
    lat = np.array([0.0, 1.0, 2.0, 0.0])
    band = cte.in_corridor_band(fwd, lat, cte.SW_CFG, 1.25)
    assert band.tolist() == [False, True, False, False]


def test_frame_dt_from_indices():
    assert cte.frame_dt_s([0, 5, 10, 15], 15.0) == pytest.approx(1 / 3)
