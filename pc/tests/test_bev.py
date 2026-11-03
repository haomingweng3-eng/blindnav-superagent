import numpy as np

from blindnav_pc.bev import BEVMemory, build_bev_frame, depth_to_level_points, level_frame, points_to_bev
from blindnav_pc.bev import apply_footprint
from blindnav_pc.config import BEVConfig, FREE, OCCUPIED, UNKNOWN
from blindnav_pc.ego import EgoDelta
from blindnav_pc.ground import flat_ground_depth, pixel_rays, up_from_gravity

K = np.array([[250.0, 0, 160.0], [0, 250.0, 120.0], [0, 0, 1.0]])


def test_level_frame_upright_and_pitched():
    f, r = level_frame(np.array([0.0, -1.0, 0.0]))
    assert np.allclose(f, [0, 0, 1]) and np.allclose(r, [1, 0, 0])
    p = np.deg2rad(20)
    up = up_from_gravity([0, np.cos(p), np.sin(p)])
    f, r = level_frame(up)
    assert abs(f @ up) < 1e-9 and abs(r @ up) < 1e-9 and np.isclose(r[0], 1.0)


def test_projection_of_ground_and_obstacle_points():
    cfg = BEVConfig()
    x = np.array([0.0, 0.5, -1.0, 0.0, 9.0])
    z = np.array([1.0, 2.0, 3.0, 2.05, 1.0])
    h = np.array([0.0, 0.02, -0.03, 0.8, 0.0])     # ground, ground, ground, obstacle, outside lateral range
    walk = np.array([True, True, True, True, True])
    free, occ = points_to_bev(x, z, h, walk, cfg)
    assert free[10, 15] == 1                         # x=0 -> col 15, z=1 -> row 10
    assert free[20, 20] == 1                         # x=.5 -> col 20
    assert free[30, 5] == 1                          # x=-1 -> col 5
    assert occ[20, 15] == 1                          # obstacle
    assert free.sum() == 3 and occ.sum() == 1        # point outside the grid is dropped


def test_non_walkable_ground_is_occupied_and_drop():
    cfg = BEVConfig()
    free, occ = points_to_bev(np.array([0.0, 0.3]), np.array([1.0, 1.5]), np.array([0.0, -0.6]),
                              np.array([False, True]), cfg)
    assert occ.sum() == 2 and free.sum() == 0         # grass-like ground and a drop-off are not free


def test_flat_ground_depth_image_yields_free_floor():
    cfg = BEVConfig()
    p = np.deg2rad(15)
    up = up_from_gravity([0, np.cos(p), np.sin(p)])
    rays = pixel_rays(K, 320, 240)
    depth = flat_ground_depth(rays, up, 1.2).astype(np.float32)
    depth[depth > 8] = np.nan
    mask = np.ones((240, 320), np.uint8)
    free, occ = build_bev_frame(depth, mask, K, up, 1.2, cfg)
    assert occ.sum() == 0
    states = BEVMemory(cfg).update(free, occ)
    assert (states == FREE).sum() > 100


def test_decay_09_and_occupied_wins():
    cfg = BEVConfig()
    mem = BEVMemory(cfg)
    free = np.zeros((cfg.nz, cfg.nx), np.int32)
    occ = np.zeros_like(free)
    occ[20, 15] = 5
    free[20, 15] = 5
    still = EgoDelta(mode="full")                    # orientation available, not moving
    s = mem.update(free, occ, still)
    assert s[20, 15] == OCCUPIED
    ev0 = mem.occ_ev[20, 15]
    mem.update(np.zeros_like(free), np.zeros_like(occ), still)
    assert np.isclose(mem.occ_ev[20, 15], ev0 * 0.9)
    for _ in range(10):
        s = mem.update(np.zeros_like(free), np.zeros_like(occ), still)
    assert s[20, 15] == UNKNOWN                       # unobserved evidence fades away -> unknown, never free
    # free observation clears occupied evidence
    mem.update(free * 0, occ)
    mem.update(free, np.zeros_like(occ))
    assert mem.occ_ev[20, 15] < 1.0


def test_unknown_for_no_depth():
    cfg = BEVConfig()
    depth = np.full((240, 320), np.nan, np.float32)
    free, occ = build_bev_frame(depth, np.ones((240, 320), np.uint8), K, np.array([0, -1.0, 0]), 1.2, cfg)
    assert free.sum() == 0 and occ.sum() == 0
    st = BEVMemory(cfg).update(free, occ)
    assert (st == FREE).sum() == (apply_footprint(np.full_like(st, UNKNOWN), cfg) == FREE).sum()   # only the footprint is known
    assert (st == OCCUPIED).sum() == 0


# ---------------- three-class near field + ego-motion warp ----------------
def _grid(cfg):
    return np.zeros((cfg.nz, cfg.nx), np.int32)


def _cell(cfg, x, z):
    return int(z / cfg.cell_m), int((x + cfg.lateral_half_m) / cfg.cell_m)


def test_footprint_known_free_but_rest_of_near_field_unknown():
    cfg = BEVConfig()
    s = BEVMemory(cfg).update(_grid(cfg), _grid(cfg), None)
    assert s[_cell(cfg, 0.0, 0.1)] == FREE                    # the person's footprint
    assert s[_cell(cfg, 0.0, 0.8)] == UNKNOWN                 # not seen -> unknown, never painted free
    assert s[_cell(cfg, 0.9, 0.1)] == UNKNOWN                 # beside the footprint
    t = np.full((cfg.nz, cfg.nx), UNKNOWN, np.uint8)
    t[_cell(cfg, 0.0, 0.1)] = OCCUPIED                        # a measured obstacle in the footprint stays
    assert apply_footprint(t, cfg)[_cell(cfg, 0.0, 0.1)] == OCCUPIED


