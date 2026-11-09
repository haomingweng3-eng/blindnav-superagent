"""Obstacle Guardian: owns objects that actually occupy the human path.

Issue #18 responsibility split, second half.  The Obstacle Guardian answers *is a 3D object
occupying the body's swept path?*  It consumes :class:`blindnav_pc.corridor_evidence.ObstaclePoints`
-- metric-depth 3D points above the local ground (``obstacle_min_height_m`` ..
``obstacle_max_height_m``) that the segmentation does not call walkable -- and emits one of:

``CLEAR``     no physical obstacle occupies the body swept path.
``BLOCKED``   a confirmed 3D occupancy mass intersects the body swept path (distance, height,
              physical extent and temporal continuity all pass).
``OBSERVE``   a detected 3D occupancy mass intersects the body path but is not yet temporally
              confirmed (hold and observe; never a pass).
``UNKNOWN``   the geometry/calibration/depth evidence cannot support a decision.

Two modes exist for the Issue #18 A/B/C ablation:

``coarse``
    the legacy PR #17 support rule (>= ``points_per_bin`` obstacle points in a 1.0 m forward bin
    of the body swept rectangle) -> immediate BLOCKED.  This is the responsibility-split arm (B)
    and deliberately keeps the old *measurement* of "there is something there" so the split's
    effect is isolated.
``spatial``
    the proposed collision criterion (arm C): physical 3D occupancy cells, connected components,
    swept-body intersection, actual signed clearance, depth/height/distance gating and temporal
    continuity.  It does **not** use a fixed "at least N points" rule (Issue #18 hard rule); the
    support test is physical extent (a mass at least ``min_component_width_m`` across in the
    ground plane and ``min_component_height_m`` tall), which is a body/sensor-scale prior, not a
    point count.  All constants are a priori body or layer constants, never tuned on the eval set.

Nothing here changes the Android STOP Safety Policy, MotionAuthority or ArrivalAuthority.
"""
from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Dict, List, Optional, Tuple

import numpy as np

from .corridor_evidence import ObstaclePoints
from .safety_states import BLOCKED, CLEAR, OBSERVE, UNKNOWN

COARSE = "coarse"
SPATIAL = "spatial"
MODES = (COARSE, SPATIAL)


@dataclass(frozen=True)
class ObstacleGuardianConfig:
    """A priori body / layer constants (provenance noted per field; none tuned on the eval set)."""

    near_m: float = 1.5              # ShadowWalkConfig.near_m / PlannerConfig forward range
    far_m: float = 6.0               # ShadowWalkConfig.far_m
    body_width_m: float = 0.5        # PlannerConfig.body_width_m (human body width)
    body_margin_m: float = 0.15      # PlannerConfig.margin_m (lateral safety margin)
    obstacle_min_height_m: float = 0.15   # LayerConfig.ground_max_m / BEV obstacle band
    obstacle_max_height_m: float = 2.0    # LayerConfig.head_max_m
    cell_m: float = 0.10             # occupancy cell size (10 cm, body-scale)
    lateral_search_m: float = 0.20   # measure clearance for masses just outside the body sweep
    min_component_width_m: float = 0.08   # physical extent in the ground plane (sensor resolution)
    min_component_height_m: float = 0.15  # vertical extent (knee-height step)
    min_depth_m: float = 0.3         # GroundConfig.min_ground_depth_m
    max_depth_m: float = 12.0        # GroundConfig.max_ground_depth_m
    min_depth_confidence: float = 0.45    # GroundConfig.min_confidence (existing a priori floor)
    confirm_frames: int = 2          # temporal evidence: present in >=2 of the last 3 frames
    window_frames: int = 3           # (provenance: LayerConfig.drop_confirm_frames pattern)
    match_distance_m: float = 0.60   # component centroid match radius across frames
    legacy_points_per_bin: int = 5   # coarse mode only: frozen PR #17 support rule (ablation)
    legacy_bin_m: float = 1.0        # coarse mode only: frozen PR #17 bin width

    @property
    def swept_half_width_m(self) -> float:
        return self.body_width_m / 2.0 + self.body_margin_m

    @property
    def lateral_search_half_m(self) -> float:
        return self.swept_half_width_m + self.lateral_search_m


