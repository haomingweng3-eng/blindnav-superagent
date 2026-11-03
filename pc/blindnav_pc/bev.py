"""Metric depth -> gravity-aligned bird's-eye-view (BEV) occupancy grid with short memory."""
from __future__ import annotations

from typing import Optional, Tuple

import numpy as np

from .config import BEVConfig, FREE, OCCUPIED, UNKNOWN
from .ground import pixel_rays


def level_frame(up: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    """Return (forward, right) unit vectors (in camera coordinates) of the level frame.

    forward = camera z axis projected onto the horizontal plane; right = forward x up.
    (Upright camera: up=(0,-1,0), forward=(0,0,1) -> right=(1,0,0).)
    """
    up = np.asarray(up, dtype=np.float64)
    z = np.array([0.0, 0.0, 1.0])
    f = z - np.dot(z, up) * up
    if np.linalg.norm(f) < 1e-3:       # looking straight up/down: use the camera y axis
        y = np.array([0.0, 1.0, 0.0])
        f = y - np.dot(y, up) * up
    f /= np.linalg.norm(f)
    right = np.cross(f, up)
    return f, right / np.linalg.norm(right)


def depth_to_level_points(depth: np.ndarray, K: np.ndarray, up: np.ndarray, h: float,
                          stride: int = 4):
    """Back-project metric z-depth to the level frame.

    Returns (x_lat, z_fwd, height, (vs, us)) for finite-depth pixels (sub-sampled by stride).
    height = up . X + h  (0 on the ground plane under the camera-height assumption).
    """
    H, W = depth.shape[:2]
    d = depth[::stride, ::stride].astype(np.float64)
    rays = pixel_rays(K, W, H, stride)[: d.shape[0], : d.shape[1]]
    valid = np.isfinite(d) & (d > 0)
    vs, us = np.nonzero(valid)
    X = rays[vs, us] * d[vs, us][:, None]
    f, right = level_frame(up)
    x_lat = X @ right
    z_fwd = X @ f
    height = X @ np.asarray(up, dtype=np.float64) + h
    return x_lat, z_fwd, height, (vs * stride, us * stride)


def points_to_bev(x_lat, z_fwd, height, walkable, cfg: BEVConfig) -> Tuple[np.ndarray, np.ndarray]:
    """Rasterise classified points.  Returns (free_count, occ_count) arrays (nz, nx).

    walkable: bool array per point (segmentation says walkable ground at that pixel).
    """
    nx, nz = cfg.nx, cfg.nz
    ix = np.floor((x_lat + cfg.lateral_half_m) / cfg.cell_m).astype(int)
    iz = np.floor(z_fwd / cfg.cell_m).astype(int)
    inside = (ix >= 0) & (ix < nx) & (iz >= 0) & (iz < nz)
    ground = np.abs(height) < cfg.ground_tol_m
    obstacle = (height >= cfg.obstacle_min_m) & (height <= cfg.obstacle_max_m)
    drop = height < cfg.drop_m
    bad_ground = ground & ~walkable if cfg.non_walkable_ground_is_occupied else np.zeros_like(ground)
    occ = inside & (obstacle | drop | bad_ground)
    free = inside & ground & walkable
    occ_cnt = np.zeros((nz, nx), dtype=np.int32)
    free_cnt = np.zeros((nz, nx), dtype=np.int32)
    np.add.at(occ_cnt, (iz[occ], ix[occ]), 1)
    np.add.at(free_cnt, (iz[free], ix[free]), 1)
    return free_cnt, occ_cnt


class BEVMemory:
    """Evidence accumulation with per-frame decay (default 0.9) -> UNKNOWN/FREE/OCCUPIED grid.

    No ego-motion compensation in V0.1: memory only smooths flicker over a few frames.
    """

    def __init__(self, cfg: Optional[BEVConfig] = None):
        self.cfg = cfg or BEVConfig()
        self.reset()

    def reset(self) -> None:
        self.occ_ev = np.zeros((self.cfg.nz, self.cfg.nx), dtype=np.float32)
        self.free_ev = np.zeros_like(self.occ_ev)

    def update(self, free_cnt: np.ndarray, occ_cnt: np.ndarray) -> np.ndarray:
        c = self.cfg
        f_occ = occ_cnt >= c.min_points
        f_free = (free_cnt >= c.min_points) & ~f_occ
        self.occ_ev *= c.decay
        self.free_ev *= c.decay
        self.occ_ev[f_free] *= c.free_clears_occ
        self.occ_ev[f_occ] += 1.0
        self.free_ev[f_free] += 1.0
        return self.states()

    def states(self) -> np.ndarray:
        c = self.cfg
        s = np.full(self.occ_ev.shape, UNKNOWN, dtype=np.uint8)
        s[self.free_ev >= c.free_thr] = FREE
        s[self.occ_ev >= c.occ_thr] = OCCUPIED   # occupied wins over free
        return s


def build_bev_frame(depth: np.ndarray, mask: np.ndarray, K: np.ndarray, up: np.ndarray,
                    h: float, cfg: BEVConfig) -> Tuple[np.ndarray, np.ndarray]:
    """Convenience: metric depth + walkable mask -> (free_cnt, occ_cnt) for one frame."""
    x, z, hgt, (vs, us) = depth_to_level_points(depth, K, up, h, cfg.pixel_stride)
    walk = mask[vs, us] > 0
    return points_to_bev(x, z, hgt, walk, cfg)
