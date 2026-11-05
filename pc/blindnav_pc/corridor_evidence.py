"""Explicit corridor evidence channels: walkable / hazard / non-walkable / unknown.

Map frame (same as shadowwalk): x = right, z = forward, metres; a point is an (x, z) row.
The caller decides which pixels are walkable/hazard/non-walkable/unknown from its own mask
(this module does not know SANPO or v5 class ids).

Channel semantics:
  walkable      ground (|height| <= max_height_m) with positive walkable support
  hazard        curb/drop boundary ground points, and blocking obstacle points
                (0.15 m < height <= 2.0 m) whose mask does not call them walkable
  non_walkable  ground the mask explicitly excludes (road, terrain, grass, wall bases, ...)
  unknown       ground the mask leaves unlabelled (ignore) and geometric conflicts:
                pixels the mask calls walkable whose geometry is not ground

This module is deliberately free of ``shadowwalk`` imports (no circular import) and of
torch / model dependencies.  It only needs the existing level-frame BEV back-projection.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, Tuple

import numpy as np

from .bev import depth_to_level_points

ROLE_IGNORE, ROLE_WALKABLE, ROLE_HAZARD, ROLE_NON_WALKABLE = 0, 1, 2, 3


def _empty_points() -> np.ndarray:
    return np.zeros((0, 2), dtype=np.float64)


def _empty_vector() -> np.ndarray:
    return np.zeros((0,), dtype=np.float64)


def _as_map_points(points) -> np.ndarray:
    """Normalise any point container to a float64 (N, 2) map-frame array; empty -> (0, 2)."""
    if points is None:
        return np.zeros((0, 2), dtype=np.float64)
    arr = np.asarray(points, dtype=np.float64)
    if arr.size == 0:
        return np.zeros((0, 2), dtype=np.float64)
    return arr.reshape(-1, 2)


@dataclass(frozen=True)
class CorridorEvidence:
    """Four explicit evidence channels in the corridor map frame, each an (N, 2) array."""

    walkable: np.ndarray = field(default_factory=lambda: np.zeros((0, 2), dtype=np.float64))
    hazard: np.ndarray = field(default_factory=lambda: np.zeros((0, 2), dtype=np.float64))
    non_walkable: np.ndarray = field(default_factory=lambda: np.zeros((0, 2), dtype=np.float64))
    unknown: np.ndarray = field(default_factory=lambda: np.zeros((0, 2), dtype=np.float64))

    def __post_init__(self) -> None:
        # frozen dataclass: normalise through object.__setattr__ (shape/dtype only, never mutated)
        for name in ("walkable", "hazard", "non_walkable", "unknown"):
            object.__setattr__(self, name, _as_map_points(getattr(self, name)))

    @classmethod
    def walkable_only(cls, points) -> "CorridorEvidence":
        """The legacy walkable-only input: hazard/non-walkable/unknown channels are empty."""
        return cls(walkable=_as_map_points(points))

    def is_empty(self) -> bool:
        return (len(self.walkable) == 0 and len(self.hazard) == 0
                and len(self.non_walkable) == 0 and len(self.unknown) == 0)

    def counts(self) -> Dict[str, int]:
        return {"walkable": int(len(self.walkable)),
                "hazard": int(len(self.hazard)),
                "non_walkable": int(len(self.non_walkable)),
                "unknown": int(len(self.unknown))}


@dataclass(frozen=True)
class ObstaclePoints:
    """3D obstacle-height points in the BODY frame (Issue #18 Obstacle Guardian input).

    A point is an obstacle-height 3D pixel: ``max_height_m < height <= obstacle_max_m`` above the
    local ground plane and the segmentation does not call it walkable.  The four-channel
    ``CorridorEvidence.hazard`` channel is exactly the union of these points with the curb/boundary
    ground points, so the split adds no new negative evidence -- it only separates the two
    responsibilities and gives the Obstacle Guardian the 3D attributes it needs (distance, height,
    depth) for its geometric gating.

    ``fwd_m``/``lat_m`` are in the current body heading (same convention as
    :func:`body_frame`); ``depth_m`` is the metric z-depth of the pixel; ``role`` is the
    ``ROLE_*`` role the mask assigns at that pixel; ``map_points`` is (N, 2) map-frame (x, z).
    """

    fwd_m: np.ndarray = field(default_factory=_empty_vector)
    lat_m: np.ndarray = field(default_factory=_empty_vector)
    height_m: np.ndarray = field(default_factory=_empty_vector)
    depth_m: np.ndarray = field(default_factory=_empty_vector)
    role: np.ndarray = field(default_factory=lambda: np.zeros((0,), dtype=np.uint8))
    map_points: np.ndarray = field(default_factory=_empty_points)

    def __post_init__(self) -> None:
        for name in ("fwd_m", "lat_m", "height_m", "depth_m"):
            object.__setattr__(self, name, np.asarray(getattr(self, name),
                                                      dtype=np.float64).reshape(-1))
        object.__setattr__(self, "role", np.asarray(self.role, dtype=np.uint8).reshape(-1))
        object.__setattr__(self, "map_points", _as_map_points(self.map_points))
        n = len(self.fwd_m)
        if any(len(getattr(self, name)) != n
               for name in ("lat_m", "height_m", "depth_m", "role")):
            raise ValueError("ObstaclePoints arrays must have the same length")
        if len(self.map_points) not in (0, n):
            raise ValueError("ObstaclePoints.map_points must be empty or match the point count")

    def __len__(self) -> int:
        return int(len(self.fwd_m))

    def is_empty(self) -> bool:
        return len(self) == 0

    @classmethod
    def empty(cls) -> "ObstaclePoints":
        return cls()



def body_frame(points_map, pose) -> Tuple[np.ndarray, np.ndarray]:
    """(forward, lateral) of map points along the CURRENT BODY HEADING (pose yaw),
    walker at (pose.x_m, pose.z_m); lateral positive = right of heading.
    forward = dx*sin(yaw)+dz*cos(yaw); lateral = dx*cos(yaw)-dz*sin(yaw)"""
    p = _as_map_points(points_map)
    dx = p[:, 0] - float(pose.x_m)
    dz = p[:, 1] - float(pose.z_m)
    cos_y, sin_y = math.cos(pose.yaw_rad), math.sin(pose.yaw_rad)
    fwd = dx * sin_y + dz * cos_y
    lat = dx * cos_y - dz * sin_y
    return fwd, lat


def in_swept_body(fwd, lat, near_m: float, far_m: float, half_width_m: float) -> np.ndarray:
    """Boolean gate for points inside the body swept rectangle (forward/lateral bounds inclusive)."""
    fwd = np.asarray(fwd, dtype=np.float64).reshape(-1)
    lat = np.asarray(lat, dtype=np.float64).reshape(-1)
    return ((fwd >= near_m) & (fwd <= far_m) & (np.abs(lat) <= half_width_m))


@dataclass(frozen=True)
class SplitCorridorEvidence:
    """The four-channel evidence plus the responsibility split used by Issue #18.

    ``evidence`` keeps the PR #17 frozen four-channel contract unchanged.  The split adds:

    ``curb_ground``
        map-frame ground points whose role is ``ROLE_HAZARD`` (curb / path boundary).  These
        belong to the Path Keeper, not to the Obstacle Guardian.
    ``obstacle``
        :class:`ObstaclePoints` -- 3D obstacle-height points in body coordinates (the
        Obstacle Guardian input).  ``evidence.hazard`` is exactly
        ``curb_ground`` + ``obstacle`` (same points, same order groups), so the split changes
        responsibility assignment, never the amount of negative evidence.
    """

    evidence: CorridorEvidence
    curb_ground: np.ndarray = field(default_factory=_empty_points)
    obstacle: ObstaclePoints = field(default_factory=ObstaclePoints.empty)

    def __post_init__(self) -> None:
        object.__setattr__(self, "curb_ground", _as_map_points(self.curb_ground))

    @property
    def walkable(self) -> np.ndarray:
        return self.evidence.walkable

    @property
    def non_walkable(self) -> np.ndarray:
        return self.evidence.non_walkable

    @property
    def hazard(self) -> np.ndarray:
        return self.evidence.hazard

    @property
    def unknown(self) -> np.ndarray:
        return self.evidence.unknown


def build_split_corridor_evidence(role_map, depth_m, K, up, camera_height_m, pose,
                                  *, max_height_m: float = 0.15,
                                  obstacle_min_m: float = 0.15,
                                  obstacle_max_m: float = 2.0,
                                  pixel_stride: int = 4) -> SplitCorridorEvidence:
    """Back-project a per-pixel ROLE map into the four channels AND the split decomposition.

    The ``evidence`` member is row-for-row identical to :func:`build_corridor_evidence` with the
    same arguments.  The split only partitions the existing ``hazard`` channel into:

    * ``curb_ground``: ground points with ``ROLE_HAZARD`` (Path Keeper: curb / boundary);
    * ``obstacle``: obstacle-height points with ``role != ROLE_WALKABLE`` (Obstacle Guardian:
      3D occupancy candidates), carrying body-forward/lateral/height/depth/role attributes.

    A 3D point cloud that is not walkable and not ground is therefore *evidence of occupancy*
    rather than a surface-class claim: the Obstacle Guardian decides from its physical extent,
    distance, height and temporal continuity (Issue #18 hard rules).
    """
    role_map = np.asarray(role_map)
    depth = np.asarray(depth_m)
    if role_map.shape[:2] != depth.shape[:2]:
        raise ValueError(f"role_map {role_map.shape[:2]} and depth {depth.shape[:2]} must match")

    x_lat, z_fwd, height, (vs, us) = depth_to_level_points(
        depth, K, up, camera_height_m, stride=pixel_stride)
    role = role_map[vs, us]
    depth_at = depth[vs, us]

    ground = np.abs(height) <= max_height_m
    obstacle = (height > max_height_m) & (height <= obstacle_max_m)
    below = height < -max_height_m

    walkable_mask = ground & (role == ROLE_WALKABLE)
    curb_mask = ground & (role == ROLE_HAZARD)
    obstacle_mask = obstacle & (role != ROLE_WALKABLE)
    # Same union as build_corridor_evidence's hazard channel (disjoint by construction).
    hazard_mask = curb_mask | obstacle_mask
    non_walkable_mask = ground & (role == ROLE_NON_WALKABLE)
    # Frozen spec §1: ignore pixels are UNKNOWN as ground; obstacle+ignore is intentionally in
    # BOTH the hazard channel (mask does not call it walkable) and the unknown channel.
    unknown_mask = ((ground & (role == ROLE_IGNORE))
                    | (obstacle & (role == ROLE_IGNORE))
                    | ((role == ROLE_WALKABLE) & (obstacle | below)))

    finite = np.isfinite(x_lat) & np.isfinite(z_fwd)
    cos_y, sin_y = math.cos(pose.yaw_rad), math.sin(pose.yaw_rad)

    def to_map(mask: np.ndarray) -> np.ndarray:
        keep = mask & finite
        if not np.any(keep):
            return np.zeros((0, 2), dtype=np.float64)
        xl, zf = x_lat[keep], z_fwd[keep]
        mx = pose.x_m + xl * cos_y + zf * sin_y
        mz = pose.z_m - xl * sin_y + zf * cos_y
        return np.column_stack([mx, mz]).astype(np.float64, copy=False)

    evidence = CorridorEvidence(walkable=to_map(walkable_mask),
                                hazard=to_map(hazard_mask),
                                non_walkable=to_map(non_walkable_mask),
                                unknown=to_map(unknown_mask))

    obstacle_map = to_map(obstacle_mask)
    if len(obstacle_map):
        keep = obstacle_mask & finite
        fwd, lat = body_frame(obstacle_map, pose)
        obstacle_points = ObstaclePoints(
            fwd_m=fwd, lat_m=lat,
            height_m=height[keep], depth_m=depth_at[keep], role=role[keep],
            map_points=obstacle_map)
    else:
        obstacle_points = ObstaclePoints.empty()

    return SplitCorridorEvidence(evidence=evidence,
                                 curb_ground=to_map(curb_mask),
                                 obstacle=obstacle_points)


def build_corridor_evidence(role_map, depth_m, K, up, camera_height_m, pose,
                            *, max_height_m: float = 0.15,
                            obstacle_min_m: float = 0.15,
                            obstacle_max_m: float = 2.0,
                            pixel_stride: int = 4) -> CorridorEvidence:
    """Back-project a per-pixel ROLE map (uint8) with the existing BEV helper.

    ``obstacle_min_m`` is accepted for parity with the existing a priori constants
    (``BEVConfig.obstacle_min_m`` / ``PlannerConfig``); with the frozen defaults it equals
    ``max_height_m`` and the obstacle band is exactly ``max_height_m < height <= obstacle_max_m``.

    The walkable channel is row-for-row identical to
    ``shadowwalk.mask_to_map_points((role_map == ROLE_WALKABLE).astype(uint8), depth_m, K, up,
    camera_height_m, pose, cfg_with_same_stride_and_max_height)`` so the new decision layer keeps
    the baseline walkable input unchanged.

    This is the frozen PR #17 contract; it is now implemented on top of
    :func:`build_split_corridor_evidence` so the two views can never drift.
    """
    return build_split_corridor_evidence(
        role_map, depth_m, K, up, camera_height_m, pose,
        max_height_m=max_height_m, obstacle_min_m=obstacle_min_m,
        obstacle_max_m=obstacle_max_m, pixel_stride=pixel_stride).evidence