@dataclass(frozen=True)
class OccupiedComponent:
    """One connected physical 3D occupancy mass in body coordinates."""

    fwd_min_m: float
    fwd_max_m: float
    lat_min_m: float
    lat_max_m: float
    height_min_m: float
    height_max_m: float
    depth_median_m: float
    n_points: int
    n_cells: int

    @property
    def forward_extent_m(self) -> float:
        return self.fwd_max_m - self.fwd_min_m

    @property
    def lateral_extent_m(self) -> float:
        return self.lat_max_m - self.lat_min_m

    @property
    def height_extent_m(self) -> float:
        return self.height_max_m - self.height_min_m

    @property
    def footprint_extent_m(self) -> float:
        """Largest ground-plane extent (metres) of the mass."""
        return max(self.forward_extent_m, self.lateral_extent_m)

    @property
    def centroid(self) -> Tuple[float, float]:
        return ((self.fwd_min_m + self.fwd_max_m) / 2.0,
                (self.lat_min_m + self.lat_max_m) / 2.0)

    def signed_clearance_m(self, half_width_m: float) -> float:
        """Signed lateral clearance between the body swept band and this mass.

        Positive = a gap of that many metres on the nearer side; 0 = touching; negative = the
        mass intrudes that far into the body swept band ("actual clearance" of Issue #18).
        """
        lo, hi = self.lat_min_m, self.lat_max_m
        if lo > half_width_m:
            return lo - half_width_m
        if hi < -half_width_m:
            return -half_width_m - hi
        overlap = min(hi, half_width_m) - max(lo, -half_width_m)
        return -float(max(0.0, overlap))

    def intersects_swept(self, cfg: ObstacleGuardianConfig) -> bool:
        half = cfg.swept_half_width_m
        if not (self.fwd_max_m >= cfg.near_m and self.fwd_min_m <= cfg.far_m):
            return False
        return (self.lat_max_m >= -half) and (self.lat_min_m <= half)


@dataclass(frozen=True)
class ObstacleRisk:
    """Obstacle Guardian output (state is one of CLEAR / BLOCKED / OBSERVE / UNKNOWN)."""

    state: str
    reason: str
    n_components: int = 0
    n_in_path: int = 0
    n_confirmed: int = 0
    nearest_m: Optional[float] = None
    clearance_m: Optional[float] = None
    lateral_extent_m: Optional[float] = None
    height_extent_m: Optional[float] = None
    depth_confidence: Optional[float] = None
    temporal_streak: int = 0
    n_obstacle_points: int = 0
    n_candidate_points: int = 0

    def is_pass(self) -> bool:
        return self.state == CLEAR


@dataclass
class _HistoryFrame:
    components: List[OccupiedComponent] = field(default_factory=list)


def _union_find_components(cells: Dict[Tuple[int, int], List[int]],
                           points_fwd: np.ndarray, points_lat: np.ndarray,
                           points_height: np.ndarray, points_depth: np.ndarray,
                           max_depth_m: float) -> List[OccupiedComponent]:
    """8-connected components over occupied cells; returns physical mass summaries."""
    keys = list(cells.keys())
    index = {key: i for i, key in enumerate(keys)}
    parent = list(range(len(keys)))

    def find(a: int) -> int:
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for i, (fi, li) in enumerate(keys):
        for dfi in (-1, 0, 1):
            for dli in (-1, 0, 1):
                if dfi == 0 and dli == 0:
                    continue
                j = index.get((fi + dfi, li + dli))
                if j is not None:
                    union(i, j)

    groups: Dict[int, List[int]] = {}
    for i in range(len(keys)):
        groups.setdefault(find(i), []).append(i)

    out: List[OccupiedComponent] = []
    for idxs in groups.values():
        pt_idx: List[int] = []
        for i in idxs:
            pt_idx.extend(cells[keys[i]])
        pt = np.asarray(pt_idx, dtype=np.int64)
        depths = points_depth[pt]
        finite_depth = depths[np.isfinite(depths) & (depths > 0) & (depths <= max_depth_m)]
        out.append(OccupiedComponent(
            fwd_min_m=float(points_fwd[pt].min()), fwd_max_m=float(points_fwd[pt].max()),
            lat_min_m=float(points_lat[pt].min()), lat_max_m=float(points_lat[pt].max()),
            height_min_m=float(points_height[pt].min()),
            height_max_m=float(points_height[pt].max()),
            depth_median_m=float(np.median(finite_depth)) if len(finite_depth) else float("nan"),
            n_points=int(len(pt)), n_cells=int(len(idxs))))
    return out


