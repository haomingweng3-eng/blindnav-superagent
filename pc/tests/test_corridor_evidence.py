"""Corridor evidence channels: geometry helpers, builder classification and the walkable invariant."""
import math

import numpy as np
import pytest

from blindnav_pc.bev import depth_to_level_points
from blindnav_pc.corridor_evidence import (ROLE_HAZARD, ROLE_IGNORE, ROLE_NON_WALKABLE,
                                           ROLE_WALKABLE, CorridorEvidence, body_frame,
                                           build_corridor_evidence, in_swept_body)
from blindnav_pc.shadowwalk import Pose2D, ShadowWalkConfig, mask_to_map_points

H, W, FX = 60, 80, 60.0
CAMERA_HEIGHT = 1.5
UP = np.array([0.0, -1.0, 0.0])
OBSTACLE_MAX = 2.0


def camera_matrix(h=H, w=W, fx=FX):
    return np.array([[fx, 0.0, w / 2.0], [0.0, fx, h / 2.0], [0.0, 0.0, 1.0]], dtype=np.float64)


def flat_ground_depth(h=H, w=W, fx=FX, camera_height=CAMERA_HEIGHT):
    """Depth map of a flat ground plane at ``height = 0`` under the camera-height assumption."""
    K = camera_matrix(h, w, fx)
    v, _ = np.mgrid[0:h, 0:w].astype(np.float64)
    y = (v - K[1, 2]) / K[1, 1]
    depth = np.zeros((h, w), dtype=np.float64)
    below = y > 0
    depth[below] = camera_height / y[below]
    return depth


def map_from_level(x_lat, z_fwd, pose):
    """Map transform used everywhere (duplicated in the test on purpose, as the contract)."""
    cos_y, sin_y = math.cos(pose.yaw_rad), math.sin(pose.yaw_rad)
    mx = pose.x_m + x_lat * cos_y + z_fwd * sin_y
    mz = pose.z_m - x_lat * sin_y + z_fwd * cos_y
    return np.column_stack([mx, mz]) if len(mx) else np.zeros((0, 2))


def expected_channel(role_map, depth, pose, predicate, *, stride=2, up=UP,
                     camera_height=CAMERA_HEIGHT, max_height_m=0.15,
                     obstacle_max_m=OBSTACLE_MAX):
    """Independent re-statement of the frozen channel formulas in spec §1."""
    K = camera_matrix()
    x_lat, z_fwd, height, (vs, us) = depth_to_level_points(
        depth, K, up, camera_height, stride=stride)
    role = role_map[vs, us]
    ground = np.abs(height) <= max_height_m
    obstacle = (height > max_height_m) & (height <= obstacle_max_m)
    below = height < -max_height_m
    keep = predicate(role, ground, obstacle, below)
    keep &= np.isfinite(x_lat) & np.isfinite(z_fwd)
    return map_from_level(x_lat[keep], z_fwd[keep], pose)


def sorted_rows(points):
    p = np.asarray(points, dtype=np.float64).reshape(-1, 2)
    return p[np.lexsort((p[:, 1], p[:, 0]))] if len(p) else p


def assert_same_points(actual, expected):
    a, e = sorted_rows(actual), sorted_rows(expected)
    assert a.shape == e.shape
    np.testing.assert_allclose(a, e, atol=1e-12)


# ---------------------------------------------------------------------------------------------
# CorridorEvidence container
# ---------------------------------------------------------------------------------------------

def test_evidence_normalises_shape_and_dtype():
    ev = CorridorEvidence(walkable=[[1.0, 2.0], [3.0, 4.0]],
                          hazard=np.zeros((0, 2), np.float32),
                          non_walkable=None,
                          unknown=np.array([[5.0, 6.0]], dtype=np.float32))
    assert ev.walkable.shape == (2, 2) and ev.walkable.dtype == np.float64
    assert ev.hazard.shape == (0, 2) and ev.hazard.dtype == np.float64
    assert ev.non_walkable.shape == (0, 2)
    assert ev.unknown.shape == (1, 2) and ev.unknown.dtype == np.float64
    # an empty container works too
    empty = CorridorEvidence()
    assert empty.is_empty() and empty.counts() == {"walkable": 0, "hazard": 0,
                                                   "non_walkable": 0, "unknown": 0}


