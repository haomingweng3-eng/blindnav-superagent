import numpy as np

from blindnav_pc.bev import BEVMemory, build_bev_frame, depth_to_level_points, level_frame, points_to_bev
from blindnav_pc.config import BEVConfig, FREE, OCCUPIED, UNKNOWN
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
    s = mem.update(free, occ)
    assert s[20, 15] == OCCUPIED
    ev0 = mem.occ_ev[20, 15]
    mem.update(np.zeros_like(free), np.zeros_like(occ))
    assert np.isclose(mem.occ_ev[20, 15], ev0 * 0.9)
    for _ in range(10):
        s = mem.update(np.zeros_like(free), np.zeros_like(occ))
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
    assert (BEVMemory(cfg).update(free, occ) == UNKNOWN).all()
