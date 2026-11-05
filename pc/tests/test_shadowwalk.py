"""ShadowWalk corridor stub: geometry, mask back-projection, and hint/stop logic."""
import math

import numpy as np
import pytest

from blindnav_pc.corridor_evidence import CorridorEvidence
from blindnav_pc.shadowwalk import (HINT_TEXT, LEFT, RIGHT, STRAIGHT, STOP, UNKNOWN, PRESETS,
                                    Corridor, Pose2D, ShadowWalkConfig, ShadowWalkTracker,
                                    get_corridor, mask_to_map_points)


def band_points(corridor, s0, s1, offsets, step=0.2):
    """Walkable points inside the corridor between arclength s0 and s1."""
    pts = []
    for s in np.arange(s0, s1 + 1e-9, step):
        x, z, rx, rz = corridor.point_at(s)
        for off in offsets:
            pts.append((x + rx * off, z + rz * off))
    return np.asarray(pts, float)


def test_presets_are_corridor_sized():
    assert set(PRESETS) == {"straight60", "left_arc80", "right_arc100"}
    for corridor in PRESETS.values():
        assert len(corridor.points) >= 2
        assert corridor.width_m > 0
        assert 50.0 <= corridor.length_m <= 100.0
    assert get_corridor("straight60").heading_at(10.0) == pytest.approx(0.0)
    assert get_corridor("right_arc100").heading_at(50.0) > 0.0
    assert get_corridor("left_arc80").heading_at(40.0) < 0.0


def test_projection_offset_sign_and_roundtrip():
    corridor = get_corridor("straight60")
    s, off = corridor.project(0.8, 12.0)
    assert s == pytest.approx(12.0)
    assert off == pytest.approx(0.8)
    pose = corridor.pose_at(15.0, offset_m=-0.6)
    s2, off2 = corridor.project(pose.x_m, pose.z_m)
    assert s2 == pytest.approx(15.0)
    assert off2 == pytest.approx(-0.6)


def test_corridor_json_roundtrip():
    corridor = get_corridor("straight60")
    back = Corridor.from_dict(corridor.to_dict())
    assert back.name == corridor.name
    assert back.points == corridor.points
    assert back.width_m == corridor.width_m


def test_mask_to_map_points_uses_walkable_mask_and_flat_ground():
    h, w, fx = 240, 320, 240.0
    K = np.array([[fx, 0.0, w / 2.0], [0.0, fx, h / 2.0], [0.0, 0.0, 1.0]])
    v, u = np.mgrid[0:h, 0:w].astype(np.float64)
    y = (v - K[1, 2]) / K[1, 1]
    depth = np.zeros((h, w), np.float32)
    below = y > 0
    safe_y = np.where(below, y, 1.0)
    depth[below] = (1.5 / safe_y)[below]
    mask = ((depth >= 2.0) & (depth <= 4.0)).astype(np.uint8)

    pts = mask_to_map_points(mask, depth, K, np.array([0.0, -1.0, 0.0]), 1.5,
                             Pose2D(0.0, 0.0, 0.0), ShadowWalkConfig(pixel_stride=2))
    assert len(pts) > 100
    assert pts[:, 1].min() >= 2.0 - 0.3 and pts[:, 1].max() <= 4.0 + 0.3
    assert np.abs(pts[:, 0]).max() < 3.0

    empty = mask_to_map_points(np.zeros_like(mask), depth, K, np.array([0.0, -1.0, 0.0]), 1.5,
                               Pose2D(0.0, 0.0, 0.0))
    assert len(empty) == 0


def test_centred_walker_gets_straight():
    corridor = get_corridor("straight60")
    tracker = ShadowWalkTracker(corridor)
    pts = band_points(corridor, 2.0, 7.8, np.arange(-1.0, 1.01, 0.2))
    result = tracker.update(pts, corridor.pose_at(2.0))
    assert result.hint == STRAIGHT and result.reason == "centred"
    assert result.offset_m == pytest.approx(0.0)
    assert result.coverage == pytest.approx(1.0)


def test_walker_right_of_centreline_is_told_left():
    corridor = get_corridor("straight60")
    tracker = ShadowWalkTracker(corridor)
    pts = band_points(corridor, 2.0, 7.8, np.arange(-1.0, 1.01, 0.2))
    result = tracker.update(pts, corridor.pose_at(2.0, offset_m=0.8))
    assert result.hint == LEFT and result.reason == "lateral"
    assert result.offset_m == pytest.approx(0.8)


