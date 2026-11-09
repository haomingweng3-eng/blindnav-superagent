"""Obstacle Guardian (Issue #18): physical 3D occupancy, swept-body intersection, continuity.

The proposed collision criterion is physical occupancy + swept-body intersection + actual
clearance + temporal continuity -- explicitly NOT a fixed "at least N points" support rule.
"""
import numpy as np
import pytest

from blindnav_pc.corridor_evidence import ObstaclePoints, ROLE_NON_WALKABLE
from blindnav_pc.obstacle_guardian import (COARSE, SPATIAL, ObstacleGuardian,
                                           ObstacleGuardianConfig)
from blindnav_pc.safety_states import BLOCKED, CLEAR, OBSERVE, UNKNOWN
from blindnav_pc.safety_coordinator import SafetyCoordinator, fuse
from blindnav_pc.safety_states import CLEAR as S_CLEAR
from blindnav_pc.shadowwalk import ShadowWalkConfig
from blindnav_pc.path_keeper import PathState


def make_points(fwd_center=3.0, lat_center=0.0, fwd_extent=0.25, lat_extent=0.30,
                height_lo=0.55, height_hi=1.4, n_fwd=6, n_lat=7, depth=None):
    fwd = np.linspace(fwd_center - fwd_extent / 2, fwd_center + fwd_extent / 2, n_fwd)
    lat = np.linspace(lat_center - lat_extent / 2, lat_center + lat_extent / 2, n_lat)
    ff, ll = np.meshgrid(fwd, lat, indexing="ij")
    f, l = ff.ravel(), ll.ravel()
    h = np.resize(np.linspace(height_lo, height_hi, 8), f.shape[0])
    d = np.full(f.shape[0], fwd_center) if depth is None else np.full(f.shape[0], depth)
    map_points = np.column_stack([l, f])  # pose identity
    return ObstaclePoints(fwd_m=f, lat_m=l, height_m=h, depth_m=d,
                          role=np.full(f.shape[0], ROLE_NON_WALKABLE, dtype=np.uint8),
                          map_points=map_points)


def make_flat_path_state(status=S_CLEAR, guidance="STRAIGHT", reason="centred"):
    return PathState(status=status, guidance=guidance if status == S_CLEAR else None,
                     reason=reason, coverage=1.0, n_points=400, walkable_offset_m=0.0,
                     walker_offset_m=0.0, yaw_error_rad=0.0)


def test_one_frame_detection_is_observe_then_confirmed_is_blocked():
    g = ObstacleGuardian(SPATIAL, ObstacleGuardianConfig())
    first = g.update(make_points(), depth_confidence=1.0, calib_ok=True)
    assert first.state == OBSERVE and first.reason == "obstacle_unconfirmed_in_path"
    second = g.update(make_points(), depth_confidence=1.0, calib_ok=True)
    assert second.state == BLOCKED and second.reason == "obstacle_confirmed_in_path"
    assert second.clearance_m is not None and second.clearance_m <= 0.0
    assert second.temporal_streak >= 2


def test_clearance_is_reported_for_obstacle_outside_the_body_sweep():
    g = ObstacleGuardian(SPATIAL, ObstacleGuardianConfig())
    risk = g.update(make_points(lat_center=0.75, lat_extent=0.30), depth_confidence=1.0)
    assert risk.state == CLEAR and risk.reason == "obstacle_outside_path"
    # mass spans lat 0.60..0.90; body edge at 0.40 -> 0.20 m gap
    assert risk.clearance_m == pytest.approx(0.20, abs=1e-6)


def test_thin_depth_noise_does_not_form_an_obstacle_but_many_points_in_few_cells_do_not_count():
    # 42 points within a 1 cm x 1 cm footprint and 2 cm height: dense but physically tiny.
    g = ObstacleGuardian(SPATIAL, ObstacleGuardianConfig())
    noise = make_points(fwd_extent=0.01, lat_extent=0.01, height_lo=0.7, height_hi=0.72,
                        n_fwd=6, n_lat=7)
    assert len(noise) == 42
    risk = g.update(noise, depth_confidence=1.0)
    assert risk.state == CLEAR
    assert risk.reason in ("no_physical_obstacle", "no_occupancy")


def test_physically_sized_mass_in_path_is_a_candidate():
    g = ObstacleGuardian(SPATIAL, ObstacleGuardianConfig())
    risk = g.update(make_points(fwd_extent=0.20, lat_extent=0.25), depth_confidence=1.0)
    assert risk.state == OBSERVE
    assert risk.n_in_path >= 1


