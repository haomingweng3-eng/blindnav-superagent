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
from .safety_states import BLOCKED, CLEAR, OBSERVE, UNKNOWN


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
    max_gap_cells: int = 2
    scan_half_angle_deg: float = 90.0
    scan_step_deg: float = 1.0

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
        if self.max_gap_cells < 0 or self.scan_half_angle_deg <= 0 or self.scan_step_deg <= 0:
            raise ValueError("occupancy ray-search parameters are invalid")


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


@dataclass(frozen=True)
class OccupancyRoute:
    state: str
    angle_rad: float
    clear_distance_m: float
    blocking_cells: int
    unknown_cells: int
    confirmed: bool
    reason: str


# The evaluator uses the same shape for the immediate risk and the selected route.
OccupancyRisk = OccupancyRoute


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
        self._xy_index: Dict[Tuple[int, int], Set[OccupancyCellKey]] = {}
        self._visible_free: Dict[OccupancyCellKey, int] = {}
        self._visible_free_xy: Set[Tuple[int, int]] = set()
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
                    self._visible_free_xy.discard((key.ix, key.iz))

    @staticmethod
    def _as_key(value) -> OccupancyCellKey:
        if isinstance(value, OccupancyCellKey):
            return value
        raw = tuple(value)
        if len(raw) != 3:
            raise ValueError("visible occupancy cells must be (ix, iz, ih)")
        return OccupancyCellKey(int(raw[0]), int(raw[1]), int(raw[2]))

    def _remove_cell(self, key: OccupancyCellKey) -> None:
        self._cells.pop(key, None)
        xy = (key.ix, key.iz)
        keys = self._xy_index.get(xy)
        if keys is not None:
            keys.discard(key)
            if not keys:
                del self._xy_index[xy]

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
            self._xy_index.setdefault((key.ix, key.iz), set()).add(key)
            state.evidence += float(self.cfg.evidence_hit)
            state.last_frame = int(frame_id)
            if not state.hit_frames or state.hit_frames[-1] != int(frame_id):
                state.hit_frames.append(int(frame_id))
            self._visible_free.pop(key, None)
            self._visible_free_xy.discard((key.ix, key.iz))
            hit_keys.add(key)
            accepted += 1

        for raw_key in visible:
            if raw_key in hit_keys:
                continue
            state = self._cells.get(raw_key)
            if state is not None:
                state.evidence *= float(self.cfg.decay)
                if state.evidence < self.cfg.remove_below:
                    self._remove_cell(raw_key)
            self._visible_free[raw_key] = int(frame_id)
            self._visible_free_xy.add((raw_key.ix, raw_key.iz))

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

    def _occupied_at(self, ix: int, iz: int) -> List[Tuple[OccupancyCellKey, _CellState]]:
        out = []
        for key in self._xy_index.get((int(ix), int(iz)), ()):
            state = self._cells.get(key)
            if state is None or state.evidence < self.cfg.remove_below:
                continue
            _, _, height = self.cell_to_world(key)
            if 0.0 <= height <= self.cfg.person_height_m:
                out.append((key, state))
        return out

    def _horizontal_free(self, ix: int, iz: int) -> bool:
        return (int(ix), int(iz)) in self._visible_free_xy

    def _ray_cells(self, pose: SurfacePose, angle_rad: float):
        """Yield horizontal cell coordinates and boundary distances with a 2-D DDA."""
        c = float(self.cfg.cell_m)
        origin_x, origin_z = float(pose.x_m), float(pose.z_m)
        world_angle = float(pose.yaw_rad) + float(angle_rad)
        dx, dz = math.sin(world_angle), math.cos(world_angle)
        ix = int(math.floor(origin_x / c))
        iz = int(math.floor(origin_z / c))
        yield ix, iz, 0.0
        if abs(dx) < 1e-12:
            step_x, t_delta_x, t_max_x = 0, float("inf"), float("inf")
        elif dx > 0:
            step_x, t_delta_x = 1, c / dx
            t_max_x = ((ix + 1) * c - origin_x) / dx
        else:
            step_x, t_delta_x = -1, -c / dx
            t_max_x = (ix * c - origin_x) / dx
        if abs(dz) < 1e-12:
            step_z, t_delta_z, t_max_z = 0, float("inf"), float("inf")
        elif dz > 0:
            step_z, t_delta_z = 1, c / dz
            t_max_z = ((iz + 1) * c - origin_z) / dz
        else:
            step_z, t_delta_z = -1, -c / dz
            t_max_z = (iz * c - origin_z) / dz
        max_steps = int(math.ceil(self.cfg.max_range_m / c)) * 3 + 3
        for _ in range(max_steps):
            if t_max_x < t_max_z:
                distance = t_max_x
                t_max_x += t_delta_x
                ix += step_x
            else:
                distance = t_max_z
                t_max_z += t_delta_z
                iz += step_z
            if distance > self.cfg.max_range_m:
                break
            yield ix, iz, float(distance)

    def _body_intersection(self, pose: SurfacePose):
        cos_y, sin_y = math.cos(float(pose.yaw_rad)), math.sin(float(pose.yaw_rad))
        best = None
        for key, state in self._cells.items():
            if state.evidence < self.cfg.remove_below:
                continue
            x_m, z_m, height_m = self.cell_to_world(key)
            if not 0.0 <= height_m <= self.cfg.person_height_m:
                continue
            dx, dz = x_m - float(pose.x_m), z_m - float(pose.z_m)
            fwd = dx * sin_y + dz * cos_y
            lat = dx * cos_y - dz * sin_y
            if fwd < 0.0 or fwd > self.cfg.immediate_range_m:
                continue
            half = self.cfg.person_radius_m + self.cfg.cell_m
            if abs(lat) > half:
                continue
            candidate = (fwd, key, self.is_confirmed(key))
            if best is None or candidate[0] < best[0] or (candidate[2] and not best[2]):
                best = candidate
        return best

    def _ray_candidate(self, pose: SurfacePose, angle_rad: float) -> OccupancyRoute:
        clear = 0.0
        unknown = 0
        unknown_run = 0
        seen = set()
        for ix, iz, distance in self._ray_cells(pose, angle_rad):
            if (ix, iz) in seen:
                continue
            seen.add((ix, iz))
            occupied = self._occupied_at(ix, iz)
            confirmed = any(self.is_confirmed(key) for key, _ in occupied)
            if confirmed:
                return OccupancyRoute(BLOCKED, float(angle_rad), clear,
                                      len(occupied), unknown, True, "confirmed_ray_occupancy")
            if occupied:
                return OccupancyRoute(OBSERVE, float(angle_rad), clear,
                                      len(occupied), unknown, False, "unconfirmed_ray_occupancy")
            if self._horizontal_free(ix, iz):
                clear = max(clear, min(distance + self.cfg.cell_m, self.cfg.max_range_m))
                unknown_run = 0
                continue
            unknown += 1
            unknown_run += 1
            if unknown_run > self.cfg.max_gap_cells:
                break
        if clear <= 0.0:
            return OccupancyRoute(UNKNOWN, float(angle_rad), 0.0, 0, unknown, False,
                                  "unobserved_ray")
        state = CLEAR if unknown == 0 and clear >= self.cfg.padded_range_m else UNKNOWN
        return OccupancyRoute(state, float(angle_rad), float(clear), 0, unknown,
                              state == CLEAR, "visible_free_ray" if state == CLEAR else "unknown_gap")

    def raycast_clearance(self, pose: SurfacePose, target_angle_rad: float = 0.0) -> OccupancyRoute:
        """Return immediate body risk or the longest observed clearance ray."""
        hit = self._body_intersection(pose)
        if hit is not None:
            fwd, _key, confirmed = hit
            return OccupancyRoute(
                BLOCKED if confirmed else OBSERVE, float(target_angle_rad), float(fwd),
                1, 0, bool(confirmed),
                "confirmed_body_occupancy" if confirmed else "unconfirmed_body_occupancy",
            )

        half = math.radians(float(self.cfg.scan_half_angle_deg))
        step = math.radians(float(self.cfg.scan_step_deg))
        n = int(round((2.0 * half) / step))
        rays = [self._ray_candidate(pose, float(target_angle_rad - half + i * step))
                for i in range(n + 1)]
        clear = [r for r in rays if r.state == CLEAR and r.clear_distance_m >= self.cfg.padded_range_m]
        if clear:
            clear.sort(key=lambda r: (-r.clear_distance_m,
                                     abs((r.angle_rad - target_angle_rad + math.pi) % (2 * math.pi) - math.pi),
                                     r.angle_rad))
            return clear[0]
        unknown = [r for r in rays if r.clear_distance_m > 0.0]
        if unknown:
            unknown.sort(key=lambda r: (-r.clear_distance_m, r.unknown_cells,
                                        abs((r.angle_rad - target_angle_rad + math.pi) % (2 * math.pi) - math.pi)))
            selected = unknown[0]
            return OccupancyRoute(UNKNOWN, selected.angle_rad, selected.clear_distance_m,
                                  selected.blocking_cells, selected.unknown_cells, False,
                                  "no_fully_observed_clearance")
        return OccupancyRoute(UNKNOWN, float(target_angle_rad), 0.0, 0, 0, False,
                              "no_observed_clearance")

    def risk(self, pose: SurfacePose, target_angle_rad: float = 0.0) -> OccupancyRisk:
        return self.raycast_clearance(pose, target_angle_rad)

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
