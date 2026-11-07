import numpy as np

from blindnav_pc.mobilio_surface import (
    ROLE_NON_WALKABLE,
    ROLE_WALKABLE,
    SurfaceGrid2D,
    SurfaceGridConfig,
    SurfacePose,
)


def test_surface_route_keeps_ray_termination_diagnostics():
    grid = SurfaceGrid2D(SurfaceGridConfig(min_candidate_m=0.5, blind_zone_m=0.0))
    grid.update_points(np.array([[0.1, 0.3], [0.1, 0.5], [0.1, 0.7]]),
                       np.array([ROLE_WALKABLE] * 3), frame_id=1)
    grid.update_points(np.array([[0.1, 0.9]]), np.array([ROLE_NON_WALKABLE]), frame_id=1)

    route = grid._ray_candidate(SurfacePose(0.1, 0.1, 0.0), 0.0)

    assert route.termination_distance_m > route.last_walkable_distance_m
    assert route.termination_cell is not None
    assert route.termination_cell != route.last_walkable_cell


def test_surface_no_candidate_reports_empty_diagnostics():
    grid = SurfaceGrid2D(SurfaceGridConfig(min_candidate_m=5.0))
    route = grid.raycast_route(SurfacePose(0.0, 0.0, 0.0), 0.0)

    assert route.termination_distance_m == 0.0
    assert route.last_walkable_distance_m == 0.0
    assert route.termination_cell is None
    assert route.last_walkable_cell is None
