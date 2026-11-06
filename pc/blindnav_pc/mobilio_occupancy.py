"""Persistent height-layered occupancy evidence for the PC experiment.

The input is already gravity-aligned body geometry in the convention used by
``blindnav_pc.bev``: ``(lateral, forward, height)``.  A recorded map pose turns
those points into a session-local world grid.  Keeping this conversion here
makes the occupancy logic independent of AR Foundation, LiDAR, SANPO, and model
code while retaining the part of Mobilio's ``DepthImage.cs`` that matters for
the experiment: evidence, visibility, height filtering, and temporal state.
"""
from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Dict, Iterable, List, Optional, Set, Tuple

import numpy as np

from .mobilio_surface import SurfacePose


@dataclass(frozen=True)
class OccupancyGridConfig:
    cell_m: float = 0.10
    height_cell_m: float = 0.10
    max_range_m: float = 4.0
    immediate_range_m: float = 1.5
    padded_range_m: float = 2.0
    person_radius_m: float = 0.20
    person_height_m: float = 1.70
    evidence_hit: float = 1.0
    confirm_score: float = 2.0
    confirm_frames: int = 2
    window_frames: int = 3
    decay: float = 0.90
    remove_below: float = 0.10

    def __post_init__(self) -> None:
        if self.cell_m <= 0 or self.height_cell_m <= 0 or self.max_range_m <= 0:
            raise ValueError("occupancy grid dimensions must be positive")
        if self.immediate_range_m <= 0 or self.padded_range_m <= 0:
            raise ValueError("occupancy horizons must be positive")
        if self.person_radius_m <= 0 or self.person_height_m <= 0:
            raise ValueError("person envelope must be positive")
        if self.confirm_frames <= 0 or self.window_frames <= 0:
            raise ValueError("confirmation windows must be positive")
        if self.confirm_frames > self.window_frames:
            raise ValueError("confirm_frames cannot exceed window_frames")
        if self.evidence_hit <= 0 or self.confirm_score <= 0:
            raise ValueError("occupancy evidence thresholds must be positive")
        if not 0.0 < self.decay <= 1.0 or self.remove_below < 0:
            raise ValueError("decay/remove_below values are invalid")


@dataclass(frozen=True, order=True)
class OccupancyCellKey:
    ix: int
    iz: int
    ih: int


@dataclass(frozen=True)
class OccupancyUpdate:
    n_input: int
    n_accepted: int
    n_visible_free: int


@dataclass
class _CellState:
    evidence: float = 0.0
    last_frame: int = -1
    hit_frames: Deque[int] = field(default_factory=deque)


