"""Split corridor evidence (Issue #18): the four-channel contract is preserved exactly.

The split must not change the amount or the geometry of negative evidence the PR #17 decision
layer consumes -- it only partitions the frozen ``hazard`` channel into curb/boundary ground
(Path Keeper) and 3D obstacle-height points (Obstacle Guardian) and exposes the 3D attributes.
"""
import math

import numpy as np
import pytest

from blindnav_pc.corridor_evidence import (ROLE_HAZARD, ROLE_IGNORE, ROLE_NON_WALKABLE,
                                           ROLE_WALKABLE, ObstaclePoints,
                                           build_corridor_evidence, build_split_corridor_evidence,
                                           body_frame)
from blindnav_pc.shadowwalk import Pose2D

H, W, FX = 80, 100, 70.0
CAMERA_HEIGHT = 1.5
UP = np.array([0.0, -1.0, 0.0])


def camera_matrix():
    return np.array([[FX, 0.0, W / 2.0], [0.0, FX, H / 2.0], [0.0, 0.0, 1.0]],
                    dtype=np.float64)


def flat_ground_depth(raise_frac=(0.65, 0.72)):
    """Flat ground with a raised strip (an obstacle above the ground plane) in image rows."""
    K = camera_matrix()
    v, _ = np.mgrid[0:H, 0:W].astype(np.float64)
    y = (v - K[1, 2]) / K[1, 1]
    depth = np.zeros((H, W), dtype=np.float64)
    below = y > 0
    depth[below] = CAMERA_HEIGHT / y[below]
    lo, hi = int(raise_frac[0] * H), int(raise_frac[1] * H)
    # A raised horizontal surface: closer depth at those rows -> positive height above ground.
    depth[lo:hi, :] = 0.6 * depth[lo:hi, :]
    return depth, K


def role_map_with_everything():
    role = np.zeros((H, W), dtype=np.uint8)
    role[:, :] = ROLE_IGNORE
    role[H // 2: int(H * 0.65), : W // 2] = ROLE_WALKABLE       # pavement ground
    role[H // 2: int(H * 0.65), W // 2: 5 * W // 8] = ROLE_HAZARD  # curb / boundary ground
    role[H // 2: int(H * 0.65), 5 * W // 8:] = ROLE_NON_WALKABLE   # road / terrain ground
    role[int(H * 0.65): int(H * 0.72), W // 4: 3 * W // 4] = ROLE_NON_WALKABLE  # raised object
    return role


def sorted_rows(points):
    p = np.asarray(points, dtype=np.float64).reshape(-1, 2)
    return p[np.lexsort((p[:, 1], p[:, 0]))] if len(p) else p


def test_split_evidence_is_row_for_row_the_frozen_four_channel_evidence():
    depth, K = flat_ground_depth()
    role = role_map_with_everything()
    pose = Pose2D(0.3, -0.2, 0.15)
    frozen = build_corridor_evidence(role, depth, K, UP, CAMERA_HEIGHT, pose, pixel_stride=2)
    split = build_split_corridor_evidence(role, depth, K, UP, CAMERA_HEIGHT, pose,
                                          pixel_stride=2)
    for name in ("walkable", "hazard", "non_walkable", "unknown"):
        np.testing.assert_array_equal(getattr(split, name), getattr(frozen, name),
                                      err_msg=f"{name} channel drifted from the frozen contract")


def test_split_hazard_is_exactly_curb_ground_plus_3d_obstacles():
    depth, K = flat_ground_depth()
    role = role_map_with_everything()
    pose = Pose2D(0.0, 0.0, 0.0)
    split = build_split_corridor_evidence(role, depth, K, UP, CAMERA_HEIGHT, pose, pixel_stride=2)
    union = np.vstack([split.curb_ground, split.obstacle.map_points])
    np.testing.assert_allclose(sorted_rows(union), sorted_rows(split.hazard))
    assert len(split.obstacle) > 0, "the raised strip must produce 3D obstacle points"


def test_obstacle_attributes_are_body_frame_and_consistent_with_map_points():
    depth, K = flat_ground_depth()
    role = role_map_with_everything()
    pose = Pose2D(1.1, 2.2, 0.4)
    split = build_split_corridor_evidence(role, depth, K, UP, CAMERA_HEIGHT, pose, pixel_stride=2)
    obs = split.obstacle
    fwd, lat = body_frame(obs.map_points, pose)
    np.testing.assert_allclose(obs.fwd_m, fwd)
    np.testing.assert_allclose(obs.lat_m, lat)
    assert np.all(obs.height_m > 0.15)
    assert np.all(obs.height_m <= 2.0)
    assert np.all(obs.depth_m > 0)
    assert np.all(obs.role != ROLE_WALKABLE)


def test_obstacle_points_validate_shapes():
    with pytest.raises(ValueError):
        ObstaclePoints(fwd_m=np.zeros(2), lat_m=np.zeros(3))
    with pytest.raises(ValueError):
        ObstaclePoints(fwd_m=np.zeros(2), lat_m=np.zeros(2), height_m=np.zeros(2),
                       depth_m=np.zeros(2), role=np.zeros(2, dtype=np.uint8),
                       map_points=np.zeros((3, 2)))
    assert len(ObstaclePoints.empty()) == 0
    assert ObstaclePoints.empty().is_empty()


def test_split_builder_rejects_shape_mismatch():
    depth, K = flat_ground_depth()
    role = np.zeros((H - 1, W), dtype=np.uint8)
    with pytest.raises(ValueError):
        build_split_corridor_evidence(role, depth, K, UP, CAMERA_HEIGHT, Pose2D(0, 0, 0))
