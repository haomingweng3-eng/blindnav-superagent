import numpy as np
from blindnav_pc.mobilio_reference import HarvardSurfaceReference
from blindnav_pc.mobilio_occupancy_reference import HarvardOccupancyReference
from blindnav_pc.mobilio_reference_adapters import occupancy_reference_body_to_world, world_surface_cells
from blindnav_pc.mobilio_surface import SurfacePose


def test_reference_body_to_world_preserves_forward_axis():
    out = occupancy_reference_body_to_world(np.array([[0.0, 0.8, 2.0]]), SurfacePose(3.0, 4.0, 0.0))
    assert out == [(3.0, 0.8, 6.0)]


def test_reference_surface_grid_accepts_world_cells():
    ref = HarvardSurfaceReference()
    ref.grid.update({(0, z): True for z in range(31)})
    assert ref.raycast_grid_walkable(0.0, 0.0, 0.0) > 1.0


def test_surface_adapter_writes_false_cells_for_fill_grid_parity():
    roles = np.ones((2, 3), dtype=np.uint8)
    roles[0, 1] = 3
    depth = np.ones((2, 3), dtype=np.float32) * 2.0
    K = np.array([[1., 0., 1.], [0., 1., 1.], [0., 0., 1.]])
    cells = world_surface_cells(roles, depth, K, np.array([0., 1., 0.]), 2.0,
                                SurfacePose(0., 0., 0.), pixel_stride=1)
    assert False in cells.values()