def test_visible_band_off_centre_drives_hint():
    corridor = get_corridor("straight60")
    tracker = ShadowWalkTracker(corridor)
    # walker is centred, but the visible walkable band is only on the right half
    pts = band_points(corridor, 2.0, 5.8, np.arange(0.4, 1.01, 0.1))
    result = tracker.update(pts, corridor.pose_at(2.0))
    assert result.walkable_offset_m > 0.4
    assert result.hint == RIGHT and result.reason == "lateral"


def test_walker_outside_corridor_stops():
    corridor = get_corridor("straight60")
    tracker = ShadowWalkTracker(corridor)
    pts = band_points(corridor, 2.0, 7.8, np.arange(-1.0, 1.01, 0.2))
    result = tracker.update(pts, corridor.pose_at(2.0, offset_m=1.1))
    assert result.hint == STOP and result.reason == "off_corridor"


def test_large_heading_error_stops():
    corridor = get_corridor("straight60")
    tracker = ShadowWalkTracker(corridor)
    pts = band_points(corridor, 2.0, 7.8, np.arange(-1.0, 1.01, 0.2))
    result = tracker.update(pts, corridor.pose_at(2.0, yaw_error_rad=math.radians(40)))
    assert result.hint == STOP and result.reason == "yaw_too_large"


def test_unseen_corridor_is_unknown_not_go():
    corridor = get_corridor("straight60")
    tracker = ShadowWalkTracker(corridor)
    pts = band_points(corridor, 3.5, 4.4, np.arange(-1.0, 1.01, 0.1), step=0.05)
    result = tracker.update(pts, corridor.pose_at(2.0))
    assert result.hint == UNKNOWN and result.reason == "corridor_unseen"
    assert result.coverage < 0.5

    assert tracker.update(None, corridor.pose_at(2.0)).reason == "walkable_unseen"
    assert tracker.update(pts, None).reason == "no_pose"


# =============================================================================================
# Negative-evidence fusion (Issue #16): decision order, reason codes, swept body, temporal streak
# =============================================================================================

WALK_BAND = np.arange(-1.0, 1.01, 0.25)     # symmetric walkable band offsets, ~0.25 m spacing
HAZARD_FORWARDS = (2.1, 3.1, 4.1, 5.1)     # one credible swept-body bin each (1.5..6.0, 1.0 m bins)


def flat_poses(corridor, s0=2.0, **kwargs):
    return corridor.pose_at(s0, **kwargs)


def body_to_map(pose, forwards, lateral=0.0, n=5, jitter_m=0.01):
    """N map points per body-frame forward value, at the given body-frame lateral offset."""
    pts = []
    cos_y, sin_y = math.cos(pose.yaw_rad), math.sin(pose.yaw_rad)
    for f in forwards:
        for k in range(n):
            fwd = float(f) + jitter_m * k
            pts.append((pose.x_m + lateral * cos_y + fwd * sin_y,
                        pose.z_m - lateral * sin_y + fwd * cos_y))
    return np.asarray(pts, dtype=np.float64)


def walk_band(corridor, s0=2.0, s1=7.8, offsets=WALK_BAND):
    return band_points(corridor, s0, s1, offsets)


def assert_legacy_fields_equal(a, b):
    assert a.hint == b.hint
    assert a.text == b.text
    assert a.reason == b.reason
    assert a.n_points == b.n_points
    for name in ("progress_m", "offset_m", "walkable_offset_m", "yaw_error_rad", "coverage"):
        av, bv = getattr(a, name), getattr(b, name)
        if math.isnan(av) or math.isnan(bv):
            assert math.isnan(av) and math.isnan(bv)
        else:
            assert av == pytest.approx(bv)


def test_config_swept_body_defaults_and_property():
    cfg = ShadowWalkConfig()
    assert cfg.body_width_m == 0.5
    assert cfg.body_margin_m == 0.15
    assert cfg.temporal_confirm_frames == 0
    assert cfg.swept_half_width_m == pytest.approx(0.4)      # 0.25 + 0.15
    assert ShadowWalkConfig(body_width_m=1.0, body_margin_m=0.1).swept_half_width_m \
        == pytest.approx(0.6)


