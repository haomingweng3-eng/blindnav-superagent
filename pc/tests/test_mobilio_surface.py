import numpy as np

from blindnav_pc.mobilio_surface import (
    ROLE_HAZARD,
    ROLE_NON_WALKABLE,
    ROLE_UNKNOWN,
    ROLE_WALKABLE,
    SurfaceGrid2D,
    SurfaceGridConfig,
    SurfacePose,
)


def test_map_points_quantize_and_preserve_strict_role_precedence():
    grid = SurfaceGrid2D(SurfaceGridConfig(cell_m=0.2))
    points = np.array([[0.04, 1.01], [0.05, 1.02], [0.06, 1.03]], dtype=float)
    roles = np.array([ROLE_WALKABLE, ROLE_NON_WALKABLE, ROLE_HAZARD], dtype=np.uint8)
    update = grid.update_points(points, roles, frame_id=7)
    assert update.n_points == 3
    assert grid.cell_role(*grid.world_to_cell(0.05, 1.02)) == ROLE_HAZARD


def test_unknown_is_not_created_as_walkable():
    grid = SurfaceGrid2D()
    grid.update_points(np.array([[0.0, 1.0]]), np.array([ROLE_UNKNOWN], dtype=np.uint8), 1)
    assert grid.cell_role(*grid.world_to_cell(0.0, 1.0)) == ROLE_UNKNOWN


def test_reset_removes_session_map():
    grid = SurfaceGrid2D()
    grid.update_points(np.array([[0.0, 1.0]]), np.array([ROLE_WALKABLE], dtype=np.uint8), 1)
    grid.reset()
    assert grid.cell_role(*grid.world_to_cell(0.0, 1.0)) == ROLE_UNKNOWN


def _paint(grid, xs, zs, role):
    grid.update_points(np.array(list(zip(xs, zs)), dtype=float),
                       np.full(len(xs), role, dtype=np.uint8), frame_id=1)


def test_raycast_stops_at_non_walkable_and_reports_observed_distance():
    grid = SurfaceGrid2D(SurfaceGridConfig(min_candidate_m=0.5))
    _paint(grid, [0.1, 0.1, 0.1], [0.3, 0.5, 0.7], ROLE_WALKABLE)
    _paint(grid, [0.1], [0.9], ROLE_NON_WALKABLE)
    center = grid._ray_candidate(SurfacePose(0.1, 0.1, 0.0), angle_rad=0.0)
    assert center.terminated_role == ROLE_NON_WALKABLE
    route = grid.raycast_route(SurfacePose(0.1, 0.1, 0.0), target_angle_rad=0.0)
    assert route.observed_distance_m < 1.2
    assert route.candidate_count > 0


def test_unknown_gap_is_flagged_and_never_fully_observed():
    grid = SurfaceGrid2D(SurfaceGridConfig(min_candidate_m=0.5, blind_zone_m=0.0))
    _paint(grid, [0.1, 0.1, 0.1, 0.1], [0.3, 0.5, 0.9, 1.1], ROLE_WALKABLE)
    route = grid.raycast_route(SurfacePose(0.1, 0.1, 0.0), target_angle_rad=0.0)
    assert route.unknown_cells > 0
    assert not route.fully_observed


def test_equal_length_candidates_prefer_target_angle():
    grid = SurfaceGrid2D(SurfaceGridConfig(min_candidate_m=4.0))
    for angle in (-10.0, 10.0):
        theta = np.deg2rad(angle)
        pts = np.array([[np.sin(theta) * r, np.cos(theta) * r]
                        for r in np.arange(0.3, 5.6, 0.2)])
        _paint(grid, pts[:, 0], pts[:, 1], ROLE_WALKABLE)
    route = grid.raycast_route(SurfacePose(0.0, 0.0, 0.0), np.deg2rad(10.0))
    assert abs(np.rad2deg(route.angle_rad) - 10.0) <= 1.1


def test_camera_blind_zone_is_explicitly_ignored_before_observed_ground():
    grid = SurfaceGrid2D(SurfaceGridConfig(
        min_candidate_m=4.0, blind_zone_m=2.0, max_gap_cells=2))
    for z in np.arange(2.2, 6.2, 0.2):
        _paint(grid, [0.1], [z], ROLE_WALKABLE)
    route = grid.raycast_route(SurfacePose(0.1, 0.1, 0.0), 0.0)
    assert route.angle_rad is not None
    assert route.observed_distance_m >= 4.0
    assert route.fully_observed
