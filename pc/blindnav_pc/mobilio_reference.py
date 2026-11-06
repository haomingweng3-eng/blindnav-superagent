"""Faithful, input-agnostic port of Harvard Mobilio's Vision.cs ray search.

Source: Harvard-Slade-Lab/Mobilio, scripts/Vision.cs, MIT licensed.
This module intentionally has no BlindNav safety states, evidence decay, or
depth/AR dependencies. Callers provide a world grid of boolean walkability.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple


@dataclass(frozen=True)
class HarvardSurfaceConfig:
    node_size: float = 0.2
    fill_radius: float = 15.0
    min_path_length: float = 5.0
    min_branch_length: float = 3.5
    grid_skips: int = 2
    max_disparity_deg: float = 30.0


class HarvardSurfaceReference:
    """Port of RaycastGridWalkable/RaycastGrid from Vision.cs."""

    def __init__(self, walkable: Optional[Dict[Tuple[int, int], bool]] = None,
                 config: Optional[HarvardSurfaceConfig] = None):
        self.cfg = config or HarvardSurfaceConfig()
        self.grid: Dict[Tuple[int, int], bool] = dict(walkable or {})

    def _cell(self, x: float, z: float) -> Tuple[int, int]:
        return (int(round(x / self.cfg.node_size)), int(round(z / self.cfg.node_size)))

    @staticmethod
    def angle_diff(a: float, b: float) -> float:
        return 180.0 - abs(abs(a - b) - 180.0)

    def raycast_grid_walkable(self, x0: float, z0: float, target_angle_deg: float) -> float:
        """Exact DDA shape used by Vision.cs, including its two-cell skip rule."""
        x, y = self._cell(x0, z0)
        valid_x, valid_y = x, y
        origin = (self.cfg.node_size * x, self.cfg.node_size * y)
        skips = 0
        if not self.grid.get(origin_to_key(origin, self.cfg), False):
            skips += 1
        target_x = x + int(round(self.cfg.fill_radius * math.sin(math.radians(target_angle_deg)) / self.cfg.node_size))
        target_y = y + int(round(self.cfg.fill_radius * math.cos(math.radians(target_angle_deg)) / self.cfg.node_size))
        dx, dy = target_x - x + 1e-6, target_y - y + 1e-6
        step_x, step_y = (1 if dx > 0 else -1), (1 if dy > 0 else -1)
        dist = math.sqrt(dx * dx + dy * dy)
        delta_x, delta_y = abs(dist / dx), abs(dist / dy)
        max_x, max_y = delta_x, delta_y
        while x != target_x or y != target_y:
            if max_x < max_y:
                max_x += delta_x; x += step_x
            else:
                max_y += delta_y; y += step_y
            key = (x, y)
            if not self.grid.get(key, False):
                skips += 1
                if skips > self.cfg.grid_skips:
                    break
            else:
                valid_x, valid_y, skips = x, y, 0
        return math.hypot(self.cfg.node_size * valid_x - origin[0],
                          self.cfg.node_size * valid_y - origin[1])

    def raycast_grid(self, target_angle_deg: float, turn_angle_deg: float = 0.0,
                     origin: Tuple[float, float] = (0.0, 0.0)) -> Tuple[Optional[float], List[float]]:
        angle_dist = []
        for angle in range(int(target_angle_deg - 90), int(target_angle_deg + 90) + 1):
            dist = self.raycast_grid_walkable(origin[0], origin[1], float(angle))
            if dist > self.cfg.min_path_length:
                angle_dist.append(((angle + 360.0) % 360.0, dist))
        if not angle_dist:
            return None, []
        groups: List[Tuple[float, float]] = []
        index = 0
        values = angle_dist + [(9999.0, 0.0)]
        for i in range(len(values) - 1):
            if values[i][1] > values[index][1]: index = i
            if self.angle_diff(values[i + 1][0], values[i][0]) > 1.0:
                groups.append(values[index]); index = i + 1
        if not groups and angle_dist:
            groups.append(angle_dist[index])
        best = min(groups, key=lambda p: (self.angle_diff(p[0], target_angle_deg), p[0]))
        if self.angle_diff(best[0], target_angle_deg) > self.cfg.max_disparity_deg:
            return None, []
        if (self.angle_diff(best[0], turn_angle_deg) <= 45 or
                self.angle_diff((best[0] + 180) % 360, turn_angle_deg) <= 45):
            return best[0], []
        best_index = groups.index(best)
        branch_dists, branch_lengths, totals = [], [], []
        d = 2.0
        while d < best[1]:
            sx = origin[0] + d * math.sin(math.radians(best[0]))
            sz = origin[1] + d * math.cos(math.radians(best[0]))
            length = self.raycast_grid_walkable(sx, sz, turn_angle_deg)
            opposite = self.raycast_grid_walkable(sx, sz, turn_angle_deg + 180.0)
            branch_dists.append(d); branch_lengths.append(length); totals.append(length + opposite)
            d += self.cfg.node_size / 2.0
        if len(branch_lengths) <= 1:
            return best[0], []
        median_total = median(totals)
        median_branch = median(branch_lengths)
        good = [d for d, length, total in zip(branch_dists, branch_lengths, totals)
                if total > median_total + self.cfg.min_branch_length
                and length > median_branch + self.cfg.min_branch_length]
        branches = []
        if good:
            start = total = 0.0; count = 0
            for value in good + [float("inf")]:
                if count and value - start > self.cfg.node_size:
                    branches.append(total / count); total = 0.0; count = 0
                if value != float("inf"):
                    if count == 0: start = value
                    total += value; count += 1
        return best[0], branches


def origin_to_key(origin: Tuple[float, float], cfg: HarvardSurfaceConfig) -> Tuple[int, int]:
    return (int(round(origin[0] / cfg.node_size)), int(round(origin[1] / cfg.node_size)))


def median(values):
    values = sorted(values)
    n = len(values)
    return (values[n // 2] if n % 2 else (values[n // 2 - 1] + values[n // 2]) / 2.0)