class ObstacleGuardian:
    """Per-frame 3D occupancy risk for the body swept path (stateful for temporal continuity)."""

    def __init__(self, mode: str = SPATIAL,
                 cfg: Optional[ObstacleGuardianConfig] = None):
        if mode not in MODES:
            raise ValueError(f"unknown Obstacle Guardian mode {mode!r}; choose from {MODES}")
        self.mode = mode
        self.cfg = cfg or ObstacleGuardianConfig()
        self._history: Deque[_HistoryFrame] = deque(maxlen=max(1, self.cfg.window_frames))

    # -- helpers ------------------------------------------------------------------
    def _append_history(self, components: List[OccupiedComponent]) -> None:
        self._history.append(_HistoryFrame(components=list(components)))

    def _streak_for(self, comp: OccupiedComponent) -> int:
        """How many of the last ``window_frames - 1`` frames had a matching component (+1 now)."""
        if self.cfg.confirm_frames <= 1:
            return 1
        cf, cl = comp.centroid
        matched = 0
        previous = list(self._history)[-max(0, self.cfg.window_frames - 1):]
        for frame in reversed(previous):
            hit = False
            for old in frame.components:
                of, ol = old.centroid
                if math.hypot(cf - of, cl - ol) <= self.cfg.match_distance_m:
                    hit = True
                    break
            if hit:
                matched += 1
            else:
                # Continuity is consecutive: a missed frame breaks the confirmation streak.
                break
        return 1 + matched

    @staticmethod
    def _physical_extent_ok(comp: OccupiedComponent,
                            cfg: ObstacleGuardianConfig) -> bool:
        return (comp.footprint_extent_m >= cfg.min_component_width_m
                and comp.height_extent_m >= cfg.min_component_height_m)

    def _depth_quality_ok(self, comp: OccupiedComponent,
                          cfg: ObstacleGuardianConfig) -> bool:
        d = comp.depth_median_m
        return bool(math.isfinite(d) and cfg.min_depth_m <= d <= cfg.max_depth_m)

    # -- entry point --------------------------------------------------------------
    def update(self, obstacle: ObstaclePoints, *, depth_confidence: Optional[float] = None,
               calib_ok: bool = True) -> ObstacleRisk:
        """One frame of the Obstacle Guardian."""
        cfg = self.cfg
        n_total = len(obstacle)
        if not calib_ok:
            self._append_history([])
            return ObstacleRisk(UNKNOWN, "geometry_unavailable", depth_confidence=depth_confidence,
                                n_obstacle_points=n_total)
        # Depth-quality gating belongs to the spatial fusion (arm C): arm B is the minimal
        # responsibility split and keeps the legacy support rule without an added gate, so the
        # B-vs-C ablation isolates spatial fusion and A-vs-B isolates the responsibility split.
        if (self.mode == SPATIAL and depth_confidence is not None
                and not (depth_confidence >= cfg.min_depth_confidence)):
            self._append_history([])
            return ObstacleRisk(UNKNOWN, "depth_quality_insufficient",
                                depth_confidence=float(depth_confidence),
                                n_obstacle_points=n_total)

        fwd = np.asarray(obstacle.fwd_m, dtype=np.float64)
        lat = np.asarray(obstacle.lat_m, dtype=np.float64)
        height = np.asarray(obstacle.height_m, dtype=np.float64)
        depth = np.asarray(obstacle.depth_m, dtype=np.float64)
        finite = np.isfinite(fwd) & np.isfinite(lat) & np.isfinite(height)
        near_far = (fwd >= cfg.near_m) & (fwd <= cfg.far_m)
        lateral = np.abs(lat) <= cfg.lateral_search_half_m
        candidate = (finite & near_far & lateral
                     & (height > cfg.obstacle_min_height_m)
                     & (height <= cfg.obstacle_max_height_m))
        n_candidate = int(np.count_nonzero(candidate))
        if not n_candidate:
            self._append_history([])
            return ObstacleRisk(CLEAR, "no_occupancy", depth_confidence=depth_confidence,
                                n_obstacle_points=n_total, n_candidate_points=0)

        if self.mode == COARSE:
            body = candidate & (np.abs(lat) <= cfg.swept_half_width_m)
            fwd_body = fwd[body]
            nbins = max(1, int(math.ceil((cfg.far_m - cfg.near_m) / cfg.legacy_bin_m - 1e-9)))
            counts = np.zeros(nbins, dtype=np.int64)
            if len(fwd_body):
                idx = np.clip(np.floor((fwd_body - cfg.near_m) / cfg.legacy_bin_m).astype(int),
                              0, nbins - 1)
                counts = np.bincount(idx, minlength=nbins).astype(np.int64)[:nbins]
            supported = bool(np.any(counts >= cfg.legacy_points_per_bin))
            self._append_history([])  # coarse mode has no temporal confirmation
            if supported:
                nearest = float(fwd_body.min())
                half = cfg.swept_half_width_m
                lat_body = lat[body]
                lo, hi = float(lat_body.min()), float(lat_body.max())
                if lo > half or hi < -half:
                    clearance = max(lo - half, -half - hi)
                else:
                    clearance = -float(max(0.0, min(hi, half) - max(lo, -half)))
                return ObstacleRisk(BLOCKED, "obstacle_in_path", n_components=1,
                                    n_in_path=1, nearest_m=nearest, clearance_m=clearance,
                                    lateral_extent_m=hi - lo, depth_confidence=depth_confidence,
                                    n_obstacle_points=n_total, n_candidate_points=n_candidate)
            return ObstacleRisk(CLEAR, "no_supported_obstacle",
                                depth_confidence=depth_confidence, n_obstacle_points=n_total,
                                n_candidate_points=n_candidate)

        # ---- spatial mode: physical occupancy components --------------------------
        cf, cl = cfg.cell_m, cfg.cell_m
        cells: Dict[Tuple[int, int], List[int]] = {}
        for i in np.nonzero(candidate)[0]:
            key = (int(math.floor(fwd[i] / cf)), int(math.floor(lat[i] / cl)))
            cells.setdefault(key, []).append(int(i))
        components = _union_find_components(cells, fwd, lat, height, depth, cfg.max_depth_m)

        in_path: List[Tuple[OccupiedComponent, int]] = []
        for comp in components:
            if not (self._physical_extent_ok(comp, cfg) and self._depth_quality_ok(comp, cfg)):
                continue
            if comp.intersects_swept(cfg):
                in_path.append((comp, self._streak_for(comp)))

        # Keep all candidate components for next-frame continuity matching (not only in-path
        # ones: an obstacle may move into the body sweep a frame later).
        self._append_history(components)

        if not in_path:
            near_components = [c for c in components
                               if self._physical_extent_ok(c, cfg)
                               and c.fwd_min_m >= cfg.near_m]
            if near_components:
                best = min(near_components,
                           key=lambda c: abs(c.signed_clearance_m(cfg.swept_half_width_m)))
                return ObstacleRisk(
                    CLEAR, "obstacle_outside_path", n_components=len(components),
                    nearest_m=best.fwd_min_m,
                    clearance_m=best.signed_clearance_m(cfg.swept_half_width_m),
                    lateral_extent_m=best.lateral_extent_m,
                    height_extent_m=best.height_extent_m,
                    depth_confidence=depth_confidence, n_obstacle_points=n_total,
                    n_candidate_points=n_candidate)
            return ObstacleRisk(CLEAR, "no_physical_obstacle", n_components=len(components),
                                depth_confidence=depth_confidence,
                                n_obstacle_points=n_total, n_candidate_points=n_candidate)

        confirmed = [(c, s) for c, s in in_path if s >= cfg.confirm_frames]
        best_comp, best_streak = min(
            in_path, key=lambda t: abs(t[0].signed_clearance_m(cfg.swept_half_width_m)))
        if confirmed:
            comp = min((c for c, _ in confirmed),
                       key=lambda c: abs(c.signed_clearance_m(cfg.swept_half_width_m)))
            streak = max(s for _, s in confirmed)
            return ObstacleRisk(
                BLOCKED, "obstacle_confirmed_in_path", n_components=len(components),
                n_in_path=len(in_path), n_confirmed=len(confirmed),
                nearest_m=comp.fwd_min_m,
                clearance_m=comp.signed_clearance_m(cfg.swept_half_width_m),
                lateral_extent_m=comp.lateral_extent_m, height_extent_m=comp.height_extent_m,
                depth_confidence=depth_confidence, temporal_streak=int(streak),
                n_obstacle_points=n_total, n_candidate_points=n_candidate)
        return ObstacleRisk(
            OBSERVE, "obstacle_unconfirmed_in_path", n_components=len(components),
            n_in_path=len(in_path), n_confirmed=0,
            nearest_m=best_comp.fwd_min_m,
            clearance_m=best_comp.signed_clearance_m(cfg.swept_half_width_m),
            lateral_extent_m=best_comp.lateral_extent_m,
            height_extent_m=best_comp.height_extent_m, depth_confidence=depth_confidence,
            temporal_streak=int(best_streak), n_obstacle_points=n_total,
            n_candidate_points=n_candidate)