@pytest.mark.parametrize("scenario", [
    "centred", "lateral", "off_corridor", "yaw_too_large", "corridor_unseen",
    "walkable_unseen", "empty_points", "none_points", "no_pose", "nan_pose",
])
def test_update_evidence_walkable_only_matches_update(scenario):
    """Spec §2.3: update(points, pose) == update_evidence(walkable_only(points), pose)."""
    corridor = get_corridor("straight60")
    pose = corridor.pose_at(2.0)
    pts = walk_band(corridor)
    if scenario == "lateral":
        pose = corridor.pose_at(2.0, offset_m=0.8)
    elif scenario == "off_corridor":
        pose = corridor.pose_at(2.0, offset_m=1.1)
    elif scenario == "yaw_too_large":
        pose = corridor.pose_at(2.0, yaw_error_rad=math.radians(40))
    elif scenario == "corridor_unseen":
        pts = band_points(corridor, 3.5, 4.4, WALK_BAND, step=0.05)
    elif scenario == "walkable_unseen":
        pts = band_points(corridor, 3.5, 4.4, WALK_BAND, step=0.05)[:20]
    elif scenario == "empty_points":
        pts = np.zeros((0, 2))
    elif scenario == "none_points":
        pts = None
    elif scenario == "no_pose":
        pose = None
    elif scenario == "nan_pose":
        pose = Pose2D(0.0, 0.0, float("nan"))

    legacy = ShadowWalkTracker(corridor).update(pts, pose)
    fused = ShadowWalkTracker(corridor).update_evidence(CorridorEvidence.walkable_only(pts), pose)
    assert_legacy_fields_equal(legacy, fused)
    # no negative evidence was supplied -> all negative diagnostics stay at zero
    assert fused.hazard_points == fused.non_walkable_points == fused.unknown_points == 0
    assert fused.hazard_bins == fused.non_walkable_bins == fused.unknown_bins == 0
    assert fused.temporal_streak == 0


def test_legacy_update_has_no_negative_evidence_but_counts_swept_walkable_points():
    corridor = get_corridor("straight60")
    hint = ShadowWalkTracker(corridor).update(walk_band(corridor), corridor.pose_at(2.0))
    assert hint.hint == STRAIGHT and hint.reason == "centred"
    assert hint.walkable_swept_points > 0
    assert hint.hazard_points == hint.non_walkable_points == hint.unknown_points == 0


def test_road_beside_pavement_does_not_stop():
    """Hazard points OUTSIDE the 0.4 m swept body are not corridor-blocking evidence."""
    corridor = get_corridor("straight60")
    pose = corridor.pose_at(2.0)
    road = body_to_map(pose, HAZARD_FORWARDS, lateral=0.6)     # 0.6 m > swept half width 0.4 m
    ev = CorridorEvidence(walkable=walk_band(corridor), hazard=road)
    hint = ShadowWalkTracker(corridor).update_evidence(ev, pose)
    assert hint.hint == STRAIGHT and hint.reason == "centred"
    assert hint.hazard_bins == 0 and hint.hazard_points == 0


def test_swept_body_near_and_far_gating():
    corridor = get_corridor("straight60")
    pose = corridor.pose_at(2.0)
    too_near = body_to_map(pose, (0.5, 0.8), lateral=0.0)
    too_far = body_to_map(pose, (7.0, 9.0), lateral=0.0)
    ev = CorridorEvidence(walkable=walk_band(corridor), hazard=np.vstack([too_near, too_far]))
    hint = ShadowWalkTracker(corridor).update_evidence(ev, pose)
    assert hint.hint == STRAIGHT and hint.reason == "centred"
    assert hint.hazard_points == 0 and hint.hazard_bins == 0


def test_swept_evidence_includes_inclusive_far_end_bin():
    """The 5.5-6.0 m tail of the declared 1.5-6.0 m swept body must be binned.

    Review finding: with the legacy ``round((far-near)/bin) == 4`` bin count the last 0.5 m
    produced no credible bin, so hazard/non-walkable points there were silently dropped and the
    corridor could keep emitting walkable guidance.  Evidence binning must use the inclusive
    ceil range while legacy walkable coverage keeps its own (unchanged) bin count.
    """
    corridor = get_corridor("straight60")
    pose = corridor.pose_at(2.0)
    far_tail = body_to_map(pose, (5.55, 5.64, 5.73, 5.82, 5.91), lateral=0.0,
                           n=1, jitter_m=0.0)
    hint = ShadowWalkTracker(corridor).update_evidence(
        CorridorEvidence(walkable=walk_band(corridor), hazard=far_tail), pose)
    assert hint.hazard_points == 5 and hint.hazard_bins == 1
    assert hint.hint == STOP and hint.reason == "hazard_in_path"


