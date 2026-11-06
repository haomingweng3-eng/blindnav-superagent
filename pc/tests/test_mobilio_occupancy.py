import numpy as np

from blindnav_pc.mobilio_occupancy import (
    OccupancyGrid3D,
    OccupancyGridConfig,
)
from blindnav_pc.mobilio_surface import SurfacePose


def test_body_point_transform_respects_pose_yaw():
    grid = OccupancyGrid3D(OccupancyGridConfig(cell_m=0.1, height_cell_m=0.1))
    pose = SurfacePose(2.0, 3.0, np.pi / 2.0)
    grid.update_body_points(np.array([[0.0, 1.0, 0.8]]), pose, frame_id=1)
    key = grid.keys()[0]
    assert key.ix == 30  # body-forward becomes +map-x at yaw +90 degrees
    assert key.iz == 30
    assert key.ih == 8


def test_height_and_range_filter_rejects_ground_below_and_far_points():
    grid = OccupancyGrid3D()
    pose = SurfacePose(0.0, 0.0, 0.0)
    update = grid.update_body_points(
        np.array([[0.0, 1.0, -0.1], [0.0, 5.0, 0.8], [0.0, 1.0, 0.8]]),
        pose, frame_id=1)
    assert update.n_accepted == 1


def test_visible_free_cell_decays_but_unobserved_cell_does_not_disappear():
    grid = OccupancyGrid3D(OccupancyGridConfig(decay=0.9, remove_below=0.1))
    pose = SurfacePose(0.0, 0.0, 0.0)
    grid.update_body_points(np.array([[0.0, 1.0, 0.8]]), pose, frame_id=1)
    key = grid.keys()[0]
    before = grid.evidence(key)
    grid.update_body_points(np.zeros((0, 3)), pose, frame_id=2,
                            visible_free_cells=[key])
    assert grid.evidence(key) == before * 0.9
    grid.update_body_points(np.zeros((0, 3)), pose, frame_id=3)
    assert grid.evidence(key) > 0.0
