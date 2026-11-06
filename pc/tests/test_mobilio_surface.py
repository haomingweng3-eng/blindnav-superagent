import numpy as np

from blindnav_pc.mobilio_surface import (
    ROLE_HAZARD,
    ROLE_NON_WALKABLE,
    ROLE_UNKNOWN,
    ROLE_WALKABLE,
    SurfaceGrid2D,
    SurfaceGridConfig,
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
