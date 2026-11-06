from blindnav_pc.mobilio_occupancy_reference import HarvardOccupancyReference


def test_reference_requires_three_observations_to_block_a_cell():
    ref = HarvardOccupancyReference()
    ref.add_to_grid([(0.0, 0.8, 1.0), (0.0, 0.8, 1.0), (0.0, 0.8, 1.0)])
    direction, closest = ref.check_for_obstacle()
    assert closest < 1.5
    assert isinstance(direction, float)


def test_reference_decay_is_point_evidence_decay():
    ref = HarvardOccupancyReference()
    ref.add_to_grid([(0.0, 0.8, 1.0)] * 3)
    before = next(iter(ref.grid.values()))
    ref.decay_visible([(0, 10)])
    assert next(iter(ref.grid.values())) == before * 0.9


def test_world_translation_is_invariant_after_subtracting_phone_position():
    local = HarvardOccupancyReference()
    moved = HarvardOccupancyReference()
    local.add_to_grid([(0.0, 0.8, 1.0)] * 3)
    moved.add_to_grid([(10.0, 0.8, 6.0)] * 3)
    assert local.check_for_obstacle((0.0, 0.0))[1] == moved.check_for_obstacle((10.0, 5.0))[1]
