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
    blind_zone_m: float = 2.2
    max_gap_cells: int = 2
    min_candidate_m: float = 5.0
    scan_half_angle_deg: float = 90.0
    scan_step_deg: float = 1.0
    evidence_decay: float = 0.90
    stale_after_frames: int = 15

    def __post_init__(self) -> None:
        if self.cell_m <= 0 or self.half_extent_m <= 0 or self.max_range_m <= 0:
            raise ValueError("surface grid dimensions must be positive")
        if not 0.0 < self.evidence_decay <= 1.0:
            raise ValueError("evidence_decay must be in (0, 1]")
        if self.ground_band_m < 0 or self.stale_after_frames < 0:
            raise ValueError("ground band and stale frame count must be non-negative")
        if self.blind_zone_m < 0 or self.blind_zone_m > self.max_range_m:
            raise ValueError("blind_zone_m must lie within max_range_m")
        if self.max_gap_cells < 0 or self.min_candidate_m < 0:
            raise ValueError("gap and candidate distance must be non-negative")
        if self.scan_half_angle_deg <= 0 or self.scan_step_deg <= 0:
            raise ValueError("scan angles must be positive")


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
    termination_distance_m: float = 0.0
    last_walkable_distance_m: float = 0.0
    termination_cell: Optional[Tuple[int, int]] = None
    last_walkable_cell: Optional[Tuple[int, int]] = None


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

    def _in_window(self, x_m: float, z_m: float,
                   center: Tuple[float, float] = (0.0, 0.0)) -> bool:
        limit = self.cfg.half_extent_m
        return (abs(float(x_m) - float(center[0])) <= limit
                and abs(float(z_m) - float(center[1])) <= limit)

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
                      frame_id: int, confidence: Optional[np.ndarray] = None,
                      center: Optional[Tuple[float, float]] = None) -> SurfaceUpdate:
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

        center_x, center_z = center if center is not None else (0.0, 0.0)
        touched = []
        accepted = 0
        unknown = 0
        for point, raw_role, weight in zip(points, role_arr, weights):
            x_m, z_m = float(point[0]), float(point[1])
            role = int(raw_role)
            if (not np.isfinite(x_m) or not np.isfinite(z_m)
                    or not np.isfinite(weight) or weight <= 0
                    or role not in _KNOWN_ROLES
                    or not self._in_window(x_m, z_m, (center_x, center_z))):
                continue
            if math.hypot(x_m - float(center_x), z_m - float(center_z)) > self.cfg.max_range_m:
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
        return self.update_points(
            np.column_stack([map_x, map_z]), sampled_roles[keep], frame_id,
            center=(float(pose.x_m), float(pose.z_m)))

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
            cells.append({"ix": ix, "iz": iz, "x_m": x_m, "z_m": z_m,
                          "role": cell.resolve(),
                          "evidence": self.cell_evidence(ix, iz),
                          "last_frame": cell.last_frame})
        return {"cell_m": self.cfg.cell_m, "cells": cells}

    def _ray_cells(self, pose: SurfacePose, angle_rad: float):
        """Yield ``(ix, iz, distance)`` using a 2-D grid DDA."""
        c = float(self.cfg.cell_m)
        origin_x, origin_z = float(pose.x_m), float(pose.z_m)
        world_angle = float(pose.yaw_rad) + float(angle_rad)
        dx, dz = math.sin(world_angle), math.cos(world_angle)
        ix, iz = self.world_to_cell(origin_x, origin_z)
        yield ix, iz, 0.0

        if abs(dx) < 1e-12:
            step_x, t_delta_x, t_max_x = 0, float("inf"), float("inf")
        elif dx > 0:
            step_x = 1
            t_delta_x = c / dx
            t_max_x = ((ix + 1) * c - origin_x) / dx
        else:
            step_x = -1
            t_delta_x = -c / dx
            t_max_x = (ix * c - origin_x) / dx

        if abs(dz) < 1e-12:
            step_z, t_delta_z, t_max_z = 0, float("inf"), float("inf")
        elif dz > 0:
            step_z = 1
            t_delta_z = c / dz
            t_max_z = ((iz + 1) * c - origin_z) / dz
        else:
            step_z = -1
            t_delta_z = -c / dz
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

    @staticmethod
    def _angle_diff(a: float, b: float) -> float:
        return (float(a) - float(b) + math.pi) % (2.0 * math.pi) - math.pi

    def _ray_candidate(self, pose: SurfacePose, angle_rad: float) -> SurfaceRoute:
        observed = 0.0
        last_walkable_distance = 0.0
        last_walkable_cell = None
        termination_distance = 0.0
        termination_cell = None
        unknown_total = 0
        pending_unknown = 0
        terminated = ROLE_UNKNOWN
        fully_observed = True
        seen = set()
        for ix, iz, distance in self._ray_cells(pose, angle_rad):
            key = (ix, iz)
            if key in seen:
                continue
            seen.add(key)
            role = self.cell_role(ix, iz)
            if role == ROLE_WALKABLE:
                if pending_unknown:
                    unknown_total += pending_unknown
                    fully_observed = False
                    pending_unknown = 0
                observed = max(observed, min(distance + self.cfg.cell_m, self.cfg.max_range_m))
                last_walkable_distance = float(distance)
                last_walkable_cell = key
                continue
            if role == ROLE_UNKNOWN:
                if distance <= self.cfg.blind_zone_m:
                    continue
                pending_unknown += 1
                if pending_unknown > self.cfg.max_gap_cells:
                    terminated = ROLE_UNKNOWN
                    termination_distance = float(distance)
                    termination_cell = key
                    break
                continue
            if pending_unknown:
                unknown_total += pending_unknown
                fully_observed = False
                pending_unknown = 0
            terminated = role
            termination_distance = float(distance)
            termination_cell = key
            break
        if termination_cell is None and observed > 0.0:
            termination_distance = float(min(observed, self.cfg.max_range_m))
        route_clear = (fully_observed and observed >= self.cfg.min_candidate_m
                       and terminated == ROLE_UNKNOWN)
        return SurfaceRoute(
            state="CLEAR" if route_clear else "OBSERVE",
            angle_rad=float(angle_rad), observed_distance_m=float(observed),
            unknown_cells=int(unknown_total), terminated_role=int(terminated),
            candidate_count=1, fully_observed=bool(fully_observed),
            reason=("walkable" if terminated == ROLE_UNKNOWN else
                    ("hazard" if terminated == ROLE_HAZARD else "non_walkable")),
            termination_distance_m=termination_distance,
            last_walkable_distance_m=last_walkable_distance,
            termination_cell=termination_cell,
            last_walkable_cell=last_walkable_cell,
        )

    def trace_rays(self, pose: SurfacePose, target_angle_rad: float = 0.0) -> list:
        """Collect read-only ray evidence; never feeds the navigation decision."""
        half = math.radians(self.cfg.scan_half_angle_deg)
        step = math.radians(self.cfg.scan_step_deg)
        n = int(round(2 * half / step))
        records = []
        for ray_id in range(n + 1):
            angle = float(target_angle_rad - half + ray_id * step)
            route = self._ray_candidate(pose, angle)
            counts = {role: 0 for role in _KNOWN_ROLES}
            pending = 0
            continuous_start = continuous_end = None
            longest = 0.0
            termination_reason = "MAX_RANGE"
            terminal_cell = None
            termination_distance = self.cfg.max_range_m
            last_walkable = None
            last_walkable_distance = None
            seen = set()
            observed_cell_count = 0
            zero_length_cells = 0
            positive_length_unknown_cells = 0
            termination_cell_zero_length = None
            ray_cells = list(self._ray_cells(pose, angle))
            for cell_index, (ix, iz, distance) in enumerate(ray_cells):
                if (ix, iz) in seen:
                    continue
                seen.add((ix, iz))
                observed_cell_count += int(self.cell_evidence(ix, iz) > 0)
                role = self.cell_role(ix, iz)
                exit_distance = (ray_cells[cell_index + 1][2]
                                 if cell_index + 1 < len(ray_cells)
                                 else self.cfg.max_range_m)
                zero_length = exit_distance - distance <= 1e-9
                zero_length_cells += int(zero_length)
                positive_length_unknown_cells += int(role == ROLE_UNKNOWN and not zero_length)
                counts[role] += 1
                if role == ROLE_WALKABLE:
                    if not zero_length:
                        if continuous_start is None:
                            continuous_start = distance
                        continuous_end = exit_distance
                        longest = max(longest, continuous_end - continuous_start)
                    last_walkable = [ix, iz]
                    last_walkable_distance = distance
                    pending = 0
                elif role == ROLE_UNKNOWN:
                    if not zero_length:
                        continuous_start = continuous_end = None
                    if distance <= self.cfg.blind_zone_m:
                        continue
                    pending += 1
                    if pending > self.cfg.max_gap_cells:
                        termination_reason = "UNKNOWN_GAP"
                        terminal_cell, termination_distance = [ix, iz], distance
                        termination_cell_zero_length = zero_length
                        break
                else:
                    termination_reason = "HAZARD" if role == ROLE_HAZARD else "NON_WALKABLE"
                    terminal_cell, termination_distance = [ix, iz], distance
                    termination_cell_zero_length = zero_length
                    break
            records.append({
                "ray_id": ray_id, "angle_rad": angle, "angle_frame": "body_relative_yaw_positive_right",
                "observed_distance_m": route.observed_distance_m,
                "fully_observed": route.fully_observed, "unknown_cells": route.unknown_cells,
                "continuous_walkable_length_m": longest,
                "termination_distance_m": termination_distance, "termination_cell": terminal_cell,
                "last_walkable_distance_m": last_walkable_distance, "last_walkable_cell": last_walkable,
                "termination_reason": termination_reason,
                "termination_cell_zero_length": termination_cell_zero_length,
                "zero_length_cells": zero_length_cells,
                "positive_length_unknown_cells": positive_length_unknown_cells,
                "zero_length_tolerance_m": 1e-9,
                "observation_source": None, "evidence_age": None,
                "observation_source_unavailable_reason": "per_cell_source_not_recorded",
                "evidence_age_unavailable_reason": "per_cell_age_not_exported",
                "terminal_role": role if terminal_cell is not None else None,
                "total_cells": len(seen), "walkable_cells": counts[ROLE_WALKABLE],
                "observed_cell_count": observed_cell_count,
                "unknown_cell_count": counts[ROLE_UNKNOWN], "hazard_cells": counts[ROLE_HAZARD],
                "non_walkable_cells": counts[ROLE_NON_WALKABLE], "pending_unknown_run": pending,
                "passes_length_filter": route.observed_distance_m >= self.cfg.min_candidate_m,
                "selected_by_original_algorithm": False, "best_rejected_diagnostic": False,
            })
        selected = self.raycast_route(pose, target_angle_rad)
        for item in records:
            item["selected_by_original_algorithm"] = (selected.angle_rad is not None and
                                                       item["angle_rad"] == selected.angle_rad)
        rejected = [r for r in records if not r["passes_length_filter"]
                    and r["continuous_walkable_length_m"] > 1e-9]
        if rejected:
            best = min(rejected, key=lambda r: (-r["continuous_walkable_length_m"],
                       abs(self._angle_diff(r["angle_rad"], target_angle_rad)), r["angle_rad"]))
            best["best_rejected_diagnostic"] = True
        return records

    def raycast_route(self, pose: SurfacePose, target_angle_rad: float = 0.0) -> SurfaceRoute:
        """Search target +/-90 degrees and choose the longest safe observed ray."""
        half = math.radians(float(self.cfg.scan_half_angle_deg))
        step = math.radians(float(self.cfg.scan_step_deg))
        n = int(round((2.0 * half) / step))
        angles = [float(target_angle_rad - half + i * step) for i in range(n + 1)]
        candidates = []
        for angle in angles:
            candidate = self._ray_candidate(pose, angle)
            if candidate.observed_distance_m >= self.cfg.min_candidate_m:
                candidates.append(candidate)
        if not candidates:
            return SurfaceRoute("UNKNOWN", None, 0.0, 0, ROLE_UNKNOWN, 0, False,
                                "no_candidate_ray")
        candidates.sort(key=lambda r: (not r.fully_observed, -r.observed_distance_m,
                                       abs(self._angle_diff(r.angle_rad, target_angle_rad)),
                                       r.angle_rad))
        selected = candidates[0]
        return SurfaceRoute(
            state=selected.state, angle_rad=selected.angle_rad,
            observed_distance_m=selected.observed_distance_m,
            unknown_cells=selected.unknown_cells, terminated_role=selected.terminated_role,
            candidate_count=len(candidates), fully_observed=selected.fully_observed,
            reason=selected.reason,
            termination_distance_m=getattr(selected, "termination_distance_m", 0.0),
            last_walkable_distance_m=getattr(selected, "last_walkable_distance_m", 0.0),
            termination_cell=getattr(selected, "termination_cell", None),
            last_walkable_cell=getattr(selected, "last_walkable_cell", None),
        )