def test_walkable_only_keeps_the_negative_channels_empty():
    pts = np.array([[0.0, 2.0], [0.2, 3.0]])
    ev = CorridorEvidence.walkable_only(pts)
    np.testing.assert_array_equal(ev.walkable, pts)
    for name in ("hazard", "non_walkable", "unknown"):
        assert getattr(ev, name).shape == (0, 2)
    assert not ev.is_empty()
    assert ev.counts() == {"walkable": 2, "hazard": 0, "non_walkable": 0, "unknown": 0}
    assert CorridorEvidence.walkable_only(None).is_empty()


# ---------------------------------------------------------------------------------------------
# body frame / swept body geometry
# ---------------------------------------------------------------------------------------------

@pytest.mark.parametrize("yaw, expected_fwd, expected_lat", [
    (0.0, (3.0, -0.5), (1.0, -0.5)),           # heading +z: fwd = dz, lateral = dx
    (math.pi / 2, (1.0, -0.5), (-3.0, 0.5)),    # heading +x: fwd = dx, lateral = -dz
    (math.pi, (-3.0, 0.5), (-1.0, 0.5)),        # heading -z: fwd = -dz, lateral = -dx
    (-math.pi / 2, (-1.0, 0.5), (3.0, -0.5)),
])
def test_body_frame_is_relative_to_current_heading(yaw, expected_fwd, expected_lat):
    pose = Pose2D(x_m=1.0, z_m=2.0, yaw_rad=yaw)
    points = np.array([[2.0, 5.0], [0.5, 1.5]])   # dx, dz = (1, 3) and (-0.5, -0.5)
    fwd, lat = body_frame(points, pose)
    np.testing.assert_allclose(fwd, expected_fwd, atol=1e-12)
    np.testing.assert_allclose(lat, expected_lat, atol=1e-12)


def test_body_frame_empty():
    fwd, lat = body_frame(None, Pose2D(0.0, 0.0, 0.0))
    assert fwd.shape == (0,) and lat.shape == (0,)


def test_in_swept_body_bounds_are_inclusive():
    fwd = np.array([1.0, 1.5, 3.0, 6.0, 6.001, 3.0])
    lat = np.array([0.0, 0.0, 0.0, 0.0, 0.0, 0.401])
    inside = in_swept_body(fwd, lat, 1.5, 6.0, 0.4)
    np.testing.assert_array_equal(inside, [False, True, True, True, False, False])
    # exactly on the lateral boundary counts as inside
    assert bool(in_swept_body(np.array([2.0]), np.array([-0.4]), 1.5, 6.0, 0.4)[0])


# ---------------------------------------------------------------------------------------------
# builder: role -> channel classification
# ---------------------------------------------------------------------------------------------

def scene_with_roles():
    depth = flat_ground_depth()
    role = np.full((H, W), ROLE_WALKABLE, dtype=np.uint8)
    role[:, 20:40] = ROLE_HAZARD
    role[:, 40:50] = ROLE_NON_WALKABLE
    role[:, 50:60] = ROLE_IGNORE
    return role, depth


