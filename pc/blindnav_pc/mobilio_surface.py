"""Persistent map-frame surface evidence and route search.

This module is the PC equivalent of the useful part of Mobilio's ``Vision.cs``:
semantic observations are written into a ground-aligned world grid and can be
searched independently of the current camera image.  It deliberately has no
AR Foundation, LiDAR, model, or SANPO dependency.  Callers provide the role map,
metric depth, camera intrinsics, gravity-aligned camera frame, and a small 2-D
pose value object.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, Iterable, Optional, Tuple

import numpy as np

from .bev import depth_to_level_points


ROLE_UNKNOWN, ROLE_WALKABLE, ROLE_HAZARD, ROLE_NON_WALKABLE = 0, 1, 2, 3

_ROLE_PRIORITY = {
    ROLE_UNKNOWN: 0,
    ROLE_WALKABLE: 1,
    ROLE_NON_WALKABLE: 2,
    ROLE_HAZARD: 3,
}
_KNOWN_ROLES = frozenset(_ROLE_PRIORITY)


@dataclass(frozen=True)
class SurfaceGridConfig:
    """A-priori grid and evidence parameters."""

    cell_m: float = 0.20
    half_extent_m: float = 15.0
    ground_band_m: float = 0.15
    max_range_m: float = 15.0
    evidence_decay: float = 0.90
    stale_after_frames: int = 15

    def __post_init__(self) -> None:
        if self.cell_m <= 0 or self.half_extent_m <= 0 or self.max_range_m <= 0:
            raise ValueError("surface grid dimensions must be positive")
        if not 0.0 < self.evidence_decay <= 1.0:
            raise ValueError("evidence_decay must be in (0, 1]")
        if self.ground_band_m < 0 or self.stale_after_frames < 0:
            raise ValueError("ground band and stale frame count must be non-negative")


@dataclass(frozen=True)
class SurfacePose:
    """Map-frame pose: x is right, z is forward, yaw is positive to the right."""

    x_m: float
    z_m: float
    yaw_rad: float


@dataclass(frozen=True)
class SurfaceUpdate:
    n_points: int
    n_cells: int
    n_unknown: int


@dataclass(frozen=True)
class SurfaceRoute:
    state: str
    angle_rad: Optional[float]
    observed_distance_m: float
    unknown_cells: int
    terminated_role: int
    candidate_count: int
    fully_observed: bool
    reason: str


@dataclass
class _CellEvidence:
    scores: Dict[int, float] = field(default_factory=dict)
    last_frame: int = -1

    def add(self, role: int, weight: float, frame_id: int) -> None:
        self.scores[role] = self.scores.get(role, 0.0) + float(weight)
        self.last_frame = int(frame_id)

    def decay(self, factor: float) -> None:
        for role in tuple(self.scores):
            self.scores[role] *= factor
            if self.scores[role] < 1e-4:
                del self.scores[role]

    def resolve(self) -> int:
        if not self.scores:
            return ROLE_UNKNOWN
        return max(self.scores, key=lambda role: (self.scores[role], _ROLE_PRIORITY[role]))


class SurfaceGrid2D:
    """Persistent semantic surface grid in a session map frame."""

    def __init__(self, cfg: Optional[SurfaceGridConfig] = None):
        self.cfg = cfg or SurfaceGridConfig()
        self.reset()

    def reset(self) -> None:
        self._cells: Dict[Tuple[int, int], _CellEvidence] = {}
        self._last_frame = -1

    def world_to_cell(self, x_m: float, z_m: float) -> Tuple[int, int]:
        c = self.cfg.cell_m
        return int(math.floor(float(x_m) / c)), int(math.floor(float(z_m) / c))

    def cell_to_world(self, ix: int, iz: int) -> Tuple[float, float]:
        c = self.cfg.cell_m
        return (float(ix + 0.5) * c, float(iz + 0.5) * c)

    def _in_window(self, x_m: float, z_m: float) -> bool:
        limit = self.cfg.half_extent_m
        return abs(float(x_m)) <= limit and abs(float(z_m)) <= limit

    def _age_cells(self, frame_id: int, touched: Iterable[Tuple[int, int]]) -> None:
        touched_set = set(touched)
        if self._last_frame < 0:
            return
        for key, cell in tuple(self._cells.items()):
            if key in touched_set or cell.last_frame < 0:
                continue
            delta = max(0, int(frame_id) - int(cell.last_frame))
            if delta:
                cell.decay(self.cfg.evidence_decay ** delta)
            if not cell.scores or delta > self.cfg.stale_after_frames:
                del self._cells[key]

    def update_points(self, points_map: np.ndarray, roles: np.ndarray,
                      frame_id: int, confidence: Optional[np.ndarray] = None) -> SurfaceUpdate:
        points = np.asarray(points_map, dtype=np.float64)
        if points.size == 0:
            points = np.zeros((0, 2), dtype=np.float64)
        points = points.reshape(-1, 2)
        role_arr = np.asarray(roles).reshape(-1)
        if len(role_arr) != len(points):
            raise ValueError("points_map and roles must contain the same number of points")
        if confidence is None:
            weights = np.ones(len(points), dtype=np.float64)
        else:
            weights = np.asarray(confidence, dtype=np.float64).reshape(-1)
            if len(weights) != len(points):
                raise ValueError("confidence must contain one value per point")

        touched = []
        accepted = 0
        unknown = 0
        for point, raw_role, weight in zip(points, role_arr, weights):
            x_m, z_m = float(point[0]), float(point[1])
            role = int(raw_role)
            if (not np.isfinite(x_m) or not np.isfinite(z_m)
                    or not np.isfinite(weight) or weight <= 0
                    or role not in _KNOWN_ROLES
                    or not self._in_window(x_m, z_m)):
                continue
            if math.hypot(x_m, z_m) > self.cfg.max_range_m:
                continue
            key = self.world_to_cell(x_m, z_m)
            cell = self._cells.setdefault(key, _CellEvidence())
            cell.add(role, float(weight), int(frame_id))
            touched.append(key)
            accepted += 1
            unknown += int(role == ROLE_UNKNOWN)

        self._age_cells(int(frame_id), touched)
        self._last_frame = max(self._last_frame, int(frame_id))
        return SurfaceUpdate(accepted, len(set(touched)), unknown)

    def update_from_frame(self, roles: np.ndarray, depth_m: np.ndarray, K: np.ndarray,
                          up: np.ndarray, camera_height_m: float, pose: SurfacePose,
                          frame_id: int, pixel_stride: int = 4) -> SurfaceUpdate:
        role_map = np.asarray(roles)
        depth = np.asarray(depth_m)
        if role_map.shape[:2] != depth.shape[:2]:
            raise ValueError("roles and depth_m must have matching image dimensions")
        if pixel_stride <= 0:
            raise ValueError("pixel_stride must be positive")
        x_lat, z_fwd, height, (vs, us) = depth_to_level_points(
            depth, np.asarray(K, dtype=np.float64), np.asarray(up, dtype=np.float64),
            float(camera_height_m), stride=int(pixel_stride))
        sampled_roles = role_map[vs, us]
        keep = np.isfinite(height) & (np.abs(height) <= self.cfg.ground_band_m)
        if not np.any(keep):
            return self.update_points(np.zeros((0, 2)), np.zeros((0,), dtype=np.uint8), frame_id)
        cos_y, sin_y = math.cos(float(pose.yaw_rad)), math.sin(float(pose.yaw_rad))
        map_x = float(pose.x_m) + x_lat[keep] * cos_y + z_fwd[keep] * sin_y
        map_z = float(pose.z_m) - x_lat[keep] * sin_y + z_fwd[keep] * cos_y
        return self.update_points(np.column_stack([map_x, map_z]), sampled_roles[keep], frame_id)

    def cell_role(self, ix: int, iz: int) -> int:
        cell = self._cells.get((int(ix), int(iz)))
        return ROLE_UNKNOWN if cell is None else cell.resolve()

    def cell_evidence(self, ix: int, iz: int) -> float:
        cell = self._cells.get((int(ix), int(iz)))
        return 0.0 if cell is None or not cell.scores else float(max(cell.scores.values()))

    def observed_cells(self) -> Dict[Tuple[int, int], int]:
        return {key: cell.resolve() for key, cell in self._cells.items() if cell.scores}

    def snapshot(self, pose: Optional[SurfacePose] = None, radius_m: Optional[float] = None) -> dict:
        """Return a JSON-friendly diagnostic snapshot without exposing mutable state."""
        cells = []
        limit = self.cfg.max_range_m if radius_m is None else float(radius_m)
        for (ix, iz), cell in sorted(self._cells.items()):
            x_m, z_m = self.cell_to_world(ix, iz)
            if pose is not None and math.hypot(x_m - pose.x_m, z_m - pose.z_m) > limit:
                continue
            cells.append({"ix": ix, "iz": iz, "role": cell.resolve(),
                          "evidence": self.cell_evidence(ix, iz),
                          "last_frame": cell.last_frame})
        return {"cell_m": self.cfg.cell_m, "cells": cells}