def test_swept_evidence_far_endpoint_is_inclusive():
    corridor = get_corridor("straight60")
    pose = corridor.pose_at(2.0)
    at_far = body_to_map(pose, (6.0,), lateral=0.0, n=5, jitter_m=0.0)
    hint = ShadowWalkTracker(corridor).update_evidence(
        CorridorEvidence(walkable=walk_band(corridor), non_walkable=at_far), pose)
    assert hint.non_walkable_points == 5 and hint.non_walkable_bins == 1
    assert hint.hint == STOP and hint.reason == "non_walkable_in_path"

    beyond = body_to_map(pose, (6.01,), lateral=0.0, n=1, jitter_m=0.0)
    hint2 = ShadowWalkTracker(corridor).update_evidence(
        CorridorEvidence(walkable=walk_band(corridor), non_walkable=beyond), pose)
    assert hint2.non_walkable_points == 0
    assert hint2.hint == STRAIGHT and hint2.reason == "centred"


def test_legacy_coverage_bins_unchanged_by_evidence_far_bin():
    """Arm A walkable coverage must keep the legacy binning (the fix is evidence-only)."""
    corridor = get_corridor("straight60")
    hint = ShadowWalkTracker(corridor).update(walk_band(corridor), corridor.pose_at(2.0))
    assert hint.hint == STRAIGHT and hint.reason == "centred"
    assert hint.coverage == pytest.approx(1.0)


def test_hazard_in_swept_path_stops_despite_strong_walkable_support():
    corridor = get_corridor("straight60")
    pose = corridor.pose_at(2.0)
    hazard = body_to_map(pose, HAZARD_FORWARDS, lateral=0.1)
    ev = CorridorEvidence(walkable=walk_band(corridor), hazard=hazard)
    hint = ShadowWalkTracker(corridor).update_evidence(ev, pose)
    assert hint.hint == STOP and hint.reason == "hazard_in_path"
    assert hint.text == HINT_TEXT[STOP]
    # the walkable input is strong, yet negative evidence wins (checked before quantity branches)
    assert hint.coverage == pytest.approx(1.0) and hint.n_points >= 50
    assert hint.hazard_bins == 4 and hint.hazard_points == 20


def test_non_walkable_in_swept_path_stops_and_hazard_wins_when_both():
    corridor = get_corridor("straight60")
    pose = corridor.pose_at(2.0)
    non_walkable = body_to_map(pose, HAZARD_FORWARDS, lateral=-0.1)
    ev = CorridorEvidence(walkable=walk_band(corridor), non_walkable=non_walkable)
    hint = ShadowWalkTracker(corridor).update_evidence(ev, pose)
    assert hint.hint == STOP and hint.reason == "non_walkable_in_path"
    assert hint.non_walkable_bins == 4 and hint.non_walkable_points == 20

    hazard = body_to_map(pose, HAZARD_FORWARDS, lateral=0.1)
    both = CorridorEvidence(walkable=walk_band(corridor), hazard=hazard,
                            non_walkable=non_walkable)
    assert ShadowWalkTracker(corridor).update_evidence(both, pose).reason == "hazard_in_path"


def test_negative_evidence_precedes_walkable_quantity_checks():
    corridor = get_corridor("straight60")
    pose = corridor.pose_at(2.0)
    hazard = body_to_map(pose, HAZARD_FORWARDS, lateral=0.1)
    ev = CorridorEvidence(walkable=np.zeros((0, 2)), hazard=hazard)
    hint = ShadowWalkTracker(corridor).update_evidence(ev, pose)
    assert hint.hint == STOP and hint.reason == "hazard_in_path"   # not "walkable_unseen"

    # ... also before a temporal confirmation's walkable-quantity fallback
    tracker = ShadowWalkTracker(corridor, ShadowWalkConfig(temporal_confirm_frames=3))
    confirming = tracker.update_evidence(ev, pose)
    assert confirming.hint == UNKNOWN and confirming.reason == "hazard_confirming"


def test_unknown_evidence_requires_credible_support():
    corridor = get_corridor("straight60")
    pose = corridor.pose_at(2.0)
    credible = body_to_map(pose, (3.1,), lateral=0.1)          # 5 points in one swept-body bin
    ev = CorridorEvidence(walkable=walk_band(corridor), unknown=credible)
    hint = ShadowWalkTracker(corridor).update_evidence(ev, pose)
    assert hint.hint == UNKNOWN and hint.reason == "unknown_evidence"
    assert hint.text == HINT_TEXT[UNKNOWN]
    assert hint.unknown_bins == 1 and hint.unknown_points == 5

    # below support threshold: not credible on its own...
    low = body_to_map(pose, (3.1,), lateral=0.1, n=2)
    ev_low = CorridorEvidence(walkable=walk_band(corridor), unknown=low)
    hint_low = ShadowWalkTracker(corridor).update_evidence(ev_low, pose)
    assert hint_low.unknown_bins == 0
    # ...and with walkable support in the same swept bin it is explained, not conflicting
    assert hint_low.hint == STRAIGHT and hint_low.reason == "centred"


