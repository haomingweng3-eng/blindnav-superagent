"""ShadowWalk-style virtual-corridor guidance stub.

A corridor is a predefined 2-D polyline plus width in a local map frame (x = right, z = forward,
metres).  A frame's walkable segmentation mask is back-projected with the existing level-frame
helpers in :mod:`bev` and transformed into that map frame using an *externally supplied* pose
(PDR / odometry).  The stub then reports how far the walker is from the corridor centreline and
which way to steer.

Deliberately minimal: no localisation, no planning, no control.  Without a trusted pose, enough
visible walkable ground, or with the walker outside the corridor, the output is UNKNOWN/STOP.
It never emits "go" and is only meant to run inside the supervised demo of
``docs/REAL_DEVICE_PROTOCOL.md``.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import Optional, Sequence, Tuple

import numpy as np

from .bev import depth_to_level_points
from .ego import wrap_pi

LEFT, RIGHT, STRAIGHT, STOP, UNKNOWN = "LEFT", "RIGHT", "STRAIGHT", "STOP", "UNKNOWN"

HINT_TEXT = {
    LEFT: "稍向左",
    RIGHT: "稍向右",
    STRAIGHT: "沿走廊直行",
    STOP: "请停下",
    UNKNOWN: "前方走廊无法确认，请停下并使用盲杖探查",
}


@dataclass(frozen=True)
class Corridor:
    """Predefined corridor: polyline in metres, ``width_m`` is the full walkable width."""

    name: str
    points: Tuple[Tuple[float, float], ...]
    width_m: float = 2.5

    def __post_init__(self) -> None:
        if len(self.points) < 2:
            raise ValueError("a corridor needs at least two points")
        if self.width_m <= 0:
            raise ValueError("corridor width must be positive")
        if self.length_m <= 0:
            raise ValueError("corridor length must be positive")

    @property
    def length_m(self) -> float:
        return sum(math.hypot(b[0] - a[0], b[1] - a[1])
                   for a, b in zip(self.points[:-1], self.points[1:]))

    def _segments(self):
        for a, b in zip(self.points[:-1], self.points[1:]):
            dx, dz = b[0] - a[0], b[1] - a[1]
            length = math.hypot(dx, dz)
            if length > 0:
                yield a[0], a[1], dx, dz, length

    def project_many(self, points: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
        """Return (arclength s, signed lateral offset) for each (x, z) point.

        Offset is positive to the *right* of the corridor direction (x-right/z-forward frame).
        Points beyond either end clamp to that end segment.
        """
        p = np.asarray(points, dtype=np.float64).reshape(-1, 2)
        best = np.full(len(p), np.inf)
        s_out = np.zeros(len(p))
        off_out = np.zeros(len(p))
        s_acc = 0.0
        for x0, z0, dx, dz, length in self._segments():
            t = ((p[:, 0] - x0) * dx + (p[:, 1] - z0) * dz) / (length * length)
            tc = np.clip(t, 0.0, 1.0)
            cx, cz = x0 + dx * tc, z0 + dz * tc
            dist2 = (p[:, 0] - cx) ** 2 + (p[:, 1] - cz) ** 2
            right = np.array([dz, -dx]) / length
            off = (p[:, 0] - cx) * right[0] + (p[:, 1] - cz) * right[1]
            better = dist2 < best
            best[better] = dist2[better]
            s_out[better] = s_acc + length * t[better]
            off_out[better] = off[better]
            s_acc += length
        return s_out, off_out

    def project(self, x: float, z: float) -> Tuple[float, float]:
        s, off = self.project_many(np.array([[x, z]]))
        return float(s[0]), float(off[0])

    def heading_at(self, s: float) -> float:
        """Corridor tangent heading (radians, 0 = +z, positive = turning right)."""
        s = min(max(s, 0.0), self.length_m)
        s_acc = 0.0
        for _, _, dx, dz, length in self._segments():
            if s <= s_acc + length:
                return math.atan2(dx, dz)
            s_acc += length
        a, b = self.points[-2], self.points[-1]
        return math.atan2(b[0] - a[0], b[1] - a[1])

    def point_at(self, s: float) -> Tuple[float, float, float, float]:
        """Return (x, z, right_x, right_z) at arclength ``s``."""
        s = min(max(s, 0.0), self.length_m)
        s_acc = 0.0
        for x0, z0, dx, dz, length in self._segments():
            if s <= s_acc + length or s_acc + length >= self.length_m:
                t = min(max((s - s_acc) / length, 0.0), 1.0)
                return x0 + dx * t, z0 + dz * t, dz / length, -dx / length
            s_acc += length
        x0, z0 = self.points[-1]
        return x0, z0, 0.0, -1.0

    def pose_at(self, s: float, offset_m: float = 0.0,
                yaw_error_rad: float = 0.0) -> "Pose2D":
        x, z, rx, rz = self.point_at(s)
        return Pose2D(x + rx * offset_m, z + rz * offset_m,
                      wrap_pi(self.heading_at(s) + yaw_error_rad))

    def to_dict(self) -> dict:
        return {"name": self.name, "width_m": self.width_m,
                "points": [[float(x), float(z)] for x, z in self.points]}

    @classmethod
    def from_dict(cls, data: dict) -> "Corridor":
        return cls(str(data.get("name", "corridor")),
                   tuple((float(p[0]), float(p[1])) for p in data["points"]),
                   float(data.get("width_m", 2.5)))


@dataclass(frozen=True)
class Pose2D:
    x_m: float
    z_m: float
    yaw_rad: float = 0.0        # 0 = +z; positive = heading towards +x (right)


@dataclass
class ShadowWalkConfig:
    near_m: float = 1.5              # only judge walkable ground this far ahead ...
    far_m: float = 6.0               # ... up to here
    bin_m: float = 1.0               # forward coverage bins
    points_per_bin: int = 5          # a bin counts as seen with at least this many points
    min_coverage: float = 0.5        # below this the corridor is not seen well enough
    min_points: int = 50
    band_margin_m: float = 0.3       # accept walkable points slightly outside the nominal width
    deadband_m: float = 0.25         # smaller lateral errors are not worth a spoken turn
    deadband_yaw_deg: float = 10.0
    stop_offset_frac: float = 0.8    # |offset| > frac * width/2 -> walker outside the corridor
    max_yaw_deg: float = 30.0
    pixel_stride: int = 4
    max_height_m: float = 0.15       # segmentation points must lie on the ground plane


@dataclass(frozen=True)
class ShadowWalkHint:
    hint: str
    text: str
    progress_m: float           # walker arclength along the corridor
    offset_m: float             # walker lateral offset from the centreline (+ right)
    walkable_offset_m: float    # median lateral offset of the visible walkable band
    yaw_error_rad: float        # walker heading minus corridor tangent (+ = pointing right)
    coverage: float
    n_points: int
    reason: str


def mask_to_map_points(mask: np.ndarray, depth: np.ndarray, K: np.ndarray, up: np.ndarray,
                       camera_height_m: float, pose: Pose2D,
                       cfg: Optional[ShadowWalkConfig] = None) -> np.ndarray:
    """Back-project walkable-mask ground pixels into the corridor map frame.  Returns Nx2 (x, z)."""
    c = cfg or ShadowWalkConfig()
    mask = np.asarray(mask)
    depth = np.asarray(depth)
    if mask.shape[:2] != depth.shape[:2]:
        raise ValueError(f"mask {mask.shape[:2]} and depth {depth.shape[:2]} must match")
    x_lat, z_fwd, height, (vs, us) = depth_to_level_points(
        depth, K, up, camera_height_m, stride=c.pixel_stride)
    ok = np.abs(height) <= c.max_height_m
    ok &= mask[vs, us] > 0
    ok &= np.isfinite(x_lat) & np.isfinite(z_fwd)
    x_lat, z_fwd = x_lat[ok], z_fwd[ok]
    cos_y, sin_y = math.cos(pose.yaw_rad), math.sin(pose.yaw_rad)
    mx = pose.x_m + x_lat * cos_y + z_fwd * sin_y
    mz = pose.z_m - x_lat * sin_y + z_fwd * cos_y
    return np.column_stack([mx, mz]) if len(mx) else np.zeros((0, 2))


class ShadowWalkTracker:
    """Frame-by-frame lateral/yaw guidance against one predefined corridor."""

    def __init__(self, corridor: Corridor, cfg: Optional[ShadowWalkConfig] = None):
        self.corridor = corridor
        self.cfg = cfg or ShadowWalkConfig()

    def _result(self, hint, pose_s, offset, band, yaw_err, coverage, n, reason) -> ShadowWalkHint:
        return ShadowWalkHint(hint, HINT_TEXT[hint], pose_s, offset, band, yaw_err,
                              coverage, int(n), reason)

    def update(self, points_map: Optional[np.ndarray], pose: Optional[Pose2D]) -> ShadowWalkHint:
        c, corr = self.cfg, self.corridor
        if pose is None or not all(math.isfinite(v) for v in (pose.x_m, pose.z_m, pose.yaw_rad)):
            return self._result(UNKNOWN, 0.0, float("nan"), float("nan"), float("nan"),
                                0.0, 0, "no_pose")
        s0, walker_off = corr.project(pose.x_m, pose.z_m)
        yaw_err = float(wrap_pi(pose.yaw_rad - corr.heading_at(s0)))
        half = corr.width_m / 2.0

        pts = np.zeros((0, 2)) if points_map is None else np.asarray(points_map, float).reshape(-1, 2)
        band, coverage = 0.0, 0.0
        n = 0
        if len(pts):
            s, off = corr.project_many(pts)
            fwd = s - s0
            keep = ((fwd >= c.near_m) & (fwd <= c.far_m) &
                    (np.abs(off) <= half + c.band_margin_m))
            off = off[keep]
            fwd = fwd[keep]
            n = int(len(off))
            nbins = max(1, int(round((c.far_m - c.near_m) / c.bin_m)))
            if n:
                idx = np.floor((fwd - c.near_m) / c.bin_m).astype(int)
                idx = idx[(idx >= 0) & (idx < nbins)]
                counts = np.bincount(idx, minlength=nbins)
                coverage = float(np.mean(counts >= c.points_per_bin))
                band = float(np.median(off))

        if abs(walker_off) > c.stop_offset_frac * half:
            return self._result(STOP, s0, walker_off, band, yaw_err, coverage, n, "off_corridor")
        if n < c.min_points:
            return self._result(UNKNOWN, s0, walker_off, band, yaw_err, coverage, n, "walkable_unseen")
        if coverage < c.min_coverage:
            return self._result(UNKNOWN, s0, walker_off, band, yaw_err, coverage, n, "corridor_unseen")
        if abs(math.degrees(yaw_err)) > c.max_yaw_deg:
            return self._result(STOP, s0, walker_off, band, yaw_err, coverage, n, "yaw_too_large")

        target = float(np.clip(band, -(half - 0.4), half - 0.4))
        error = walker_off - target            # + = walker is right of the visible path -> go left
        if abs(error) > c.deadband_m:
            return self._result(LEFT if error > 0 else RIGHT, s0, walker_off, band, yaw_err,
                                coverage, n, "lateral")
        if abs(math.degrees(yaw_err)) > c.deadband_yaw_deg:
            return self._result(LEFT if yaw_err > 0 else RIGHT, s0, walker_off, band, yaw_err,
                                coverage, n, "yaw")
        return self._result(STRAIGHT, s0, walker_off, band, yaw_err, coverage, n, "centred")


def _arc(name: str, length_m: float, turn_deg: float, width_m: float, step_m: float = 10.0) -> Corridor:
    """Corridor that bends by ``turn_deg`` (+ right, - left) over ``length_m``."""
    angle = math.radians(abs(turn_deg))
    sign = 1.0 if turn_deg >= 0 else -1.0
    radius = length_m / angle
    pts = []
    s = 0.0
    while s < length_m:
        phi = s / radius
        pts.append((sign * radius * (1.0 - math.cos(phi)), radius * math.sin(phi)))
        s += step_m
    phi = length_m / radius
    pts.append((sign * radius * (1.0 - math.cos(phi)), radius * math.sin(phi)))
    return Corridor(name, tuple(pts), width_m)


PRESETS = {
    "straight60": Corridor("straight60", ((0.0, 0.0), (0.0, 30.0), (0.0, 60.0)), 2.5),
    "left_arc80": _arc("left_arc80", 80.0, -30.0, 2.5),
    "right_arc100": _arc("right_arc100", 100.0, 20.0, 2.5),
}


def get_corridor(name: str) -> Corridor:
    if name in PRESETS:
        return PRESETS[name]
    raise KeyError(f"unknown corridor {name!r}; choose from {sorted(PRESETS)}")


def load_corridor(path: str) -> Corridor:
    with open(path, "r", encoding="utf-8") as fh:
        return Corridor.from_dict(json.load(fh))
