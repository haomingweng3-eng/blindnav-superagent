"""Isolated port of Harvard Mobilio ``DepthImage.cs`` obstacle search.

MIT source: Harvard-Slade-Lab/Mobilio, scripts/DepthImage.cs. This reference
keeps the upstream count/decay/search semantics and does not use BlindNav's
UNKNOWN policy or temporal confirmation rules.
"""
from __future__ import annotations
import math
from collections import defaultdict
from dataclasses import dataclass
from typing import Dict, Iterable, Tuple


@dataclass(frozen=True)
class HarvardOccupancyConfig:
    node_size: float = 0.1
    search_radius: float = 4.0
    distance_to_obstacle: float = 1.5
    padded_distance: float = 2.0
    person_radius: float = 0.2
    person_height: float = 1.7
    # Reference grid stores world height; the adapter converts camera-relative
    # height to a ground-at-zero world coordinate.
    ground: float = 0.0
    points_to_block_cell: float = 2.0
    cells_to_block_person: int = 2


class HarvardOccupancyReference:
    def __init__(self, config: HarvardOccupancyConfig | None = None):
        self.cfg = config or HarvardOccupancyConfig()
        self.grid: Dict[Tuple[int, int, int], float] = defaultdict(float)

    def add_to_grid(self, points_world: Iterable[Tuple[float, float, float]]) -> None:
        c = self.cfg.node_size
        for x, y, z in points_world:
            key = (round(x / c), round(y / c), round(z / c))
            self.grid[key] += 1.0

    def decay_visible(self, visible_xy: Iterable[Tuple[int, int]]) -> None:
        visible = set(visible_xy)
        for key in list(self.grid):
            if (key[0], key[2]) in visible:
                self.grid[key] *= 0.9
                if self.grid[key] < 1e-6:
                    del self.grid[key]

    def _blocking(self, position=(0.0, 0.0), heading_deg: float = 0.0):
        sin_h, cos_h = math.sin(math.radians(heading_deg)), math.cos(math.radians(heading_deg))
        points = []
        for (ix, iy, iz), score in self.grid.items():
            if score <= self.cfg.points_to_block_cell:
                continue
            x = ix * self.cfg.node_size - float(position[0])
            y = iy * self.cfg.node_size
            z = iz * self.cfg.node_size - float(position[1])
            if not (self.cfg.ground < y < self.cfg.ground + self.cfg.person_height):
                continue
            rx, rz = cos_h * x - sin_h * z, sin_h * x + cos_h * z
            points.append((rx, rz))
        return points

    def attempt_line(self, blocked, target_x: int, target_y: int) -> Tuple[bool, float]:
        half = int(self.cfg.search_radius / self.cfg.node_size)
        x = y = half
        dx, dy = target_x - x + 1e-6, target_y - y + 1e-6
        sx, sy = (1 if dx > 0 else -1), (1 if dy > 0 else -1)
        dist = math.sqrt(dx * dx + dy * dy)
        tx, ty = abs(dist / dx), abs(dist / dy)
        mx, my = tx, ty
        while x != target_x or y != target_y:
            if mx < my: mx += tx; x += sx
            else: my += ty; y += sy
            if (x, y) in blocked:
                return True, float((x - half) ** 2 + (y - half) ** 2)
        return False, float((x - half) ** 2 + (y - half) ** 2)

    def best_line_direction(self, position=(0.0, 0.0), heading_deg: float = 0.0) -> float:
        blocked = set()
        for rx, rz in self._blocking(position, heading_deg):
            i = int(rx / self.cfg.node_size) + int(self.cfg.search_radius / self.cfg.node_size)
            j = int(rz / self.cfg.node_size) + int(self.cfg.search_radius / self.cfg.node_size)
            blocked.add((i, j))
        degree = 0.0; best_dir = 10.0; best_dist = -1.0
        while abs(degree) <= 90:
            x = int(self.cfg.padded_distance * math.cos(math.radians(90 - degree)) / self.cfg.node_size)
            y = int(self.cfg.padded_distance * math.sin(math.radians(90 - degree)) / self.cfg.node_size)
            hit, dist = self.attempt_line(blocked, x + int(self.cfg.search_radius / self.cfg.node_size),
                                          y + int(self.cfg.search_radius / self.cfg.node_size))
            if not hit: return degree
            if dist > best_dist: best_dir, best_dist = degree, dist
            degree = -degree if degree > 0 else -degree + 2
        if -30 < best_dir < 0: best_dir = -30
        if 0 < best_dir < 30: best_dir = 30
        return best_dir

    def check_for_obstacle(self, position=(0.0, 0.0), heading_deg: float = 0.0) -> Tuple[float, float]:
        points = self._blocking(position, heading_deg)
        inside = [(x, z) for x, z in points if 0 < z < self.cfg.distance_to_obstacle
                  and -self.cfg.person_radius < x < self.cfg.person_radius]
        closest = min((math.hypot(x, z) for x, z in inside), default=999.0)
        direction = self.best_line_direction(position, heading_deg) if len(inside) >= self.cfg.cells_to_block_person else 0.0
        return direction, closest
