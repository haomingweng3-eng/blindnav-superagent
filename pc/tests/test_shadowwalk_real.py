"""ShadowWalk wired to real mask inputs: label-map adapter + flat-ground depth path."""
import math

import numpy as np
import pytest

from blindnav_pc.ground import flat_ground_depth_map
from blindnav_pc.segmentation import PrecomputedMaskSegmenter, label_map_to_walkable
from blindnav_pc.shadowwalk import (LEFT, RIGHT, STRAIGHT, STOP, UNKNOWN, ShadowWalkConfig,
                                    ShadowWalkTracker, get_corridor, mask_to_map_points)


def _ground_mask_label(h=240, w=320):
    """A real-label-shaped array: background black, walkable ground (blind-road colour) at the bottom."""
    label = np.zeros((h, w, 3), np.uint8)
    label[h // 2:, :] = (128, 128, 0)
    return label


def _camera(w=320, h=240, fx=240.0):
    return np.array([[fx, 0.0, w / 2.0], [0.0, fx, h / 2.0], [0.0, 0.0, 1.0]])


def test_label_map_to_walkable_3d_and_2d():
    label = _ground_mask_label()
    mask = label_map_to_walkable(label)
    assert mask.dtype == np.uint8
    assert mask[label.shape[0] // 2:, :].all()
    assert not mask[:label.shape[0] // 2, :].any()

    ids = np.zeros((4, 4), np.int32)
    ids[1:3, 1:3] = 2
    m2 = label_map_to_walkable(ids)
    assert m2.sum() == 4


def test_precomputed_mask_segmenter_returns_mask_and_checks_shape():
    label = _ground_mask_label()
    mask = label_map_to_walkable(label)
    seg = PrecomputedMaskSegmenter(mask)
    frame = np.zeros((240, 320, 3), np.uint8)
    assert np.array_equal(seg.predict(frame), mask)
    with pytest.raises(ValueError):
        seg.predict(np.zeros((120, 160, 3), np.uint8))


def test_flat_ground_depth_map_is_finite_only_below_horizon():
    K = _camera()
    up = np.array([0.0, -math.cos(math.radians(15.0)), -math.sin(math.radians(15.0))])
    depth = flat_ground_depth_map(K, 320, 240, up, 1.2)
    assert depth.shape == (240, 320)
    assert np.isnan(depth[:50]).all()
    assert np.isfinite(depth[-40:]).all()
    assert np.nanmin(depth) > 0.0


def test_real_mask_sequence_reaches_tracker_and_stops_off_corridor():
    corridor = get_corridor("straight60")
    K = _camera()
    up = np.array([0.0, -math.cos(math.radians(15.0)), -math.sin(math.radians(15.0))])
    depth = flat_ground_depth_map(K, 320, 240, up, 1.2)
    mask = PrecomputedMaskSegmenter(label_map_to_walkable(_ground_mask_label())).predict(
        np.zeros((240, 320, 3), np.uint8))
    cfg = ShadowWalkConfig()
    tracker = ShadowWalkTracker(corridor, cfg)

    centred = tracker.update(mask_to_map_points(mask, depth, K, up, 1.2, corridor.pose_at(2.0), cfg),
                             corridor.pose_at(2.0))
    assert centred.hint in (STRAIGHT, LEFT, RIGHT)
    assert centred.n_points > cfg.min_points

    outside = tracker.update(
        mask_to_map_points(mask, depth, K, up, 1.2, corridor.pose_at(2.0, offset_m=1.2), cfg),
        corridor.pose_at(2.0, offset_m=1.2))
    assert outside.hint == STOP and outside.reason == "off_corridor"


def test_real_mask_with_no_pose_or_no_walkable_is_unknown():
    corridor = get_corridor("straight60")
    tracker = ShadowWalkTracker(corridor)
    empty = np.zeros((240, 320), np.uint8)
    assert tracker.update(mask_to_map_points(empty, np.ones((240, 320), np.float32), _camera(),
                                              np.array([0.0, -1.0, 0.0]), 1.2,
                                              corridor.pose_at(2.0)), corridor.pose_at(2.0)).hint == UNKNOWN
    assert tracker.update(np.zeros((0, 2)), None).reason == "no_pose"
