"""ShadowWalk corridor stub: geometry, mask back-projection, and hint/stop logic."""
import math

import numpy as np
import pytest

from blindnav_pc.shadowwalk import (LEFT, RIGHT, STRAIGHT, STOP, UNKNOWN, PRESETS, Corridor,
                                    Pose2D, ShadowWalkConfig, ShadowWalkTracker, get_corridor,
                                    mask_to_map_points)


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
