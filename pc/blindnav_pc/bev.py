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


def apply_footprint(states: np.ndarray, cfg: BEVConfig) -> np.ndarray:
    """Mark the person's current footprint (just in front of / under the camera) as known FREE.
    Only UNKNOWN cells are changed: a measured obstacle inside the footprint stays OCCUPIED.  In place."""
    nzf = max(1, int(round(cfg.footprint_len_m / cfg.cell_m)))
    x0 = int(np.floor((-cfg.footprint_half_m + cfg.lateral_half_m) / cfg.cell_m + 1e-9))
    x1 = int(np.ceil((cfg.footprint_half_m + cfg.lateral_half_m) / cfg.cell_m - 1e-9))
    fp = states[:nzf, x0:x1]
    fp[fp == UNKNOWN] = FREE
    return states


class BEVMemory:
    """Evidence accumulation -> UNKNOWN/FREE/OCCUPIED grid, with optional ego-motion compensation.

    Three kinds of near-field knowledge (kept apart on purpose):
      * the person's *current footprint* (``footprint_*`` in BEVConfig) is known FREE (they stand there);
      * cells the camera cannot see stay UNKNOWN - they are never painted free;
      * *historical* FREE (seen earlier, now behind the blind zone) survives only if it is carried along with
        ego-motion compensation (rotate by delta-yaw, translate by steps * L_step).

    Without any orientation input (``ego=None``) the history is nearly dropped (``decay_no_ego``, default
    0.3 < threshold 0.5): the current frame dominates and old cells cannot become ghosts.
    With an unreliable delta (time gap / absurd rotation) the whole history is cleared.

    How the ego-motion compensation is done: the evidence lives in a fixed *world-frame* grid (finer than the
    BEV: ``world_res_m``) and the egocentric BEV is *sampled* from it at the current dead-reckoned pose every
    frame.  Nothing is re-sampled repeatedly, so rotation/translation cannot smear or duplicate obstacles.
    Only cells that the camera observes in the current frame are written back, and they are written with the
    fused value, so the current view always wins over history (ghost suppression: a clear current view of a
    cell removes the old obstacle there, even if the motion estimate was wrong).
    """

    WORLD_HALF_M = 15.0               # world window half-size; re-centred (lossless, integer cells) when exceeded

    def __init__(self, cfg: Optional[BEVConfig] = None, world_res_m: float = 0.05):
        self.cfg = cfg or BEVConfig()
        self.res = world_res_m
        c = self.cfg
        self._n = int(round(2 * self.WORLD_HALF_M / self.res))
        xs = (np.arange(c.nx, dtype=np.float64) + 0.5) * c.cell_m - c.lateral_half_m
        zs = (np.arange(c.nz, dtype=np.float64) + 0.5) * c.cell_m
        X, Z = np.meshgrid(xs, zs)
        q = c.cell_m * 0.25
        self._sub = [(X + dx, Z + dz) for dx in (-q, q) for dz in (-q, q)]     # 4 sub-samples per BEV cell
        self.reset()

    def reset(self) -> None:
        c = self.cfg
        self.occ_ev = np.zeros((c.nz, c.nx), dtype=np.float32)
        self.free_ev = np.zeros_like(self.occ_ev)
        self._w_occ = np.zeros((self._n, self._n), dtype=np.float32)
        self._w_free = np.zeros_like(self._w_occ)
        self.pose = np.zeros(3)           # world x, z (m, from the window centre), heading psi (rad, + right)

    # ---- pose / world mapping -----------------------------------------
    def _integrate(self, dyaw: float, dx: float, dz: float) -> None:
        """Advance the dead-reckoned pose.  (dx, dz) is the motion in the previous body frame."""
        psi = self.pose[2]
        f = np.array([np.sin(psi), np.cos(psi)])
        r = np.array([np.cos(psi), -np.sin(psi)])
        self.pose[0] += dx * r[0] + dz * f[0]
        self.pose[1] += dx * r[1] + dz * f[1]
        self.pose[2] = psi + dyaw
        lim = self.WORLD_HALF_M * 0.6
        if abs(self.pose[0]) > lim or abs(self.pose[1]) > lim:
            self._recentre()

    def _recentre(self) -> None:
        sx, sz = int(round(self.pose[0] / self.res)), int(round(self.pose[1] / self.res))
        for name in ("_w_occ", "_w_free"):
            w = getattr(self, name)
            out = np.zeros_like(w)
            ys, xs = slice(max(0, sz), min(self._n, self._n + sz)), slice(max(0, sx), min(self._n, self._n + sx))
            yd, xd = slice(max(0, -sz), min(self._n, self._n - sz)), slice(max(0, -sx), min(self._n, self._n - sx))
            out[yd, xd] = w[ys, xs]
            setattr(self, name, out)
        self.pose[0] -= sx * self.res
        self.pose[1] -= sz * self.res

    def _world_index(self):
        """Per sub-sample (iy, ix, valid) arrays: where each BEV sub-cell sits in the world grid right now."""
        psi = self.pose[2]
        f = np.array([np.sin(psi), np.cos(psi)])
        r = np.array([np.cos(psi), -np.sin(psi)])
        out = []
        for X, Z in self._sub:
            wx = self.pose[0] + X * r[0] + Z * f[0]
            wz = self.pose[1] + X * r[1] + Z * f[1]
            ix = np.floor(wx / self.res).astype(int) + self._n // 2
            iy = np.floor(wz / self.res).astype(int) + self._n // 2
            ok = (ix >= 0) & (ix < self._n) & (iy >= 0) & (iy < self._n)
            out.append((np.clip(iy, 0, self._n - 1), np.clip(ix, 0, self._n - 1), ok))
        return out

    def warp(self, dyaw_rad: float, dx_m: float, dz_m: float) -> None:
        """Move the person through the stored world by (dyaw, dx, dz) and re-sample the egocentric evidence
        (``occ_ev``/``free_ev`` are re-expressed in the new body frame)."""
        self._integrate(dyaw_rad, dx_m, dz_m)
        self._sample()

    def _sample(self) -> None:
        idx = self._world_index()
        o = np.zeros_like(self.occ_ev)
        f = np.zeros_like(self.free_ev)
        for iy, ix, ok in idx:
            o = np.maximum(o, np.where(ok, self._w_occ[iy, ix], 0.0))
            f = np.maximum(f, np.where(ok, self._w_free[iy, ix], 0.0))
        self.occ_ev, self.free_ev = o.astype(np.float32), f.astype(np.float32)

    def _write_back(self, mask: np.ndarray) -> None:
        for iy, ix, ok in self._world_index():
            m = mask & ok
            self._w_occ[iy[m], ix[m]] = self.occ_ev[m]
            self._w_free[iy[m], ix[m]] = self.free_ev[m]

    def update(self, free_cnt: np.ndarray, occ_cnt: np.ndarray, ego=None) -> np.ndarray:
        """ego: ``EgoDelta`` or None (no orientation sensor)."""
        c = self.cfg
        f_occ = occ_cnt >= c.min_points
        f_free = (free_cnt >= c.min_points) & ~f_occ
        use_world = ego is not None and ego.mode != "none"
        if not use_world:
            self.occ_ev *= c.decay_no_ego            # history nearly dropped: the current frame dominates
            self.free_ev *= c.decay_no_ego
            clears = c.free_clears_occ
        else:
            if not ego.reliable:
                self._w_occ[:] = 0
                self._w_free[:] = 0
            else:
                self._integrate(ego.dyaw_rad, ego.dx_m, ego.dz_m)
            self._w_occ *= c.decay
            self._w_free *= c.decay
            self._sample()
            clears = c.free_clears_occ_ego
        self.occ_ev[f_free] *= clears            # fusion: a clear current view overrides an old obstacle
        self.occ_ev[f_occ] += 1.0
        self.free_ev[f_free] += 1.0
        self.free_ev[f_occ] *= 0.5
        if use_world:
            self._write_back(f_occ | f_free)
        return self.states()

    def states(self) -> np.ndarray:
        c = self.cfg
        s = np.full(self.occ_ev.shape, UNKNOWN, dtype=np.uint8)
        s[self.free_ev >= c.free_thr] = FREE
        s[self.occ_ev >= c.occ_thr] = OCCUPIED   # occupied wins over free
        return apply_footprint(s, c)


def build_bev_frame(depth: np.ndarray, mask: np.ndarray, K: np.ndarray, up: np.ndarray,
                    h: float, cfg: BEVConfig) -> Tuple[np.ndarray, np.ndarray]:
    """Convenience: metric depth + walkable mask -> (free_cnt, occ_cnt) for one frame."""
    x, z, hgt, (vs, us) = depth_to_level_points(depth, K, up, h, cfg.pixel_stride)
    walk = mask[vs, us] > 0
    return points_to_bev(x, z, hgt, walk, cfg)
