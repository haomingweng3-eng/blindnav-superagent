from blindnav_pc.mobilio_fusion import fuse
from blindnav_pc.mobilio_occupancy import OccupancyRoute
from blindnav_pc.mobilio_surface import SurfaceRoute


def test_confirmed_obstacle_overrides_surface_route():
    surface = SurfaceRoute("CLEAR", 0.0, 6.0, 0, 0, 1, True, "walkable")
    occupancy = OccupancyRoute("BLOCKED", 0.0, 0.0, 1, 0, True, "confirmed")
    decision = fuse(surface, occupancy)
    assert decision.state == "BLOCKED"
    assert "occupancy" in decision.reason


def test_unknown_surface_does_not_become_clear_from_empty_occupancy():
    surface = SurfaceRoute("UNKNOWN", None, 0.0, 3, 0, 0, False, "no observed ray")
    occupancy = OccupancyRoute("UNKNOWN", 0.0, 0.0, 0, 3, False, "unobserved")
    assert fuse(surface, occupancy).state == "UNKNOWN"