def test_obstacle_beyond_far_range_is_not_yet_a_risk():
    g = ObstacleGuardian(SPATIAL, ObstacleGuardianConfig())
    risk = g.update(make_points(fwd_center=7.5), depth_confidence=1.0)
    assert risk.state == CLEAR


def test_low_depth_quality_is_unknown_in_spatial_mode():
    g = ObstacleGuardian(SPATIAL, ObstacleGuardianConfig())
    risk = g.update(make_points(), depth_confidence=0.10)
    assert risk.state == UNKNOWN and risk.reason == "depth_quality_insufficient"
    g2 = ObstacleGuardian(SPATIAL, ObstacleGuardianConfig())
    assert g2.update(make_points(), calib_ok=False).state == UNKNOWN


def test_coarse_mode_keeps_legacy_support_without_the_spatial_depth_gate():
    # Arm B is the minimal responsibility split: the spatial-fusion depth-quality gate is a C
    # addition, so B must not introduce extra UNKNOWN frames that A would not have.
    cfg = ObstacleGuardianConfig()
    pts = ObstaclePoints(fwd_m=np.array([3.0, 3.0, 3.05, 3.1, 3.2]),
                         lat_m=np.array([0.0, 0.05, -0.05, 0.1, -0.1]),
                         height_m=np.full(5, 1.0), depth_m=np.full(5, 3.0),
                         role=np.full(5, ROLE_NON_WALKABLE, dtype=np.uint8))
    assert ObstacleGuardian(COARSE, cfg).update(pts, depth_confidence=0.10).state == BLOCKED


def test_temporal_streak_breaks_when_obstacle_disappears():
    g = ObstacleGuardian(SPATIAL, ObstacleGuardianConfig())
    g.update(make_points(), depth_confidence=1.0)          # OBSERVE
    blocked = g.update(make_points(), depth_confidence=1.0)
    assert blocked.state == BLOCKED
    assert g.update(ObstaclePoints.empty(), depth_confidence=1.0).state == CLEAR
    again = g.update(make_points(), depth_confidence=1.0)
    assert again.state == OBSERVE, "a re-appearing obstacle must be re-confirmed"


def test_coarse_mode_keeps_the_legacy_support_rule():
    cfg = ObstacleGuardianConfig()
    g = ObstacleGuardian(COARSE, cfg)
    # Five points in one 1 m bin -> legacy support -> BLOCKED immediately.
    pts = ObstaclePoints(fwd_m=np.array([3.0, 3.0, 3.05, 3.1, 3.2]),
                         lat_m=np.array([0.0, 0.05, -0.05, 0.1, -0.1]),
                         height_m=np.full(5, 1.0), depth_m=np.full(5, 3.0),
                         role=np.full(5, ROLE_NON_WALKABLE, dtype=np.uint8))
    assert g.update(pts).state == BLOCKED
    g2 = ObstacleGuardian(COARSE, cfg)
    pts4 = ObstaclePoints(fwd_m=pts.fwd_m[:4], lat_m=pts.lat_m[:4], height_m=pts.height_m[:4],
                          depth_m=pts.depth_m[:4], role=pts.role[:4])
    assert g2.update(pts4).state == CLEAR


def test_spatial_mode_does_not_use_the_legacy_five_point_rule():
    # 5 far-apart obstacle-height points (no physical mass) are not BLOCKED in spatial mode.
    pts = ObstaclePoints(fwd_m=np.array([2.0, 2.5, 3.0, 4.0, 5.0]),
                         lat_m=np.zeros(5), height_m=np.full(5, 1.0),
                         depth_m=np.array([2.0, 2.5, 3.0, 4.0, 5.0]),
                         role=np.full(5, ROLE_NON_WALKABLE, dtype=np.uint8))
    cfg = ObstacleGuardianConfig()
    assert ObstacleGuardian(SPATIAL, cfg).update(pts, depth_confidence=1.0).state != BLOCKED


def test_obstacle_suppression_is_impossible_in_the_fusion():
    coord = SafetyCoordinator()
    path_clear = make_flat_path_state()
    g = ObstacleGuardian(SPATIAL, ObstacleGuardianConfig())
    g.update(make_points(), depth_confidence=1.0)
    blocked = g.update(make_points(), depth_confidence=1.0)
    assert blocked.state == BLOCKED
    decision = coord.decide(path_clear, blocked)
    assert decision.state == "BLOCKED" and decision.effective_command() == "STOP"
    # Path UNKNOWN/OBSERVE never downgrades a confirmed obstacle either.
    for status in ("UNKNOWN", "OBSERVE"):
        d = coord.decide(make_flat_path_state(status=status), blocked)
        assert d.state == "BLOCKED"