def test_walkable_quantity_checks_precede_unknown_evidence():
    corridor = get_corridor("straight60")
    pose = corridor.pose_at(2.0)
    unknown = body_to_map(pose, (3.1,), lateral=0.1)
    ev = CorridorEvidence(walkable=np.zeros((0, 2)), unknown=unknown)
    hint = ShadowWalkTracker(corridor).update_evidence(ev, pose)
    assert hint.hint == UNKNOWN and hint.reason == "walkable_unseen"


def test_low_support_conflict_becomes_unknown_not_safe():
    """Negative/unknown points below support, with no walkable support in the swept body."""
    corridor = get_corridor("straight60")
    pose = corridor.pose_at(2.0)
    # walkable band exists but only OUTSIDE the 0.4 m swept body (still inside the corridor band)
    walkable = band_points(corridor, 2.0, 7.8, np.arange(0.6, 1.01, 0.2))
    hazard = body_to_map(pose, (3.1, 3.2), lateral=0.1, n=2)   # 2 points in one bin
    ev = CorridorEvidence(walkable=walkable, hazard=hazard)
    hint = ShadowWalkTracker(corridor).update_evidence(ev, pose)
    assert hint.coverage == pytest.approx(1.0) and hint.n_points >= 50
    assert hint.hazard_bins == 0
    assert hint.hint == UNKNOWN and hint.reason == "evidence_conflict"


def test_low_support_negative_evidence_is_not_conflict_when_walkable_explains_it():
    corridor = get_corridor("straight60")
    pose = corridor.pose_at(2.0)
    # walkable band crosses the swept body, so each swept bin has >= points_per_bin walkable points
    hazard = body_to_map(pose, (3.1, 3.2), lateral=0.0, n=2)
    ev = CorridorEvidence(walkable=walk_band(corridor), hazard=hazard)
    hint = ShadowWalkTracker(corridor).update_evidence(ev, pose)
    assert hint.hazard_bins == 0
    assert hint.hint == STRAIGHT and hint.reason == "centred"


def test_temporal_confirmation_unknown_then_stop_and_reset():
    corridor = get_corridor("straight60")
    pose = corridor.pose_at(2.0)
    tracker = ShadowWalkTracker(corridor, ShadowWalkConfig(temporal_confirm_frames=3))
    hazard = CorridorEvidence(walkable=walk_band(corridor),
                              hazard=body_to_map(pose, HAZARD_FORWARDS, lateral=0.1))

    first = tracker.update_evidence(hazard, pose)
    assert first.hint == UNKNOWN and first.reason == "hazard_confirming"
    assert first.temporal_streak == 1
    second = tracker.update_evidence(hazard, pose)
    assert second.hint == UNKNOWN and second.reason == "hazard_confirming"
    assert second.temporal_streak == 2
    third = tracker.update_evidence(hazard, pose)
    assert third.hint == STOP and third.reason == "hazard_in_path"
    assert third.temporal_streak == 3

    # evidence gone -> streak resets and the tracker returns to walkable guidance
    clear = tracker.update_evidence(CorridorEvidence(walkable=walk_band(corridor)), pose)
    assert clear.hint == STRAIGHT and clear.temporal_streak == 0
    again = tracker.update_evidence(hazard, pose)
    assert again.hint == UNKNOWN and again.reason == "hazard_confirming"
    assert again.temporal_streak == 1