class OccupancyGrid3D:
    """Map-frame occupancy evidence with explicit unknown/free/occupied state."""

    def __init__(self, cfg: Optional[OccupancyGridConfig] = None):
        self.cfg = cfg or OccupancyGridConfig()
        self.reset()

    def reset(self) -> None:
        self._cells: Dict[OccupancyCellKey, _CellState] = {}
        self._visible_free: Dict[OccupancyCellKey, int] = {}
        self._frame_history: Deque[int] = deque(maxlen=self.cfg.window_frames)
        self._last_frame: Optional[int] = None

    def world_to_cell(self, x_m: float, z_m: float, height_m: float) -> OccupancyCellKey:
        return OccupancyCellKey(
            int(math.floor(float(x_m) / self.cfg.cell_m)),
            int(math.floor(float(z_m) / self.cfg.cell_m)),
            int(math.floor(float(height_m) / self.cfg.height_cell_m)),
        )

    def cell_to_world(self, key: OccupancyCellKey) -> Tuple[float, float, float]:
        return (
            (key.ix + 0.5) * self.cfg.cell_m,
            (key.iz + 0.5) * self.cfg.cell_m,
            (key.ih + 0.5) * self.cfg.height_cell_m,
        )

    @staticmethod
    def body_to_map(points_body: np.ndarray, pose: SurfacePose) -> np.ndarray:
        """Transform columns ``(lateral, forward, height)`` to ``(x, z, height)``."""
        points = np.asarray(points_body, dtype=np.float64).reshape(-1, 3)
        lat, fwd, height = points.T if len(points) else (
            np.empty(0), np.empty(0), np.empty(0))
        cos_y, sin_y = math.cos(float(pose.yaw_rad)), math.sin(float(pose.yaw_rad))
        map_x = float(pose.x_m) + fwd * sin_y + lat * cos_y
        map_z = float(pose.z_m) + fwd * cos_y - lat * sin_y
        return np.column_stack([map_x, map_z, height]) if len(points) else np.zeros((0, 3))

    def _begin_frame(self, frame_id: int) -> None:
        frame = int(frame_id)
        if self._last_frame != frame:
            self._frame_history.append(frame)
            self._last_frame = frame
            active = set(self._frame_history)
            for key, state in tuple(self._cells.items()):
                state.hit_frames = deque(
                    (f for f in state.hit_frames if f in active),
                    maxlen=self.cfg.window_frames,
                )
            for key, seen_frame in tuple(self._visible_free.items()):
                if seen_frame not in active:
                    del self._visible_free[key]

    @staticmethod
    def _as_key(value) -> OccupancyCellKey:
        if isinstance(value, OccupancyCellKey):
            return value
        raw = tuple(value)
        if len(raw) != 3:
            raise ValueError("visible occupancy cells must be (ix, iz, ih)")
        return OccupancyCellKey(int(raw[0]), int(raw[1]), int(raw[2]))

    def update_body_points(self, points_body: np.ndarray, pose: SurfacePose,
                           frame_id: int, visible_free_cells: Iterable = ()) -> OccupancyUpdate:
        points = np.asarray(points_body, dtype=np.float64)
        if points.size == 0:
            points = np.zeros((0, 3), dtype=np.float64)
        points = points.reshape(-1, 3)
        self._begin_frame(int(frame_id))
        visible = {self._as_key(value) for value in visible_free_cells}
        hit_keys: Set[OccupancyCellKey] = set()
        accepted = 0
        for lateral, forward, height in points:
            if (not np.isfinite(lateral) or not np.isfinite(forward)
                    or not np.isfinite(height)):
                continue
            if height < 0.0 or height > self.cfg.person_height_m:
                continue
            if forward < -self.cfg.person_radius_m:
                continue
            if math.hypot(float(lateral), float(forward)) > self.cfg.max_range_m:
                continue
            world = self.body_to_map(np.array([[lateral, forward, height]]), pose)[0]
            key = self.world_to_cell(float(world[0]), float(world[1]), float(world[2]))
            state = self._cells.setdefault(key, _CellState(
                hit_frames=deque(maxlen=self.cfg.window_frames)))
            state.evidence += float(self.cfg.evidence_hit)
            state.last_frame = int(frame_id)
            if not state.hit_frames or state.hit_frames[-1] != int(frame_id):
                state.hit_frames.append(int(frame_id))
            self._visible_free.pop(key, None)
            hit_keys.add(key)
            accepted += 1

        for raw_key in visible:
            if raw_key in hit_keys:
                continue
            state = self._cells.get(raw_key)
            if state is not None:
                state.evidence *= float(self.cfg.decay)
                if state.evidence < self.cfg.remove_below:
                    del self._cells[raw_key]
            self._visible_free[raw_key] = int(frame_id)

        return OccupancyUpdate(len(points), accepted, len(visible - hit_keys))

    def keys(self) -> List[OccupancyCellKey]:
        return sorted(self._cells)

    def evidence(self, key: OccupancyCellKey) -> float:
        state = self._cells.get(self._as_key(key))
        return 0.0 if state is None else float(state.evidence)

    def is_observed_free(self, key: OccupancyCellKey) -> bool:
        return self._as_key(key) in self._visible_free

    def is_confirmed(self, key: OccupancyCellKey) -> bool:
        state = self._cells.get(self._as_key(key))
        if state is None or state.evidence < self.cfg.confirm_score:
            return False
        active = set(self._frame_history)
        return len({frame for frame in state.hit_frames if frame in active}) >= self.cfg.confirm_frames

    def snapshot(self, pose: Optional[SurfacePose] = None, radius_m: Optional[float] = None) -> dict:
        """Return JSON-friendly diagnostics for visualization."""
        limit = self.cfg.max_range_m if radius_m is None else float(radius_m)
        cells = []
        for key in self.keys():
            x_m, z_m, height_m = self.cell_to_world(key)
            if pose is not None and math.hypot(x_m - pose.x_m, z_m - pose.z_m) > limit:
                continue
            cells.append({"ix": key.ix, "iz": key.iz, "ih": key.ih,
                          "x_m": x_m, "z_m": z_m, "height_m": height_m,
                          "evidence": self.evidence(key),
                          "confirmed": self.is_confirmed(key)})
        return {"cell_m": self.cfg.cell_m, "height_cell_m": self.cfg.height_cell_m,
                "cells": cells}
