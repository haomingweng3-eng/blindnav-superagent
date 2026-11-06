from blindnav_pc.mobilio_reference import HarvardSurfaceReference


def test_reference_ray_search_keeps_five_meter_and_two_gap_parameters():
    grid = {(0, z): True for z in range(0, 31)}
    grid[(0, 10)] = False
    grid[(0, 11)] = False
    route, branches = HarvardSurfaceReference(grid).raycast_grid(0.0, 0.0)
    assert HarvardSurfaceReference.angle_diff(route, 0.0) <= 1.0
    assert branches == []


def test_reference_rejects_path_farther_than_thirty_degrees_from_target():
    grid = {(x, 0): True for x in range(-75, 76)}
    route, _ = HarvardSurfaceReference(grid).raycast_grid(0.0, 0.0)
    assert route is None
