"""Path Keeper (Issue #18): surface/boundary/drift responsibility never produces BLOCKED.

Hard rule under test: NON_WALKABLE (road/terrain) and curb/boundary surface evidence may demote
the decision to OBSERVE/UNKNOWN, never to a collision STOP; the walkable guidance geometry must
stay the frozen PR #17 geometry.
"""
import math

import numpy as np
import pytest

from blindnav_pc.path_keeper import (PathKeeper, PathObservation, swept_bin_counts,
                                     swept_evidence_bin_count)
from blindnav_pc.safety_coordinator import fuse
from blindnav_pc.safety_states import BLOCKED, CLEAR, OBSERVE, UNKNOWN
from blindnav_pc.shadowwalk import LEFT, RIGHT, STRAIGHT, STOP, Corridor, Pose2D, ShadowWalkConfig

CORRIDOR = Corridor("straight", ((0.0, 0.0), (0.0, 60.0)), 2.5)
CFG = ShadowWalkConfig()


def empty():
    return np.zeros((0, 2), dtype=np.float64)


def band_points(n=400, fwd0=1.5, fwd1=6.0, lat=-0.2, lat1=0.2):
    """Walkable ground covering the full 1.5-6.0 m band, centred on the walker."""
    fwd = np.linspace(fwd0, fwd1, n)
    lat = np.linspace(lat, lat1, n)
    return np.column_stack([lat, fwd])


def observation(walkable=None, non_walkable=None, curb=None, unknown=None):
    return PathObservation(walkable=band_points() if walkable is None else walkable,
                           non_walkable=empty() if non_walkable is None else non_walkable,
                           curb=empty() if curb is None else curb,
                           unknown=empty() if unknown is None else unknown)


def body_points(fwd0, fwd1, lat=0.0, n=40):
    fwd = np.linspace(fwd0, fwd1, n)
    return np.column_stack([np.full(n, lat), fwd])


def test_clear_walkable_band_emits_centred_guidance():
    state = PathKeeper(CORRIDOR, CFG).update(observation(), Pose2D(0.0, 0.0, 0.0))
    assert state.status == CLEAR
    assert state.guidance == STRAIGHT
    assert state.reason == "centred"
    assert state.is_pass()


def test_non_walkable_ground_in_body_sweep_is_observe_never_stop():
    obs = observation(non_walkable=body_points(2.0, 5.0, lat=0.0))
    path = PathKeeper(CORRIDOR, CFG).update(obs, Pose2D(0.0, 0.0, 0.0))
    assert path.status == OBSERVE
    assert path.reason == "non_walkable_in_path"
    assert path.guidance is None
    assert path.non_walkable_bins > 0
    # The fused state must never be BLOCKED for surface-only evidence.
    decision = fuse(path, _clear_obstacle())
    assert decision.state == OBSERVE
    assert decision.effective_command() != STOP


def test_curb_ground_in_body_sweep_is_observe_never_stop():
    obs = observation(curb=body_points(2.5, 5.0, lat=0.1))
    path = PathKeeper(CORRIDOR, CFG).update(obs, Pose2D(0.0, 0.0, 0.0))
    assert path.status == OBSERVE
    assert path.reason == "curb_in_path"
    decision = fuse(path, _clear_obstacle())
    assert decision.state == OBSERVE and decision.effective_command() != STOP


def test_non_walkable_outside_body_sweep_does_not_block():
    # Road 1 m to the side: outside the 0.4 m body swept band.
    obs = observation(non_walkable=body_points(2.0, 5.0, lat=1.0))
    path = PathKeeper(CORRIDOR, CFG).update(obs, Pose2D(0.0, 0.0, 0.0))
    assert path.status == CLEAR and path.guidance == STRAIGHT


def test_insufficient_walkable_support_is_unknown_not_pass():
    obs = observation(walkable=np.column_stack([np.zeros(3), np.linspace(2.0, 3.0, 3)]))
    path = PathKeeper(CORRIDOR, CFG).update(obs, Pose2D(0.0, 0.0, 0.0))
    assert path.status == UNKNOWN and path.guidance is None


def test_walkable_but_only_one_bin_is_unknown_corridor_unseen():
    walk = body_points(1.6, 2.4, n=200)
    path = PathKeeper(CORRIDOR, CFG).update(observation(walkable=walk), Pose2D(0.0, 0.0, 0.0))
    assert path.status == UNKNOWN
    assert path.reason in ("corridor_unseen", "walkable_unseen")


def test_unknown_surface_evidence_is_unknown_not_pass():
    obs = observation(unknown=body_points(2.5, 5.0, lat=0.0))
    path = PathKeeper(CORRIDOR, CFG).update(obs, Pose2D(0.0, 0.0, 0.0))
    assert path.status == UNKNOWN and path.reason == "unknown_evidence"


def test_off_corridor_is_observe_drift_never_stop():
    path = PathKeeper(CORRIDOR, CFG).update(observation(),
                                            Pose2D(1.4, 10.0, 0.0))  # beyond half*0.8 = 1.0
    assert path.status == OBSERVE and path.reason == "off_corridor"
    assert fuse(path, _clear_obstacle()).state == OBSERVE


def test_large_yaw_error_is_observe_drift():
    path = PathKeeper(CORRIDOR, CFG).update(observation(),
                                            Pose2D(0.0, 2.0, math.radians(45.0)))
    assert path.status == OBSERVE and path.reason == "yaw_too_large"


def test_lateral_offset_produces_usable_correction_guidance():
    walk = band_points(lat=0.5, lat1=0.9)   # visible walkable band shifted right
    path = PathKeeper(CORRIDOR, CFG).update(observation(walkable=walk),
                                            Pose2D(-0.6, 2.0, 0.0))
    assert path.status == CLEAR
    assert path.guidance in (LEFT, RIGHT)


def test_no_pose_is_unknown():
    path = PathKeeper(CORRIDOR, CFG).update(observation(), None)
    assert path.status == UNKNOWN and path.reason == "no_pose"


def test_swept_bin_helpers_match_legacy_tracker_definition():
    from blindnav_pc.shadowwalk import ShadowWalkTracker
    cfg = ShadowWalkConfig()
    nbins = swept_evidence_bin_count(cfg)
    assert nbins == ShadowWalkTracker._swept_evidence_bins(cfg)
    points = np.column_stack([np.linspace(-0.5, 0.5, 50), np.linspace(1.4, 6.1, 50)])
    pose = Pose2D(0.0, 0.0, 0.3)
    np.testing.assert_array_equal(
        swept_bin_counts(points, pose, cfg, nbins),
        ShadowWalkTracker._swept_bin_counts(points, pose, cfg, nbins))


def _clear_obstacle():
    from blindnav_pc.obstacle_guardian import ObstacleRisk
    return ObstacleRisk(CLEAR, "no_occupancy")