@pytest.mark.parametrize("pose", [
    Pose2D(0.0, 0.0, 0.0),
    Pose2D(1.2, -0.7, 0.35),
])
def test_builder_classifies_ground_roles(pose):
    role, depth = scene_with_roles()
    ev = build_corridor_evidence(role, depth, camera_matrix(), UP, CAMERA_HEIGHT, pose,
                                 pixel_stride=2)

    def walkable(role, ground, obstacle, below):
        return ground & (role == ROLE_WALKABLE)

    def hazard(role, ground, obstacle, below):
        return (ground & (role == ROLE_HAZARD)) | (obstacle & (role != ROLE_WALKABLE))

    def non_walkable(role, ground, obstacle, below):
        return ground & (role == ROLE_NON_WALKABLE)

    def unknown(role, ground, obstacle, below):
        return ((ground & (role == ROLE_IGNORE)) | (obstacle & (role == ROLE_IGNORE))
                | ((role == ROLE_WALKABLE) & (obstacle | below)))

    assert_same_points(ev.walkable, expected_channel(role, depth, pose, walkable))
    assert_same_points(ev.hazard, expected_channel(role, depth, pose, hazard))
    assert_same_points(ev.non_walkable, expected_channel(role, depth, pose, non_walkable))
    assert_same_points(ev.unknown, expected_channel(role, depth, pose, unknown))
    # flat ground: every valid point is ground and has exactly one of the four roles
    for arr in (ev.walkable, ev.hazard, ev.non_walkable, ev.unknown):
        assert len(arr) > 0
    # hazard/non-walkable/unknown must not be walkable geometry
    assert len(ev.walkable) + len(ev.hazard) + len(ev.non_walkable) + len(ev.unknown) == 560


def rows_set(points):
    p = np.asarray(points, dtype=np.float64).reshape(-1, 2)
    return {tuple(row) for row in np.round(p, 12)}


def test_builder_geometric_conflicts_are_unknown_not_walkable():
    role, depth = scene_with_roles()
    depth = depth.copy()
    # obstacle (height 0.75 m) on pixels the mask calls walkable -> geometric conflict
    depth[40:50:2, 10:20:2] *= 0.5
    # drop (height -0.75 m) on pixels the mask calls walkable -> geometric conflict
    depth[40:50:2, 60:70:2] *= 1.5
    pose = Pose2D(0.0, 0.0, 0.0)
    ev = build_corridor_evidence(role, depth, camera_matrix(), UP, CAMERA_HEIGHT, pose,
                                 pixel_stride=2)

    conflict = expected_channel(role, depth, pose,
                                lambda r, g, o, b: (r == ROLE_WALKABLE) & (o | b))
    assert len(conflict) > 0
    assert_same_points(ev.unknown, expected_channel(
        role, depth, pose,
        lambda r, g, o, b: ((g & (r == ROLE_IGNORE)) | (o & (r == ROLE_IGNORE))
                            | ((r == ROLE_WALKABLE) & (o | b)))))
    # the conflict points land in unknown; the walkable channel is exactly ground-walkable points
    # (map coordinates can coincide across different pixels, so compare counts, not set overlap)
    conflict_set, unknown_set = rows_set(conflict), rows_set(ev.unknown)
    assert conflict_set <= unknown_set
    assert len(ev.walkable) == len(expected_channel(
        role, depth, pose, lambda r, g, o, b: g & (r == ROLE_WALKABLE)))
    # bonus: the mask-walkable obstacle/drop points are the only geometric conflicts here
    assert len(conflict_set) == 50


def test_builder_obstacle_evidence_and_ignore_overlap():
    # Frozen spec formulas: blocking obstacles are HAZARD when the mask does not say walkable
    # (including ignore), and obstacle+ignore is ALSO in the unknown channel.
    role = np.full((H, W), ROLE_WALKABLE, dtype=np.uint8)
    role[:, 20:30] = ROLE_HAZARD
    role[:, 30:40] = ROLE_NON_WALKABLE
    role[:, 40:50] = ROLE_IGNORE
    depth = flat_ground_depth()
    depth[40:50:2, 20:50:2] *= 0.5     # obstacle band over hazard/non-walkable/ignore columns
    pose = Pose2D(0.0, 0.0, 0.0)
    ev = build_corridor_evidence(role, depth, camera_matrix(), UP, CAMERA_HEIGHT, pose,
                                 pixel_stride=2)
    expected_hazard = expected_channel(
        role, depth, pose,
        lambda r, g, o, b: (g & (r == ROLE_HAZARD)) | (o & (r != ROLE_WALKABLE)))
    expected_non = expected_channel(role, depth, pose, lambda r, g, o, b: g & (r == ROLE_NON_WALKABLE))
    expected_unknown = expected_channel(
        role, depth, pose,
        lambda r, g, o, b: ((g & (r == ROLE_IGNORE)) | (o & (r == ROLE_IGNORE))
                            | ((r == ROLE_WALKABLE) & (o | b))))
    assert_same_points(ev.hazard, expected_hazard)
    assert_same_points(ev.non_walkable, expected_non)
    assert_same_points(ev.unknown, expected_unknown)
    # frozen spec overlap: obstacle + ignore points are in BOTH hazard and unknown
    assert len(rows_set(ev.hazard) & rows_set(ev.unknown)) == 25


