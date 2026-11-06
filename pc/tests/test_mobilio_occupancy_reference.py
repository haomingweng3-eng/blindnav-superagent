from blindnav_pc.mobilio_occupancy_reference import HarvardOccupancyReference


def test_reference_requires_three_observations_to_block_a_cell():
    ref = HarvardOccupancyReference()
    ref.add_to_grid([(0.0, -1.0, 0.8), (0.0, -1.0, 0.8), (0.0, -1.0, 0.8)])
    direction, closest = ref.check_for_obstacle()
    assert closest < 1.5
    assert isinstance(direction, float)


def test_reference_decay_is_point_evidence_decay():
    ref = HarvardOccupancyReference()
    ref.add_to_grid([(0.0, -1.0, 0.8)] * 3)
    before = next(iter(ref.grid.values()))
    ref.decay_visible([(0, 8)])
    assert next(iter(ref.grid.values())) == before * 0.9