def test_no_ego_history_is_dropped():
    cfg = BEVConfig()
    mem = BEVMemory(cfg)
    f = _grid(cfg)
    f[_cell(cfg, 0.0, 1.0)] = 5
    assert mem.update(f, _grid(cfg), None)[_cell(cfg, 0.0, 1.0)] == FREE
    assert mem.update(_grid(cfg), _grid(cfg), None)[_cell(cfg, 0.0, 1.0)] == UNKNOWN   # current frame dominates
    assert mem.update(f, _grid(cfg), EgoDelta(mode="none"))[_cell(cfg, 0.0, 1.0)] == FREE
    assert mem.update(_grid(cfg), _grid(cfg), EgoDelta(mode="none"))[_cell(cfg, 0.0, 1.0)] == UNKNOWN


def test_historical_free_carried_into_blind_zone_only_with_ego():
    cfg = BEVConfig()
    f = _grid(cfg)
    f[_cell(cfg, 0.0, 1.0)] = 5
    for ego, expect in ((EgoDelta(dz_m=0.5, steps=1, mode="full"), FREE), (None, UNKNOWN)):
        mem = BEVMemory(cfg)
        mem.update(f, _grid(cfg), EgoDelta(mode="full") if ego is not None else None)
        s = mem.update(_grid(cfg), _grid(cfg), ego)           # walked 0.5 m: that cell is now at z ~ 0.5 (camera blind zone)
        assert s[_cell(cfg, 0.0, 0.55)] == expect


def test_warp_translation_and_rotation_geometry():
    cfg = BEVConfig()
    mem = BEVMemory(cfg)
    o = _grid(cfg)
    o[_cell(cfg, 0.0, 2.05)] = 5
    mem.update(_grid(cfg), o, EgoDelta(mode="full"))
    mem.update(_grid(cfg), _grid(cfg), EgoDelta(dz_m=0.7, mode="full"))
    iz, ix = np.nonzero(mem.occ_ev > 0.5)
    assert len(iz) == 1 and abs((iz[0] + 0.5) * cfg.cell_m - 1.35) < 0.11 and abs((ix[0] + 0.5) * cfg.cell_m - 1.5) < 0.11
    # turning right by 30 deg: an obstacle straight ahead at 2 m moves to the LEFT of the new view
    mem = BEVMemory(cfg)
    mem.update(_grid(cfg), o, EgoDelta(mode="full"))
    mem.update(_grid(cfg), _grid(cfg), EgoDelta(dyaw_rad=np.deg2rad(30), mode="rot_only"))
    iz, ix = np.nonzero(mem.occ_ev > 0.5)
    x = (ix.mean() + 0.5) * cfg.cell_m - cfg.lateral_half_m
    z = (iz.mean() + 0.5) * cfg.cell_m
    assert abs(x - (-2.05 * np.sin(np.deg2rad(30)))) < 0.2 and abs(z - 2.05 * np.cos(np.deg2rad(30))) < 0.2


def test_unreliable_motion_drops_history():
    cfg = BEVConfig()
    mem = BEVMemory(cfg)
    o = _grid(cfg)
    o[_cell(cfg, 0.0, 2.0)] = 5
    mem.update(_grid(cfg), o, EgoDelta(mode="full"))
    mem.update(_grid(cfg), _grid(cfg), EgoDelta(mode="full", reliable=False))
    assert mem.occ_ev.max() == 0 and mem.free_ev.max() == 0


def test_walker_shifts_right_old_obstacle_does_not_duplicate():
    """Obstacle seen at x=+0.2; the walker then steps 0.5 m to the right.  The obstacle must now be seen
    (and remembered) at x=-0.3 ONLY - no ghost left behind at the old position."""
    cfg = BEVConfig()
    z = 2.0
    old, new = _cell(cfg, 0.2, z), _cell(cfg, -0.3, z)
    occ0 = _grid(cfg)
    occ0[old] = 5
    free_row = _grid(cfg)
    free_row[int(z / cfg.cell_m), :] = 5
    free_row[old] = 0
    # (a) correct ego-motion (sideways step 0.5 m = dx)
    mem = BEVMemory(cfg)
    mem.update(free_row, occ0, EgoDelta(mode="full"))
    occ1, free1 = _grid(cfg), free_row.copy()
    occ1[new] = 5
    free1[new] = 0
    free1[old] = 5                                               # the old position is now seen as free floor
    s = mem.update(free1, occ1, EgoDelta(dx_m=0.5, mode="full"))
    occ_cells = {tuple(c) for c in np.argwhere(s == OCCUPIED)}
    assert occ_cells == {new}
    # (b) the sensor missed the sideways step (rotation-only warp): the stale obstacle would stay at the old cell,
    #     but the current view sees free floor there -> fusion removes it immediately (no ghost)
    mem = BEVMemory(cfg)
    mem.update(free_row, occ0, EgoDelta(mode="full"))
    s = mem.update(free1, occ1, EgoDelta(mode="rot_only"))
    assert {tuple(c) for c in np.argwhere(s == OCCUPIED)} == {new}