def test_temporal_confirmation_counts_mixed_hazard_and_non_walkable():
    corridor = get_corridor("straight60")
    pose = corridor.pose_at(2.0)
    tracker = ShadowWalkTracker(corridor, ShadowWalkConfig(temporal_confirm_frames=3))
    walk = walk_band(corridor)
    hazard = body_to_map(pose, HAZARD_FORWARDS, lateral=0.1)
    non_walkable = body_to_map(pose, HAZARD_FORWARDS, lateral=-0.1)

    tracker.update_evidence(CorridorEvidence(walkable=walk, hazard=hazard), pose)
    second = tracker.update_evidence(CorridorEvidence(walkable=walk, non_walkable=non_walkable),
                                     pose)
    assert second.hint == UNKNOWN and second.reason == "non_walkable_confirming"
    assert second.temporal_streak == 2
    third = tracker.update_evidence(CorridorEvidence(walkable=walk, hazard=hazard), pose)
    assert third.hint == STOP and third.reason == "hazard_in_path"
    assert third.temporal_streak == 3

    # non-walkable-only arm C confirms to its own STOP reason
    tracker2 = ShadowWalkTracker(corridor, ShadowWalkConfig(temporal_confirm_frames=1))
    confirmed = tracker2.update_evidence(
        CorridorEvidence(walkable=walk, non_walkable=non_walkable), pose)
    assert confirmed.hint == STOP and confirmed.reason == "non_walkable_in_path"


def test_temporal_streak_resets_on_unanalysable_frame():
    corridor = get_corridor("straight60")
    pose = corridor.pose_at(2.0)
    tracker = ShadowWalkTracker(corridor, ShadowWalkConfig(temporal_confirm_frames=3))
    hazard = CorridorEvidence(walkable=walk_band(corridor),
                              hazard=body_to_map(pose, HAZARD_FORWARDS, lateral=0.1))
    tracker.update_evidence(hazard, pose)
    tracker.update_evidence(hazard, pose)
    no_pose = tracker.update_evidence(hazard, None)
    assert no_pose.reason == "no_pose" and no_pose.temporal_streak == 0
    again = tracker.update_evidence(hazard, pose)
    assert again.reason == "hazard_confirming" and again.temporal_streak == 1


def test_swept_body_heading_gating():
    """The same hazard geometry blocks only when it lies along the CURRENT BODY HEADING."""
    east = Corridor("east", ((0.0, 0.0), (60.0, 0.0)), 2.5)   # corridor + tangent heading = +x
    pose = east.pose_at(2.0)
    assert pose.yaw_rad == pytest.approx(math.pi / 2)
    walk = walk_band(east)

    ahead = body_to_map(pose, HAZARD_FORWARDS, lateral=0.2)          # along body heading -> blocks
    beside = body_to_map(pose, HAZARD_FORWARDS, lateral=-3.0)        # 3 m right of heading
    stopped = ShadowWalkTracker(east).update_evidence(
        CorridorEvidence(walkable=walk, hazard=ahead), pose)
    assert stopped.hint == STOP and stopped.reason == "hazard_in_path"

    clear = ShadowWalkTracker(east).update_evidence(
        CorridorEvidence(walkable=walk, hazard=beside), pose)
    assert clear.hint == STRAIGHT and clear.reason == "centred"
    assert clear.hazard_points == 0 and clear.hazard_bins == 0


def test_body_width_config_controls_swept_gating():
    corridor = get_corridor("straight60")
    pose = corridor.pose_at(2.0)
    hazard = body_to_map(pose, HAZARD_FORWARDS, lateral=0.5)   # outside default 0.4, inside 0.65
    walk = walk_band(corridor)
    default_hint = ShadowWalkTracker(corridor).update_evidence(
        CorridorEvidence(walkable=walk, hazard=hazard), pose)
    assert default_hint.hint == STRAIGHT
    wide_hint = ShadowWalkTracker(
        corridor, ShadowWalkConfig(body_width_m=1.0)).update_evidence(
        CorridorEvidence(walkable=walk, hazard=hazard), pose)
    assert wide_hint.hint == STOP and wide_hint.reason == "hazard_in_path"


def test_off_corridor_precedes_negative_evidence():
    corridor = get_corridor("straight60")
    pose = corridor.pose_at(2.0, offset_m=1.1)
    ev = CorridorEvidence(walkable=walk_band(corridor),
                          hazard=body_to_map(pose, HAZARD_FORWARDS, lateral=0.1))
    hint = ShadowWalkTracker(corridor).update_evidence(ev, pose)
    assert hint.hint == STOP and hint.reason == "off_corridor"
    assert hint.hazard_points == 0      # evidence channels are not even converted on this branch


def test_update_evidence_no_pose_is_unchanged():
    corridor = get_corridor("straight60")
    ev = CorridorEvidence(walkable=walk_band(corridor),
                          hazard=body_to_map(corridor.pose_at(2.0), HAZARD_FORWARDS))
    hint = ShadowWalkTracker(corridor).update_evidence(ev, None)
    assert hint.hint == UNKNOWN and hint.reason == "no_pose"
    assert hint.hazard_points == hint.hazard_bins == hint.temporal_streak == 0
