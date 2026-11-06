import numpy as np
from blindnav_pc.mobilio_reference import HarvardSurfaceReference
from blindnav_pc.mobilio_occupancy_reference import HarvardOccupancyReference
from blindnav_pc.mobilio_reference_adapters import occupancy_reference_body_to_world
from blindnav_pc.mobilio_surface import SurfacePose


def test_reference_body_to_world_preserves_forward_axis():
    out = occupancy_reference_body_to_world(np.array([[0.0, 0.8, 2.0]]), SurfacePose(3.0, 4.0, 0.0))
    assert out == [(3.0, 0.8, 6.0)]


def test_reference_surface_grid_accepts_world_cells():
    ref = HarvardSurfaceReference()
    ref.grid.update({(0, z): True for z in range(31)})
    assert ref.raycast_grid_walkable(0.0, 0.0, 0.0) > 1.0
