"""Height layering and conservative drop-risk (step-down / pothole) detection.

Points are expressed in the level frame (x = lateral, z = forward, h = height above the estimated
ground plane).  Each point is classified into ``ground`` / ``low`` (0-0.15 m) / ``body``
(0.15-0.8 m) / ``head`` (0.8-2.0 m) / ``below`` (below the ground plane).  Per-layer occupancy is
kept on the existing BEV grid so the safety state machine can look at the walkable corridor
without changing the planner's three-state grid.

DROP_RISK needs all of:
  * a walkable-ground boundary (a below-ground cell next to ground support),
  * a local plane residual (the point is ``drop_depth_m`` below the local ground level), and
  * a depth discontinuity (metric depth jump) or a below-ground cell directly forward of ground,
for ``drop_confirm_frames`` *consecutive* frames.  Before confirmation the corridor is UNKNOWN.
A ground edge whose far side has no reliable depth is UNKNOWN too: it is never asserted to be a
pothole and it is never safe to pass.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np

from .bev import depth_to_level_points
from .config import BEVConfig, LayerConfig

GROUND, LOW, BODY, HEAD, BELOW, ABOVE = 0, 1, 2, 3, 4, 5

BLOCK_REASONS = frozenset({"drop_risk", "head_obstacle", "body_obstacle", "low_obstacle"})
UNKNOWN_REASONS = frozenset({"drop_unconfirmed", "drop_edge_unobserved", "low_obstacle_unknown"})


def classify_heights(height_rel: np.ndarray, cfg: LayerConfig) -> np.ndarray:
    """Per-point layer labels; ``height_rel`` is height above the local ground plane."""
    h = np.asarray(height_rel, dtype=np.float64)
    lab = np.full(h.shape, ABOVE, dtype=np.int8)
    lab[np.abs(h) <= cfg.ground_band_m] = GROUND
    lab[(h > cfg.ground_band_m) & (h <= cfg.low_max_m)] = LOW
    lab[(h > cfg.low_max_m) & (h <= cfg.body_max_m)] = BODY
    lab[(h > cfg.body_max_m) & (h <= cfg.head_max_m)] = HEAD
    lab[h < -cfg.ground_band_m] = BELOW
    return lab


@dataclass
class LayerObservation:
    """Per-cell layer support for one frame."""
    n_points: np.ndarray                 # any finite-depth point in the cell
    ground: np.ndarray                   # walkable ground support
    low: np.ndarray
    body: np.ndarray
    head: np.ndarray
    below: np.ndarray
    cell_depth: np.ndarray               # mean metric z-depth (0 where unseen)
    ground_depth: np.ndarray             # mean ground-point depth (0 where no ground support)


@dataclass
class HazardAssessment:
    observation: LayerObservation
    drop_candidate: np.ndarray
    drop_unconfirmed: np.ndarray
    drop_risk: np.ndarray
    edge_unknown: np.ndarray
    reasons: tuple = ()
    corridor_low_cells: int = 0
    corridor_body_cells: int = 0
    corridor_head_cells: int = 0
    corridor_drop_risk_cells: int = 0
    corridor_drop_unconfirmed_cells: int = 0
    corridor_edges_unobserved: int = 0

    def to_log(self) -> dict:
        return {
            "layer_low_cells": int(self.corridor_low_cells),
            "layer_body_cells": int(self.corridor_body_cells),
            "layer_head_cells": int(self.corridor_head_cells),
            "drop_risk_cells": int(self.corridor_drop_risk_cells),
            "drop_unconfirmed_cells": int(self.corridor_drop_unconfirmed_cells),
            "drop_unobserved_cells": int(self.corridor_edges_unobserved),
            "hazard_reasons": list(self.reasons),
        }


def _shift(mask: np.ndarray, dr: int, dc: int) -> np.ndarray:
    """Shift ``mask`` by (dr, dc) (positive = down/right in the array), zero-filled."""
    out = np.zeros_like(mask)
    nz, nx = mask.shape
    src_r = slice(max(0, -dr), nz - max(0, dr))
    dst_r = slice(max(0, dr), nz - max(0, -dr))
    src_c = slice(max(0, -dc), nx - max(0, dc))
    dst_c = slice(max(0, dc), nx - max(0, -dc))
    out[dst_r, dst_c] = mask[src_r, src_c]
    return out


def _max_run(mask: np.ndarray) -> int:
    """Longest contiguous run of True along the lateral axis (per row), in cells."""
    best = 0
    for row in np.asarray(mask, dtype=bool):
        run = 0
        for v in row:
            run = run + 1 if v else 0
            best = max(best, run)
    return best


class HeightLayerAnalyzer:
    """Stateful per-frame height layering + multi-frame drop evidence."""

    def __init__(self, cfg: Optional[LayerConfig] = None, bev: Optional[BEVConfig] = None):
        self.cfg = cfg or LayerConfig()
        self.bev = bev or BEVConfig()
        self.reset()

    def reset(self) -> None:
        shape = (self.bev.nz, self.bev.nx)
        self._drop_streak = np.zeros(shape, np.int32)
        self._drop_hold = np.zeros(shape, np.int32)
        self._edge_streak = np.zeros(shape, np.int32)

    def update(self, depth: np.ndarray, mask: np.ndarray, K: np.ndarray, up: np.ndarray,
               camera_height_m: float) -> HazardAssessment:
        x, z, hgt, (vs, us) = depth_to_level_points(depth, K, up, camera_height_m,
                                                    self.bev.pixel_stride)
        walk = np.asarray(mask)[vs, us] > 0
        d = np.asarray(depth, dtype=np.float64)[vs, us]
        return self.analyze_points(x, z, hgt, walk, d)

    # ---- core ---------------------------------------------------------
    def analyze_points(self, x_lat, z_fwd, height, walkable,
                       depth: Optional[np.ndarray] = None) -> HazardAssessment:
        """Analyse a level-frame point cloud.  ``depth`` is metric z-depth (used only for the
        depth-discontinuity cue; defaults to ``z_fwd`` so tests can omit it)."""
        c, b = self.cfg, self.bev
        nz, nx = b.nz, b.nx
        x = np.asarray(x_lat, dtype=np.float64).ravel()
        z = np.asarray(z_fwd, dtype=np.float64).ravel()
        h = np.asarray(height, dtype=np.float64).ravel()
        walk = np.asarray(walkable, dtype=bool).ravel()
        dep = z.copy() if depth is None else np.asarray(depth, dtype=np.float64).ravel()
        ok = np.isfinite(x) & np.isfinite(z) & np.isfinite(h) & np.isfinite(dep) & (dep > 0)

        # Height relative to the local ground plane (median of walkable ground points).
        g_sel = ok & walk & (np.abs(h) <= c.ground_band_m)
        offset = float(np.median(h[g_sel])) if int(g_sel.sum()) >= c.min_ground_points else 0.0
        h_rel = h - offset
        label = classify_heights(h_rel, c)

        ix = np.floor((x + b.lateral_half_m) / b.cell_m).astype(int)
        iz = np.floor(z / b.cell_m).astype(int)
        inside = ok & (ix >= 0) & (ix < nx) & (iz >= 0) & (iz < nz)
        ix, iz = ix[inside], iz[inside]
        lab, rel, dep = label[inside], h_rel[inside], dep[inside]
        walk_in = walk[inside]

        shape = (nz, nx)
        counts = np.zeros((6, nz, nx), np.int32)
        np.add.at(counts, (lab, iz, ix), 1)
        n_points = np.zeros(shape, np.int32)
        np.add.at(n_points, (iz, ix), 1)
        dsum = np.zeros(shape, np.float64)
        np.add.at(dsum, (iz, ix), dep)
        cell_depth = np.divide(dsum, np.maximum(n_points, 1), out=np.zeros(shape), where=n_points > 0)

        ground_pts = walk_in & (lab == GROUND)
        gsum = np.zeros(shape, np.float64)
        gcnt = np.zeros(shape, np.int32)
        np.add.at(gsum, (iz[ground_pts], ix[ground_pts]), dep[ground_pts])
        np.add.at(gcnt, (iz[ground_pts], ix[ground_pts]), 1)
        ground = gcnt >= c.min_ground_points
        ground_depth = np.divide(gsum, np.maximum(gcnt, 1), out=np.zeros(shape),
                                 where=gcnt > 0)

        # Below-ground evidence: local plane residual + minimum drop depth.
        residual = -rel
        deep = residual >= max(c.drop_depth_m, c.plane_tol_m)
        below_pts = (lab == BELOW) & deep
        n_below = np.zeros(shape, np.int32)
        np.add.at(n_below, (iz[below_pts], ix[below_pts]), 1)
        below = n_below >= c.min_points

        low_cell = counts[LOW] >= c.min_points
        body = counts[BODY] >= c.min_points
        head = counts[HEAD] >= c.min_points

        obs = LayerObservation(n_points, ground, low_cell, body, head, below,
                               cell_depth, ground_depth)

        drop_cand = self._drop_candidates(ground, below, cell_depth, ground_depth)
        drop_risk, drop_unconfirmed = self._update_drop(drop_cand)
        edge_unknown = self._update_edges(ground, below, n_points)

        reasons, n_low, n_body, n_head, n_drop, n_drop_u, n_edge = self._corridor(
            obs, drop_risk, drop_unconfirmed, edge_unknown)
        return HazardAssessment(obs, drop_cand, drop_unconfirmed, drop_risk, edge_unknown,
                                reasons, n_low, n_body, n_head, n_drop, n_drop_u, n_edge)

    # ---- drop evidence ------------------------------------------------
    def _drop_candidates(self, ground, below, cell_depth, ground_depth) -> np.ndarray:
        c = self.cfg
        if not ground.any() or not below.any():
            return np.zeros_like(below)
        # The edge can be occluded: the lower ground is seen only some distance beyond the
        # walkable frontier.  Look back up to ``drop_edge_gap_m`` for the ground boundary.
        lookback = max(1, int(round(c.drop_edge_gap_m / self.bev.cell_m)))
        near_ground = ground.copy()
        for dr, dc in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            near_ground |= _shift(ground, dr, dc)
        ground_behind = np.zeros_like(ground)
        for d in range(1, lookback + 1):
            ground_behind |= _shift(ground, d, 0)    # ground at row i-d, seen from row i
        boundary = below & (near_ground | ground | ground_behind)
        # Metric-depth discontinuity to nearby / behind ground.
        jump = np.zeros_like(cell_depth)
        offsets = [(dr, dc) for dr in (-2, -1, 0, 1, 2) for dc in (-1, 0, 1)]
        offsets += [(d, 0) for d in range(1, lookback + 1)]
        for dr, dc in offsets:
            gd = _shift(ground_depth, dr, dc)
            gs = _shift(ground, dr, dc)
            jump = np.maximum(jump, np.where(gs, np.abs(cell_depth - gd), 0.0))
        discontinuous = jump >= c.depth_jump_m
        return boundary & (discontinuous | ground_behind)

    def _update_drop(self, candidate: np.ndarray):
        c = self.cfg
        self._drop_streak = np.where(candidate, self._drop_streak + 1, 0)
        confirmed = self._drop_streak >= c.drop_confirm_frames
        self._drop_hold = np.where(confirmed, c.drop_hold_frames,
                                   np.maximum(self._drop_hold - 1, 0))
        risk = confirmed | (self._drop_hold > 0)
        unconfirmed = candidate & ~risk
        return risk, unconfirmed

    def _update_edges(self, ground: np.ndarray, below: np.ndarray,
                      n_points: np.ndarray) -> np.ndarray:
        """Ground frontier cells whose forward space has no reliable depth at all.

        A gap of one or two empty cells does not by itself make the space unknown: ground may
        continue (sparse sampling) or lower ground may be seen beyond an occluding edge.  Only a
        frontier with neither ground nor below-ground evidence within ``edge_probe_m`` is
        UNKNOWN.  Non-ground obstacle points immediately ahead are not an unseen edge."""
        c = self.cfg
        nz = ground.shape[0]
        z = (np.arange(nz) + 0.5) * self.bev.cell_m
        probe = max(1, int(round(c.edge_probe_m / self.bev.cell_m)))
        edge = np.zeros_like(ground)
        for i in range(nz - 1):
            g = ground[i]
            if not g.any():
                continue
            j = min(nz, i + 1 + probe)
            ground_ahead = ground[i + 1:j].any(axis=0)
            below_ahead = below[i + 1:j].any(axis=0)
            unseen_next = n_points[i + 1] == 0
            edge[i + 1] |= g & unseen_next & ~ground_ahead & ~below_ahead
        edge &= (z <= c.max_edge_depth_m)[:, None]
        self._edge_streak = np.where(edge, self._edge_streak + 1, 0)
        return self._edge_streak >= c.edge_unknown_frames

    # ---- corridor -----------------------------------------------------
    def _corridor(self, obs: LayerObservation, drop_risk, drop_unconfirmed, edge_unknown):
        c, b = self.cfg, self.bev
        nz, nx = b.nz, b.nx
        rows = min(nz, max(1, int(round(c.corridor_len_m / b.cell_m))))
        x0 = int(np.floor((-c.corridor_half_width_m + b.lateral_half_m) / b.cell_m + 1e-9))
        x1 = int(np.ceil((c.corridor_half_width_m + b.lateral_half_m) / b.cell_m - 1e-9))
        x0, x1 = max(0, x0), min(nx, x1)
        corr = np.zeros((nz, nx), dtype=bool)
        corr[:rows, x0:x1] = True

        low = obs.low & corr
        n_low = int(low.sum())
        n_body = int((obs.body & corr).sum())
        n_head = int((obs.head & corr).sum())
        risk = drop_risk & corr
        unconf = drop_unconfirmed & corr
        edges = edge_unknown & corr

        reasons = []
        if n_head:
            reasons.append("head_obstacle")
        if n_body:
            reasons.append("body_obstacle")
        if n_low:
            large = n_low >= c.low_block_cells or _max_run(low) * b.cell_m >= c.low_block_width_m
            if large:
                reasons.append("low_obstacle")
            else:
                # A small low obstacle is ignored only if the ground beyond it is observed;
                # otherwise its extent is unknown -> UNKNOWN, never CLEAR.
                low_fwd = obs.low & _shift(obs.n_points == 0, -1, 0) & corr
                if low_fwd.any():
                    reasons.append("low_obstacle_unknown")
        if risk.any():
            reasons.append("drop_risk")
        elif unconf.any():
            reasons.append("drop_unconfirmed")
        if edges.any():
            reasons.append("drop_edge_unobserved")
        return (tuple(reasons), n_low, n_body, n_head, int(risk.sum()),
                int(unconf.sum()), int(edges.sum()))


def empty_hazards_log() -> dict:
    return {"layer_low_cells": 0, "layer_body_cells": 0, "layer_head_cells": 0,
            "drop_risk_cells": 0, "drop_unconfirmed_cells": 0,
            "drop_unobserved_cells": 0, "hazard_reasons": []}