def test_builder_walkable_channel_matches_mask_to_map_points_exactly():
    """Hard invariant (spec §1): the walkable channel is row-for-row mask_to_map_points output."""
    rng = np.random.default_rng(7)
    role = rng.integers(0, 4, size=(H, W)).astype(np.uint8)
    depth = flat_ground_depth()
    pose = Pose2D(0.9, -1.4, -0.55)
    K = camera_matrix()
    for stride in (1, 2, 4):
        ev = build_corridor_evidence(role, depth, K, UP, CAMERA_HEIGHT, pose,
                                     pixel_stride=stride)
        cfg = ShadowWalkConfig(pixel_stride=stride, max_height_m=0.15)
        expected = mask_to_map_points((role == ROLE_WALKABLE).astype(np.uint8), depth, K, UP,
                                      CAMERA_HEIGHT, pose, cfg)
        np.testing.assert_array_equal(ev.walkable, expected)


def test_builder_keeps_pose_transform_sign_conventions():
    depth = flat_ground_depth()
    role = np.full((H, W), ROLE_WALKABLE, dtype=np.uint8)
    pose = Pose2D(2.0, 3.0, 0.0)
    ev = build_corridor_evidence(role, depth, camera_matrix(), UP, CAMERA_HEIGHT, pose,
                                 pixel_stride=2)
    assert len(ev.walkable) > 0
    fwd, lat = body_frame(ev.walkable, pose)
    # flat ground is in front of the camera in the body frame, under the horizon
    assert np.all(fwd > 0.0)
    assert np.allclose(ev.walkable[:, 1] - pose.z_m, fwd)
    assert np.allclose(ev.walkable[:, 0] - pose.x_m, lat)
    assert float(lat.max()) > 0.0 > float(lat.min())   # spans both sides of the heading


def test_builder_drops_non_finite_points():
    role = np.full((H, W), ROLE_WALKABLE, dtype=np.uint8)
    depth = flat_ground_depth()
    pose = Pose2D(0.0, 0.0, 0.0)
    # a non-finite up vector makes the level-frame projection non-finite -> every channel empty
    bad_up = np.array([np.nan, -1.0, 0.0])
    ev = build_corridor_evidence(role, depth, camera_matrix(), bad_up, CAMERA_HEIGHT, pose,
                                 pixel_stride=2)
    assert ev.is_empty()
    # depth 0 / inf pixels are excluded by the existing BEV validity gate, not emitted as NaN
    depth2 = depth.copy()
    depth2[::2, ::2] = np.inf
    ev2 = build_corridor_evidence(role, depth2, camera_matrix(), UP, CAMERA_HEIGHT, pose,
                                  pixel_stride=2)
    for arr in (ev2.walkable, ev2.hazard, ev2.non_walkable, ev2.unknown):
        assert np.all(np.isfinite(arr))


def test_builder_rejects_shape_mismatch():
    role = np.full((H, W + 2), ROLE_WALKABLE, dtype=np.uint8)
    depth = flat_ground_depth()
    with pytest.raises(ValueError):
        build_corridor_evidence(role, depth, camera_matrix(), UP, CAMERA_HEIGHT,
                                Pose2D(0.0, 0.0, 0.0), pixel_stride=2)
