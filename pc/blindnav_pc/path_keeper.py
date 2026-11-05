"""Path Keeper: owns the walking surface (pavement, road, curb, boundary, drift).

Issue #18 splits the corridor decision layer into two independent responsibilities.  The Path
Keeper answers *is the walking surface still there, and where does it go?*:

* pavement / walkable ground support and the lateral guidance band (STRAIGHT / LEFT / RIGHT);
* explicit non-walkable ground the mask excludes (road, terrain, grass, inaccessible surface);
* curb / path-boundary ground (``ROLE_HAZARD`` ground points);
* unlabelled ground (unknown surface evidence);
* walker drift (off-corridor position, excessive yaw).

Hard rule (Issue #18): **NON_WALKABLE alone must never equal STOP.**  A road/curb/boundary
surface risk demotes the state to ``OBSERVE`` (hold, do not pass).  The only STOP-like state,
``BLOCKED``, belongs to the :mod:`blindnav_pc.obstacle_guardian`; the
:mod:`blindnav_pc.safety_coordinator` fuses both and cannot upgrade a path-surface risk into a
confirmed collision.

This module does not import the legacy :class:`blindnav_pc.shadowwalk.ShadowWalkTracker`; it
reuses the same frozen geometry constants (:class:`blindnav_pc.shadowwalk.ShadowWalkConfig`) and
the same swept-body definitions from :mod:`blindnav_pc.corridor_evidence`, so arm A (PR #17)
stays byte-identical and this module can be unit-tested on its own.

Nothing here changes the Android STOP Safety Policy, MotionAuthority or ArrivalAuthority.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, Optional

import numpy as np

from .corridor_evidence import body_frame, in_swept_body
from .safety_states import CLEAR, OBSERVE, UNKNOWN
from .shadowwalk import LEFT, RIGHT, STRAIGHT, Corridor, Pose2D, ShadowWalkConfig


def swept_evidence_bin_count(cfg: ShadowWalkConfig) -> int:
    """Inclusive ``near_m..far_m`` evidence bin count (frozen PR #17 definition).

    ``ceil`` so the inclusive far end (5.5-6.0 m tail of a 1.5-6.0 m body) is not dropped.
    """
    return max(1, int(math.ceil((cfg.far_m - cfg.near_m) / cfg.bin_m - 1e-9)))


def swept_bin_counts(points_map, pose: Pose2D, cfg: ShadowWalkConfig,
                     nbins: int) -> np.ndarray:
    """Per-forward-bin support of one evidence channel inside the body swept rectangle.

    Same frozen definition as ``ShadowWalkTracker._swept_bin_counts``: bins measured along the
    current body heading, laterally gated to ``|lateral| <= swept_half_width_m``, inclusive far
    bin.  Kept as a local helper (and pinned against the PR #17 tracker by tests) so the Path
    Keeper does not depend on a private method.
    """
    counts = np.zeros(nbins, dtype=np.int64)
    if points_map is None:
        return counts
    pts = np.asarray(points_map, dtype=np.float64).reshape(-1, 2)
    if not len(pts):
        return counts
    fwd, lat = body_frame(pts, pose)
    fwd = fwd[in_swept_body(fwd, lat, cfg.near_m, cfg.far_m, cfg.swept_half_width_m)]
    if not len(fwd):
        return counts
    idx = np.floor((fwd - cfg.near_m) / cfg.bin_m).astype(int)
    idx = np.clip(idx, 0, nbins - 1)
    return np.bincount(idx, minlength=nbins).astype(np.int64)[:nbins]


@dataclass(frozen=True)
class PathObservation:
    """Per-frame Path Keeper input, all channels in the corridor map frame (x right, z forward).

    ``from_split`` converts the Issue #18 :class:`SplitCorridorEvidence`; the ``curb`` channel is
    the curb/boundary ground split out of the frozen four-channel ``hazard`` channel.
    """

    walkable: np.ndarray
    non_walkable: np.ndarray
    curb: np.ndarray
    unknown: np.ndarray

    def __post_init__(self) -> None:
        for name in ("walkable", "non_walkable", "curb", "unknown"):
            arr = np.asarray(getattr(self, name), dtype=np.float64)
            if arr.size == 0:
                arr = np.zeros((0, 2), dtype=np.float64)
            else:
                arr = arr.reshape(-1, 2)
            object.__setattr__(self, name, arr)

    @classmethod
    def from_corridor_evidence(cls, evidence, curb=None) -> "PathObservation":
        """Build from a :class:`CorridorEvidence` (curb defaults to the whole hazard channel).

        The default reproduces the PR #17 semantics for callers that only have the four-channel
        structure; the split harness always passes the explicit curb channel.
        """
        return cls(walkable=evidence.walkable, non_walkable=evidence.non_walkable,
                   curb=evidence.hazard if curb is None else curb,
                   unknown=evidence.unknown)

    @classmethod
    def from_split(cls, split) -> "PathObservation":
        """Build from :class:`blindnav_pc.corridor_evidence.SplitCorridorEvidence`."""
        return cls(walkable=split.walkable, non_walkable=split.non_walkable,
                   curb=split.curb_ground, unknown=split.unknown)


@dataclass(frozen=True)
class PathState:
    """Path Keeper output.  ``status`` is one of CLEAR / OBSERVE / UNKNOWN.

    ``guidance`` is STRAIGHT / LEFT / RIGHT only when ``status == CLEAR``; a non-CLEAR path state
    never carries forward guidance (a surface risk or unseen path demotes it to OBSERVE/UNKNOWN).
    """

    status: str
    guidance: Optional[str]
    reason: str
    coverage: float
    n_points: int
    walkable_offset_m: float
    walker_offset_m: float
    yaw_error_rad: float
    non_walkable_bins: int = 0
    non_walkable_points: int = 0
    curb_bins: int = 0
    curb_points: int = 0
    unknown_bins: int = 0
    unknown_points: int = 0
    walkable_swept_points: int = 0

    def is_pass(self) -> bool:
        return self.status == CLEAR and self.guidance in (STRAIGHT, LEFT, RIGHT)


class PathKeeper:
    """Surface / boundary / drift responsibility for one corridor (stateless per frame).

    The walkable-band geometry (coverage, lateral band, deadbands) is the frozen PR #17
    geometry, so the split cannot silently change how the walkable channel is consumed.  What
    changes is the decision: surface and drift evidence produce OBSERVE/UNKNOWN, never BLOCKED.
    """

    def __init__(self, corridor: Corridor, cfg: Optional[ShadowWalkConfig] = None):
        self.corridor = corridor
        self.cfg = cfg or ShadowWalkConfig()

    # -- helpers ------------------------------------------------------------------
    def _result(self, status: str, guidance: Optional[str], reason: str,
                walker_off: float, band: float, yaw_err: float, coverage: float, n: int,
                *, ev: Optional[Dict[str, int]] = None) -> PathState:
        ev = ev or {}
        return PathState(
            status=status, guidance=guidance, reason=reason, coverage=float(coverage),
            n_points=int(n),
            walkable_offset_m=float(band) if math.isfinite(band) else float("nan"),
            walker_offset_m=float(walker_off) if math.isfinite(walker_off) else float("nan"),
            yaw_error_rad=float(yaw_err),
            non_walkable_bins=int(ev.get("non_walkable_bins", 0)),
            non_walkable_points=int(ev.get("non_walkable_points", 0)),
            curb_bins=int(ev.get("curb_bins", 0)), curb_points=int(ev.get("curb_points", 0)),
            unknown_bins=int(ev.get("unknown_bins", 0)),
            unknown_points=int(ev.get("unknown_points", 0)),
            walkable_swept_points=int(ev.get("walkable_swept_points", 0)))

    def update(self, observation: PathObservation,
               pose: Optional[Pose2D]) -> PathState:
        """One frame of the Path Keeper."""
        c, corr = self.cfg, self.corridor
        if pose is None or not all(math.isfinite(v) for v in (pose.x_m, pose.z_m, pose.yaw_rad)):
            return self._result(UNKNOWN, None, "no_pose", float("nan"), float("nan"),
                                float("nan"), 0.0, 0)
        s0, walker_off = corr.project(pose.x_m, pose.z_m)
        yaw_err = float(math.atan2(math.sin(pose.yaw_rad - corr.heading_at(s0)),
                                   math.cos(pose.yaw_rad - corr.heading_at(s0))))
        half = corr.width_m / 2.0
        nbins = max(1, int(round((c.far_m - c.near_m) / c.bin_m)))  # legacy coverage bins
        nbins_ev = swept_evidence_bin_count(c)

        # ---- walkable band geometry (frozen PR #17 geometry, unchanged) ------------
        walk_pts = np.asarray(observation.walkable, dtype=np.float64).reshape(-1, 2)
        band, coverage, n = 0.0, 0.0, 0
        if len(walk_pts):
            s, off = corr.project_many(walk_pts)
            fwd = s - s0
            keep = ((fwd >= c.near_m) & (fwd <= c.far_m) &
                    (np.abs(off) <= half + c.band_margin_m))
            off, fwd = off[keep], fwd[keep]
            n = int(len(off))
            if n:
                idx = np.floor((fwd - c.near_m) / c.bin_m).astype(int)
                idx = idx[(idx >= 0) & (idx < nbins)]
                counts = np.bincount(idx, minlength=nbins)
                coverage = float(np.mean(counts >= c.points_per_bin))
                band = float(np.median(off))

        # ---- evidence channels in body coordinates --------------------------------
        w_bins = swept_bin_counts(observation.walkable, pose, c, nbins_ev)
        nw_bins = swept_bin_counts(observation.non_walkable, pose, c, nbins_ev)
        curb_bins = swept_bin_counts(observation.curb, pose, c, nbins_ev)
        u_bins = swept_bin_counts(observation.unknown, pose, c, nbins_ev)
        ev = {
            "non_walkable_bins": int(np.count_nonzero(nw_bins >= c.points_per_bin)),
            "non_walkable_points": int(nw_bins.sum()),
            "curb_bins": int(np.count_nonzero(curb_bins >= c.points_per_bin)),
            "curb_points": int(curb_bins.sum()),
            "unknown_bins": int(np.count_nonzero(u_bins >= c.points_per_bin)),
            "unknown_points": int(u_bins.sum()),
            "walkable_swept_points": int(w_bins.sum()),
        }

        # Walker outside the declared corridor band -> drift / boundary risk (OBSERVE, never STOP).
        if abs(walker_off) > c.stop_offset_frac * half:
            return self._result(OBSERVE, None, "off_corridor", walker_off, band, yaw_err,
                                coverage, n, ev=ev)

        # A road/terrain/curb surface inside the body swept path: hold and observe, not STOP.
        if ev["curb_bins"] > 0:
            return self._result(OBSERVE, None, "curb_in_path", walker_off, band, yaw_err,
                                coverage, n, ev=ev)
        if ev["non_walkable_bins"] > 0:
            return self._result(OBSERVE, None, "non_walkable_in_path", walker_off, band,
                                yaw_err, coverage, n, ev=ev)

        # Insufficient surface evidence: never a safe pass.
        if n < c.min_points:
            return self._result(UNKNOWN, None, "walkable_unseen", walker_off, band, yaw_err,
                                coverage, n, ev=ev)
        if coverage < c.min_coverage:
            return self._result(UNKNOWN, None, "corridor_unseen", walker_off, band, yaw_err,
                                coverage, n, ev=ev)
        if ev["unknown_bins"] > 0:
            return self._result(UNKNOWN, None, "unknown_evidence", walker_off, band,
                                yaw_err, coverage, n, ev=ev)

        # Drift: pointing well away from the corridor tangent is a path-boundary risk (OBSERVE).
        if abs(math.degrees(yaw_err)) > c.max_yaw_deg:
            return self._result(OBSERVE, None, "yaw_too_large", walker_off, band, yaw_err,
                                coverage, n, ev=ev)

        # ---- usable path: lateral / yaw / centred guidance (frozen PR #17 geometry) --
        target = float(np.clip(band, -(half - 0.4), half - 0.4))
        error = walker_off - target            # + = walker is right of the visible path -> go left
        if abs(error) > c.deadband_m:
            return self._result(CLEAR, LEFT if error > 0 else RIGHT, "lateral", walker_off,
                                band, yaw_err, coverage, n, ev=ev)
        if abs(math.degrees(yaw_err)) > c.deadband_yaw_deg:
            return self._result(CLEAR, LEFT if yaw_err > 0 else RIGHT, "yaw", walker_off,
                                band, yaw_err, coverage, n, ev=ev)
        return self._result(CLEAR, STRAIGHT, "centred", walker_off, band, yaw_err,
                            coverage, n, ev=ev)
