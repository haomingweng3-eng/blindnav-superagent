#!/usr/bin/env python3
"""A/B/C corridor-evidence evaluation harness on SANPO-Real (measurement only).

Implements Issue #16 / SPEC v1 section 4: three corridor-decision arms, each run on the
same frames under two *input conditions*:

    condition "annotated"  -> GT SANPO segmentation remapped to the v5 5-class ids +
                              oracle geometry (GT depth, GT ground-plane up / height)
    condition "production" -> v5 SegFormer-B0 SANPO fine-tune + Depth-Anything-V2-Small +
                              the existing ``GroundCalibrator`` with pose-derived gravity

    arm A  baseline:        ``ShadowWalkTracker.update(walkable_points, pose)`` (PR #15)
    arm B  negative evidence: ``update_evidence(CorridorEvidence, pose)``,
                              ``temporal_confirm_frames = 0``
    arm C  temporal confirm:  ``update_evidence(CorridorEvidence, pose)``,
                              ``temporal_confirm_frames = 3`` (one tracker per session/condition)

All arms in one condition see identical RGB, masks and geometry; one tracker instance per
(session, condition, arm).  The algorithm change itself lives in
``blindnav_pc/corridor_evidence.py`` + ``blindnav_pc/shadowwalk.py`` (the corridor decision
layer); this tool only measures it and never modifies the algorithm.

Corridor / pose setup is identical to PR #15 (``pc/tools/corridor_truth_eval.py``):
the corridor centreline is the recorded VIO route, the route tangent is a TLS line over
+/-4 m of arclength, ``Pose2D(0, 0, heading - route_tangent)`` and
``Corridor("recorded-route", ((0, 0), (0, 60)), 2.5)``.  The baseline geometry/bins are
``ShadowWalkConfig()``.

Ground-truth scoring (computed ONCE per frame from oracle geometry + raw SANPO ids, so the
denominators are identical for every arm and condition):

* ``gt_walk_pts_swept`` - GT walkable ground points (raw SANPO ids in ``SAFE_IDS``) inside
  the decision swept body: body heading from pose yaw, forward ``near_m..far_m`` (1.5-6 m),
  ``|lateral| <= ShadowWalkConfig.swept_half_width_m`` (0.4 m), GT depth + GT plane.
* ``gt_danger_swept`` - GT ground points of raw ids {1 road, 2 curb, 16 water body,
  18 inaccessible surface, 19 railway track, 30 terrain} PLUS GT obstacle points (raw ids
  {4,7,8,9,10,11,12,13,14,15,20,21,22,23,24,25,26,28} with 0.15 m < height <= 2.0 m)
  inside that same swept body; ``gt_danger_swept = count >= MIN_EVENT_POINTS`` (5).
* PR #15-compat events (map frame, exact PR #15 definitions): ``|lat| <= 0.6``, road id 1
  forward 1.5-6 m, curb id 2 forward 2.5-6 m, >= 5 points -> ``pr15_road_path_band``,
  ``pr15_curb_path_band``; ``pr15_event = road_path_band or curb_path_band``.
* ``pr15_23`` - fixed PR #15 regression set: annotated-condition / arm-A rows with
  ``pr15_event and hint == STRAIGHT``.  The tool verifies the count is 23 on a full run and
  records it; it is a reporting label only and is never fed to the algorithm.

Two GT danger denominators are reported (SPEC amendment v1.1, reporting only; the A/B/C
decision rule and CorridorEvidence API are unchanged):

* PRIMARY ``danger_ground`` - GT non-walkable GROUND points (raw ids in
  ``{1 road, 2 curb, 16 water body, 18 inaccessible surface, 19 railway track, 30 terrain}``)
  with ``>= MIN_EVENT_POINTS`` (5) points inside the swept body.  This is the PR #15
  first-loss / false-safe class.
* SECONDARY ``danger_any`` - ``danger_ground`` OR GT object occupancy (raw ids in
  ``{4,7,8,9,10,11,12,13,14,15,20,21,22,23,24,25,26,28}`` with 0.15 m < height <= 2.0 m) OR
  GT non-walkable-class ground points (role ``ROLE_NON_WALKABLE``), ``>= 5`` points inside
  the same swept body.  This is the broader "any danger present" view, so the safety number
  is never one-sided (BLINDNAV_EVAL_RULES: always report the blocking side too).

Per-row flags (all explicit, no tuning):

    walkable_guidance           = hint in (STRAIGHT, LEFT, RIGHT)
    false_free                  = walkable_guidance and danger_ground          (PRIMARY)
    false_free_any              = walkable_guidance and danger_any             (SECONDARY)
    correct_block               = danger_ground and hint == STOP
    correct_block_ground        = same as correct_block (explicit amendment name)
    correct_block_any           = danger_any and hint == STOP
    unknown_on_danger           = danger_ground and hint == UNKNOWN
    unknown_on_danger_ground    = same as unknown_on_danger (explicit amendment name)
    unknown_on_danger_any       = danger_any and hint == UNKNOWN
    false_stop                  = hint == STOP and not danger_ground
                                  and gt_walk_pts_swept >= MIN_EVENT_POINTS          (PRIMARY)
    false_stop_ground           = same as false_stop (explicit amendment name)
    false_stop_any              = hint == STOP and not danger_any
                                  and gt_walk_pts_swept >= MIN_EVENT_POINTS          (SECONDARY)
    unknown_when_gt_walkable    = hint == UNKNOWN and gt_walk_pts_swept >= MIN_EVENT_POINTS

A second pass marks every row with an action flip vs arm A of the same condition/frame
(``action_flip_vs_A``, ``action_flip_pair = "<hint_A>-><hint_arm>"``).

Outputs in ``--out``: ``per_frame.csv`` (all fields below), ``summary.json`` (frozen key
structure of SPEC section 4 plus the amendment's dual danger keys), ``metrics.csv`` (tidy
numerator/denominator rows) and ``summary.md`` (A/B/C tables per condition).

Denominator conventions used in ``summary.json`` (reported in ``meta.denominators``):

* ``false_free``              admitted walkable guidance / GT-``danger_ground`` frames (PRIMARY)
* ``false_free_any``          admitted walkable guidance / GT-``danger_any`` frames (SECONDARY)
* ``false_free_pr15_gate``    primary false-free / PR #15 in-path event frames (114 full set)
* ``false_stop``              false STOP / frames with no ``danger_ground`` and >= 5 GT
                               walkable points in the swept body (PRIMARY)
* ``false_stop_any``          false STOP / frames with no ``danger_any`` and >= 5 GT
                               walkable points in the swept body (SECONDARY)
* ``unknown``                 UNKNOWN frames / all frames
* ``coverage``                frames with coverage >= ``min_coverage`` / all frames,
                               ``mean_coverage`` over all frames
* ``guidance_coverage``       walkable-guidance frames / all frames
* ``action_flips``            flips vs arm A / all frames of the arm
* ``danger_handling``         legacy alias of ``danger_handling_ground``
* ``danger_handling_ground``  STOP / UNKNOWN / admitted-walkable partitions of the
                               ``danger_ground`` frames; ``blocking_recall = (STOP+UNKNOWN)/danger``
* ``danger_handling_any``     the same partitions on the ``danger_any`` frames
* ``pr15_23``                 counts inside the fixed 23-frame set (denominator 23)

Deterministic: fixed seed, no sampling, no random augmentation, no synthetic data.
Evidence level: DESKTOP_REAL_DATA.  Not device-verified, not user-tested; BlindNav is not a
certified mobility aid and nothing here is a safety claim.

Usage (from ``pc/`` with the venv and the offline env vars, v5 checkpoint local)::

    HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 .venv/bin/python \
        tools/corridor_evidence_eval.py \
        --v5-checkpoint /Users/mima0000/blindnav-real-eval/models/segformer_sanpo_b0_v5.pt \
        --data-root data/sanpo_real/sanpo-real \
        --out out/corridor_evidence
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import sys
import time
from collections import Counter
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

_TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
_PC_DIR = os.path.abspath(os.path.join(_TOOLS_DIR, ".."))
for _p in (_TOOLS_DIR, _PC_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from blindnav_pc.bev import depth_to_level_points
from blindnav_pc.config import Config
from blindnav_pc.ego import wrap_pi
from blindnav_pc.ground import GroundCalibrator, metric_depth, up_from_gravity
from blindnav_pc.shadowwalk import (LEFT, RIGHT, STRAIGHT, STOP, UNKNOWN, Corridor, Pose2D,
                                    ShadowWalkConfig, ShadowWalkTracker, mask_to_map_points)
from corridor_truth_eval import (BODY_HALF_M, CLASS_BOUND, CLASS_COND, CLASS_NON,
                                 CLASS_SAFE, MIN_EVENT_POINTS, SAFE_IDS, frame_dt_s,
                                 gt_remap, load_v5, oracle_plane, predict_v5,
                                 rotation_only_flags, route_frame, route_tangents)

# The evidence fusion layer is the parallel deliverable of this ticket (SPEC sections 1-2).
# The import is defensive only so that the pure measurement helpers below stay importable
# while that module is still being written; ``main`` refuses to run without the frozen API.
try:  # pragma: no cover - the happy path is always exercised by the smoke run
    from blindnav_pc.corridor_evidence import (ROLE_HAZARD, ROLE_IGNORE,
                                               ROLE_NON_WALKABLE, ROLE_WALKABLE,
                                               build_corridor_evidence)
except ImportError:  # pragma: no cover - fallback matches the frozen SPEC values (0,1,2,3)
    ROLE_IGNORE, ROLE_WALKABLE, ROLE_HAZARD, ROLE_NON_WALKABLE = 0, 1, 2, 3
    build_corridor_evidence = None  # type: ignore[assignment]

from eval.sanpo import SanpoSession

# ---------------------------------------------------------------------------
# Arm / condition / command vocabulary (frozen by SPEC section 3-4)
# ---------------------------------------------------------------------------
ARMS = ("A", "B", "C")
COND_ANNOTATED = "annotated"
COND_PRODUCTION = "production"
CONDITIONS = (COND_ANNOTATED, COND_PRODUCTION)
COMMANDS = (STRAIGHT, LEFT, RIGHT, STOP, UNKNOWN)
ARM_DESCRIPTION = {
    "A": "current ShadowWalk baseline (walkable points only)",
    "B": "A + negative-evidence channels (no temporal confirmation)",
    "C": "B + temporal confirmation (temporal_confirm_frames)",
}
CORRIDOR = Corridor("recorded-route", ((0.0, 0.0), (0.0, 60.0)), 2.5)

#: Baseline geometry/bins config: identical to PR #15 (used for the GT reference and the
#: walkable-only arm's projection; the algorithm's own config lives in ShadowWalkTracker).
SW_CFG = ShadowWalkConfig()

#: Raw SANPO label ids used by the PR #15 ground truth (first-loss diagnostic).
#: SAFE_IDS = sidewalk / crosswalk / paved trail / other walkable (road id 1 excluded).
DANGER_GROUND_IDS = (1, 2, 16, 18, 19, 30)   # road, curb, water, inaccessible, rail, terrain
DANGER_OBSTACLE_IDS = (4, 7, 8, 9, 10, 11, 12, 13, 14, 15, 20, 21, 22, 23, 24, 25, 26, 28)
ROAD_ID, CURB_ID = 1, 2
OBSTACLE_MAX_HEIGHT_M = 2.0                  # SPEC section 4 / LayerConfig.head_max_m

#: PR #15-compat curb event starts at 2.5 m because the chest camera cannot see ground
#: closer than ~3 m; road event uses the full 1.5-6 m decision range (PR #15 definition).
PR15_ROAD_NEAR_M, PR15_ROAD_FAR_M = 1.5, 6.0
PR15_CURB_NEAR_M, PR15_CURB_FAR_M = 2.5, 6.0

#: Fixed set expected on the full 481-frame run (PR #15 report / committed per_frame.csv).
PR15_23_EXPECTED = 23

#: CLI-written per-frame columns (documented here so downstream tools have one source).
PER_FRAME_FIELDS = [
    "condition", "arm", "session", "frame", "hint", "reason",
    "coverage", "coverage_bins_ok", "n_points", "walkable_offset_m",
    "yaw_err_deg", "yaw_correction_deg",
    "calib_ok", "calib_reason", "rotation_only",
    "temporal_streak", "hazard_points", "hazard_bins",
    "non_walkable_points", "non_walkable_bins", "unknown_points", "unknown_bins",
    "walkable_swept_points",
    "gt_plane_ok", "gt_walk_pts_swept",
    "gt_danger_ground_pts", "gt_danger_obstacle_pts", "gt_danger_any_pts",
    "gt_danger_pts_swept", "gt_danger_swept", "danger_ground", "danger_any",
    "pr15_road_path_band", "pr15_curb_path_band", "pr15_event", "pr15_23",
    "gt_hint_A_oracle", "walkable_guidance", "false_free", "false_free_any",
    "correct_block", "correct_block_ground", "correct_block_any",
    "unknown_on_danger", "unknown_on_danger_ground", "unknown_on_danger_any",
    "false_stop", "false_stop_ground", "false_stop_any",
    "unknown_when_gt_walkable",
    "action_flip_vs_A", "action_flip_pair",
]

_EVIDENCE_FIELDS = ("temporal_streak", "hazard_points", "hazard_bins",
                    "non_walkable_points", "non_walkable_bins", "unknown_points",
                    "unknown_bins", "walkable_swept_points")


# ---------------------------------------------------------------------------
# Role map: v5 5-class ids -> explicit corridor evidence roles (SPEC section 3)
# ---------------------------------------------------------------------------
_ROLE_LOOKUP: Optional[np.ndarray] = None


def role_map_from_classes(cls: np.ndarray) -> np.ndarray:
    """v5 class map -> corridor-evidence role map (uint8, same shape).

    0 ignore -> ROLE_IGNORE, 1 SAFE -> ROLE_WALKABLE, 2 BOUNDARY -> ROLE_HAZARD,
    3 CONDITIONAL -> ROLE_NON_WALKABLE, 4 NON -> ROLE_NON_WALKABLE.
    """
    global _ROLE_LOOKUP
    if _ROLE_LOOKUP is None:
        _ROLE_LOOKUP = np.array([ROLE_IGNORE, ROLE_WALKABLE, ROLE_HAZARD,
                                 ROLE_NON_WALKABLE, ROLE_NON_WALKABLE], dtype=np.uint8)
    return _ROLE_LOOKUP[np.asarray(cls, dtype=np.uint8)]


def walkable_mask_from_classes(cls: np.ndarray) -> np.ndarray:
    """Binary walkable mask for arm A: exactly ``cls == CLASS_SAFE`` (SPEC section 3)."""
    return (np.asarray(cls) == CLASS_SAFE).astype(np.uint8)


# ---------------------------------------------------------------------------
# Pure geometry / GT scoring helpers
# ---------------------------------------------------------------------------
def body_forward_lateral(dx_m, dz_m, yaw_rad: float) -> Tuple[np.ndarray, np.ndarray]:
    """Map-frame offsets -> (forward, lateral) along the CURRENT BODY HEADING.

    ``dx_m`` is map +x (right of map +z) and ``dz_m`` is map +z (corridor forward).
    forward = dx*sin(yaw) + dz*cos(yaw); lateral = dx*cos(yaw) - dz*sin(yaw).
    """
    dx = np.asarray(dx_m, dtype=np.float64)
    dz = np.asarray(dz_m, dtype=np.float64)
    s, c = math.sin(yaw_rad), math.cos(yaw_rad)
    return dx * s + dz * c, dx * c - dz * s


def oracle_point_table(depth_m: np.ndarray, K: np.ndarray, up: np.ndarray, h: float,
                       pose: Pose2D, cfg: ShadowWalkConfig) -> Dict[str, np.ndarray]:
    """Ungated GT ground-point table (stride = cfg.pixel_stride) in body and map frames.

    The walker sits at ``pose``; ``map_fwd``/``map_lat`` are the map-frame offsets used by
    the PR #15 0.6 m swept-body events, ``fwd``/``lat`` are the same offsets projected on
    the current body heading (SPEC section 4 decision swept body).  ``height`` is the
    ground-plane residual; callers apply the ground / obstacle gates themselves.
    """
    x_lat, z_fwd, height, (vs, us) = depth_to_level_points(
        depth_m, K, up, h, stride=cfg.pixel_stride)
    ok = np.isfinite(x_lat) & np.isfinite(z_fwd)
    x_lat, z_fwd, height, vs, us = x_lat[ok], z_fwd[ok], height[ok], vs[ok], us[ok]
    cy, sy = math.cos(pose.yaw_rad), math.sin(pose.yaw_rad)
    # Same map transform as shadowwalk.mask_to_map_points (offsets from the walker).
    map_lat = x_lat * cy + z_fwd * sy
    map_fwd = -x_lat * sy + z_fwd * cy
    fwd, lat = body_forward_lateral(map_lat, map_fwd, pose.yaw_rad)
    return {"map_lat": map_lat, "map_fwd": map_fwd, "fwd": fwd, "lat": lat,
            "height": height, "vs": vs, "us": us}


def swept_body_mask(fwd, lat, cfg: ShadowWalkConfig, near_m: Optional[float] = None,
                    far_m: Optional[float] = None,
                    half_width_m: Optional[float] = None) -> np.ndarray:
    """Boolean mask of points inside the decision swept body (SPEC section 4)."""
    near = cfg.near_m if near_m is None else near_m
    far = cfg.far_m if far_m is None else far_m
    half = cfg.swept_half_width_m if half_width_m is None else half_width_m
    fwd = np.asarray(fwd, dtype=np.float64)
    lat = np.asarray(lat, dtype=np.float64)
    return (fwd >= near) & (fwd <= far) & (np.abs(lat) <= half)


def gt_walk_count(raw: np.ndarray, height: np.ndarray, swept: np.ndarray,
                  cfg: ShadowWalkConfig) -> int:
    """GT walkable ground points inside the swept body (raw SANPO ids in SAFE_IDS)."""
    ground = np.abs(np.asarray(height)) <= cfg.max_height_m
    return int(np.count_nonzero(np.asarray(swept) & ground & np.isin(raw, SAFE_IDS)))


def gt_danger_counts(raw: np.ndarray, height: np.ndarray, swept: np.ndarray,
                     cfg: ShadowWalkConfig) -> Tuple[int, int]:
    """(danger ground points, danger obstacle points) inside the swept body.

    Danger ground: raw ids in DANGER_GROUND_IDS on the ground plane (|height| <=
    max_height_m).  Danger obstacles: raw ids in DANGER_OBSTACLE_IDS with
    max_height_m < height <= 2.0 m.  Both are counted inside the same swept body.
    """
    raw = np.asarray(raw)
    height = np.asarray(height, dtype=np.float64)
    swept = np.asarray(swept)
    ground = np.abs(height) <= cfg.max_height_m
    danger_ground = swept & ground & np.isin(raw, DANGER_GROUND_IDS)
    obstacle = (height > cfg.max_height_m) & (height <= OBSTACLE_MAX_HEIGHT_M) & \
        np.isin(raw, DANGER_OBSTACLE_IDS)
    return int(np.count_nonzero(danger_ground)), int(np.count_nonzero(swept & obstacle))


def pr15_event_counts(raw: np.ndarray, height: np.ndarray, map_fwd: np.ndarray,
                      map_lat: np.ndarray, cfg: ShadowWalkConfig) -> Dict[str, object]:
    """PR #15-compat road/curb in-path events (exact PR #15 definitions).

    Map frame, ``|lat| <= 0.6`` (0.3 m body half width + 0.3 m margin), ground-gated:
    road id 1 forward 1.5-6 m, curb id 2 forward 2.5-6 m, >= MIN_EVENT_POINTS points.
    """
    raw = np.asarray(raw)
    height = np.asarray(height, dtype=np.float64)
    map_fwd = np.asarray(map_fwd, dtype=np.float64)
    map_lat = np.asarray(map_lat, dtype=np.float64)
    ground = np.abs(height) <= cfg.max_height_m
    body = np.abs(map_lat) <= BODY_HALF_M
    road_pts = int(np.count_nonzero(
        ground & body & (raw == ROAD_ID) &
        (map_fwd >= PR15_ROAD_NEAR_M) & (map_fwd <= PR15_ROAD_FAR_M)))
    curb_pts = int(np.count_nonzero(
        ground & body & (raw == CURB_ID) &
        (map_fwd >= PR15_CURB_NEAR_M) & (map_fwd <= PR15_CURB_FAR_M)))
    road_event = road_pts >= MIN_EVENT_POINTS
    curb_event = curb_pts >= MIN_EVENT_POINTS
    return {"road_path_band": int(road_pts), "curb_path_band": int(curb_pts),
            "road_event": bool(road_event), "curb_event": bool(curb_event),
            "event": bool(road_event or curb_event)}


def danger_swept_flag(danger_points: int,
                      min_points: int = MIN_EVENT_POINTS) -> bool:
    """A frame is a GT-danger event only with at least MIN_EVENT_POINTS points."""
    return int(danger_points) >= int(min_points)


def gt_danger_any_points(raw: np.ndarray, height: np.ndarray, swept: np.ndarray,
                         gt_role: Optional[np.ndarray], cfg: ShadowWalkConfig) -> int:
    """SECONDARY GT danger count (SPEC amendment v1.1): ground danger ids OR object
    occupancy OR GT non-walkable-class ground points, inside the swept body.

    ``gt_role`` is the per-point ROLE map of the GT annotated segmentation (cond/non classes
    map to ROLE_NON_WALKABLE); pass ``None`` to omit that dimension (ids-only view).
    """
    raw = np.asarray(raw)
    height = np.asarray(height, dtype=np.float64)
    swept = np.asarray(swept)
    ground = np.abs(height) <= cfg.max_height_m
    danger_ground = ground & np.isin(raw, DANGER_GROUND_IDS)
    obstacle = ((height > cfg.max_height_m) & (height <= OBSTACLE_MAX_HEIGHT_M)
                & np.isin(raw, DANGER_OBSTACLE_IDS))
    nonwalk_ground = np.zeros_like(danger_ground)
    if gt_role is not None:
        nonwalk_ground = ground & (np.asarray(gt_role) == ROLE_NON_WALKABLE)
    return int(np.count_nonzero(swept & (danger_ground | obstacle | nonwalk_ground)))


def row_flags(hint: str, danger_ground: bool, danger_any: bool,
              gt_walk_pts_swept: int,
              min_points: int = MIN_EVENT_POINTS) -> Dict[str, bool]:
    """Per-row safety / coverage flags (SPEC amendment v1.1 dual danger view).

    PRIMARY = GT non-walkable ground danger (``danger_ground``) - the PR #15 first-loss /
    false-safe class.  SECONDARY = any GT danger (``danger_any``) - includes object
    occupancy and non-walkable-class ground.  Both are reported so the safety number is
    never one-sided; no tuned thresholds.
    """
    guidance = hint in (STRAIGHT, LEFT, RIGHT)
    enough_walkable = int(gt_walk_pts_swept) >= int(min_points)
    false_stop_ground = hint == STOP and not danger_ground and enough_walkable
    return {
        "walkable_guidance": bool(guidance),
        "false_free": bool(guidance and danger_ground),
        "false_free_any": bool(guidance and danger_any),
        "correct_block": bool(danger_ground and hint == STOP),
        "correct_block_ground": bool(danger_ground and hint == STOP),
        "correct_block_any": bool(danger_any and hint == STOP),
        "unknown_on_danger": bool(danger_ground and hint == UNKNOWN),
        "unknown_on_danger_ground": bool(danger_ground and hint == UNKNOWN),
        "unknown_on_danger_any": bool(danger_any and hint == UNKNOWN),
        "false_stop": bool(false_stop_ground),
        "false_stop_ground": bool(false_stop_ground),
        "false_stop_any": bool(hint == STOP and not danger_any and enough_walkable),
        "unknown_when_gt_walkable": bool(hint == UNKNOWN and enough_walkable),
    }


def empty_gt_reference() -> Dict[str, object]:
    """GT reference for a frame where oracle geometry is unavailable."""
    return {
        "plane_ok": False, "up": None, "h": None,
        "gt_walk_pts_swept": 0, "gt_danger_ground_pts": 0, "gt_danger_obstacle_pts": 0,
        "gt_danger_any_pts": 0, "gt_danger_pts_swept": 0,
        "danger_ground": False, "danger_any": False, "gt_danger_swept": False,
        "pr15_road_path_band": 0, "pr15_curb_path_band": 0, "pr15_event": False,
        "gt_hint_A_oracle": None,
    }


def compute_gt_reference(gt, gt_cls: np.ndarray, session: SanpoSession, i: int,
                         pose: Pose2D, cfg: ShadowWalkConfig,
                         corridor: Corridor = CORRIDOR) -> Dict[str, object]:
    """Ground truth scored ONCE per frame from oracle geometry + raw SANPO ids.

    Returns counts/flags plus the GT reference command ``gt_hint_A_oracle`` (existing
    tracker fed the annotated mask and oracle geometry, i.e. annotated/A).  Never uses a
    model prediction as ground truth.  Both danger views (``danger_ground`` primary /
    ``danger_any`` secondary) are computed from the same oracle point table and swept body.
    """
    out = empty_gt_reference()
    plane = oracle_plane(session, i, gt)
    if plane is None:
        return out
    up, h = plane
    table = oracle_point_table(gt.depth_m, session.K, up, h, pose, cfg)
    raw = np.asarray(gt.seg)[table["vs"], table["us"]]
    swept = swept_body_mask(table["fwd"], table["lat"], cfg)
    ground_walk = gt_walk_count(raw, table["height"], swept, cfg)
    danger_ground, danger_obstacle = gt_danger_counts(raw, table["height"], swept, cfg)
    gt_role = role_map_from_classes(gt_cls)[table["vs"], table["us"]]
    danger_any = gt_danger_any_points(raw, table["height"], swept, gt_role, cfg)
    pr15 = pr15_event_counts(raw, table["height"], table["map_fwd"], table["map_lat"], cfg)
    gt_points = mask_to_map_points(walkable_mask_from_classes(gt_cls), gt.depth_m,
                                   session.K, up, h, pose, cfg)
    gt_hint = ShadowWalkTracker(corridor, cfg).update(gt_points, pose)
    ground_event = danger_swept_flag(danger_ground)
    any_event = danger_swept_flag(danger_any)
    out.update({
        "plane_ok": True, "up": up, "h": float(h),
        "gt_walk_pts_swept": int(ground_walk),
        "gt_danger_ground_pts": int(danger_ground),
        "gt_danger_obstacle_pts": int(danger_obstacle),
        "gt_danger_any_pts": int(danger_any),
        # legacy aliases: the pre-amendment column was ground-ids + object occupancy.
        "gt_danger_pts_swept": int(danger_any),
        "danger_ground": bool(ground_event),
        "danger_any": bool(any_event),
        "gt_danger_swept": bool(any_event),
        "pr15_road_path_band": int(pr15["road_path_band"]),
        "pr15_curb_path_band": int(pr15["curb_path_band"]),
        "pr15_event": bool(pr15["event"]),
        "gt_hint_A_oracle": gt_hint.hint,
    })
    return out


# ---------------------------------------------------------------------------
# Per-frame row construction
# ---------------------------------------------------------------------------
def evidence_fields(hint) -> Dict[str, int]:
    """Evidence-channel counters from a ShadowWalkHint (zeros for the walkable-only arm)."""
    out = {}
    for name in _EVIDENCE_FIELDS:
        out[name] = int(getattr(hint, name, 0) or 0)
    return out


def zero_evidence_fields() -> Dict[str, int]:
    return {name: 0 for name in _EVIDENCE_FIELDS}


def make_row(condition: str, arm: str, session_id: str, frame: int, hint, *,
             calib_ok: bool, calib_reason: str, yaw_err_deg: float, rotation_only: bool,
             gt_ref: Dict[str, object], cfg: ShadowWalkConfig) -> Dict[str, object]:
    """One per-frame/per-arm row with every captured field and derived flag."""
    walkable_offset = (round(float(hint.walkable_offset_m), 3)
                       if math.isfinite(float(hint.walkable_offset_m)) else None)
    row: Dict[str, object] = {
        "condition": condition, "arm": arm, "session": session_id, "frame": int(frame),
        "hint": hint.hint, "reason": hint.reason,
        "coverage": round(float(hint.coverage), 3),
        "coverage_bins_ok": bool(hint.coverage >= cfg.min_coverage),
        "n_points": int(hint.n_points),
        "walkable_offset_m": walkable_offset,
        "yaw_err_deg": round(float(yaw_err_deg), 2),
        "yaw_correction_deg": round(math.degrees(float(hint.yaw_error_rad)), 2),
        "calib_ok": bool(calib_ok), "calib_reason": str(calib_reason),
        "rotation_only": bool(rotation_only),
    }
    row.update(evidence_fields(hint))
    for key in ("plane_ok", "gt_walk_pts_swept", "gt_danger_ground_pts",
                "gt_danger_obstacle_pts", "gt_danger_any_pts", "gt_danger_pts_swept",
                "danger_ground", "danger_any", "gt_danger_swept",
                "pr15_road_path_band", "pr15_curb_path_band", "pr15_event",
                "gt_hint_A_oracle"):
        row[key] = gt_ref[key]
    row["gt_plane_ok"] = bool(gt_ref["plane_ok"])
    row.update(row_flags(row["hint"], bool(row["danger_ground"]),
                         bool(row["danger_any"]), int(row["gt_walk_pts_swept"])))
    row["pr15_23"] = False
    row["action_flip_vs_A"] = False
    row["action_flip_pair"] = ""
    return row


def make_no_geometry_row(condition: str, arm: str, session_id: str, frame: int, *,
                         calib_ok: bool, calib_reason: str, yaw_err_deg: float,
                         rotation_only: bool, gt_ref: Dict[str, object],
                         cfg: ShadowWalkConfig) -> Dict[str, object]:
    """Row for a frame with no usable geometry: all arms UNKNOWN / no_metric_depth."""
    class _NoHint:
        hint = UNKNOWN
        reason = "no_metric_depth"
        coverage = 0.0
        n_points = 0
        walkable_offset_m = float("nan")
        yaw_error_rad = math.nan

    row = make_row(condition, arm, session_id, frame, _NoHint(), calib_ok=calib_ok,
                   calib_reason=calib_reason, yaw_err_deg=yaw_err_deg,
                   rotation_only=rotation_only, gt_ref=gt_ref, cfg=cfg)
    row.update(zero_evidence_fields())
    row["yaw_correction_deg"] = None
    return row


# ---------------------------------------------------------------------------
# Second pass: PR #15 23-frame set and action flips vs arm A
# ---------------------------------------------------------------------------
def pr15_23_frames(rows: Sequence[dict]) -> List[Tuple[str, int]]:
    """Fixed PR #15 regression set: annotated/A rows with pr15_event and hint STRAIGHT."""
    frames = sorted({(str(r["session"]), int(r["frame"])) for r in rows
                     if r["condition"] == COND_ANNOTATED and r["arm"] == "A"
                     and bool(r["pr15_event"]) and r["hint"] == STRAIGHT})
    return frames


def compute_action_flips(rows: Sequence[dict]) -> Sequence[dict]:
    """Second pass: mark each row's action flip vs arm A of the same condition/frame."""
    arm_a = {(r["condition"], r["session"], int(r["frame"])): r["hint"]
             for r in rows if r["arm"] == "A"}
    for r in rows:
        key = (r["condition"], r["session"], int(r["frame"]))
        hint_a = arm_a.get(key)
        if r["arm"] == "A" or hint_a is None:
            r["action_flip_vs_A"] = False
            r["action_flip_pair"] = ""
            continue
        flipped = r["hint"] != hint_a
        r["action_flip_vs_A"] = bool(flipped)
        r["action_flip_pair"] = f"{hint_a}->{r['hint']}" if flipped else ""
    return rows


# ---------------------------------------------------------------------------
# Aggregation (frozen summary.json key structure, SPEC section 4)
# ---------------------------------------------------------------------------
def _rate(num: int, den: int) -> Optional[float]:
    return float(num) / float(den) if den else None


def _count(rows: Sequence[dict], key: str) -> int:
    return sum(1 for r in rows if r.get(key))


def core_summary(rows: Sequence[dict], pr15_23_set: set,
                 cfg: ShadowWalkConfig = SW_CFG) -> Dict[str, object]:
    """All frozen metrics for one (condition, arm) or (condition, arm, session) slice.

    Dual danger denominators (SPEC amendment v1.1): PRIMARY ``danger_ground`` (GT
    non-walkable ground, the PR #15 first-loss / false-safe class) and SECONDARY
    ``danger_any`` (adds object occupancy and non-walkable-class ground).  Both views are
    always reported side by side so no safety number is one-sided (BLINDNAV_EVAL_RULES).
    """
    n = len(rows)
    hints = Counter(str(r["hint"]) for r in rows)

    def _danger_frames(key: str) -> int:
        return sum(1 for r in rows if r[key])

    ground_frames = _danger_frames("danger_ground")
    any_frames = _danger_frames("danger_any")
    false_free_n = _count(rows, "false_free")
    false_free_any_n = _count(rows, "false_free_any")
    correct_block_n = _count(rows, "correct_block")
    correct_block_any_n = _count(rows, "correct_block_any")
    unknown_on_danger_n = _count(rows, "unknown_on_danger")
    unknown_on_danger_any_n = _count(rows, "unknown_on_danger_any")
    false_stop_n = _count(rows, "false_stop")
    false_stop_any_n = _count(rows, "false_stop_any")
    # false-stop denominators: frames with no GT danger of that view but enough GT walkable.
    false_stop_den = sum(1 for r in rows
                         if not r["danger_ground"]
                         and int(r["gt_walk_pts_swept"]) >= MIN_EVENT_POINTS)
    false_stop_any_den = sum(1 for r in rows
                             if not r["danger_any"]
                             and int(r["gt_walk_pts_swept"]) >= MIN_EVENT_POINTS)
    pr15_event_n = _count(rows, "pr15_event")
    selection_23 = [r for r in rows if (r["session"], int(r["frame"])) in pr15_23_set]
    coverages = [float(r["coverage"]) for r in rows]
    frames_ge_min = sum(1 for c in coverages if c >= cfg.min_coverage)
    guidance_n = _count(rows, "walkable_guidance")
    flips = [r for r in rows if r.get("action_flip_vs_A")]
    danger_handling_ground = {
        "danger_frames": ground_frames,
        "stop": correct_block_n,
        "unknown": unknown_on_danger_n,
        "walkable": false_free_n,
        "blocking_recall": _rate(correct_block_n + unknown_on_danger_n, ground_frames),
    }
    danger_handling_any = {
        "danger_frames": any_frames,
        "stop": correct_block_any_n,
        "unknown": unknown_on_danger_any_n,
        "walkable": false_free_any_n,
        "blocking_recall": _rate(correct_block_any_n + unknown_on_danger_any_n,
                                 any_frames),
    }
    return {
        "frames": n,
        "hint_counts": {c: int(hints.get(c, 0)) for c in COMMANDS},
        # PRIMARY danger view: GT non-walkable ground in the swept body.
        "false_free": {"numerator": false_free_n, "denominator": ground_frames,
                       "rate": _rate(false_free_n, ground_frames)},
        # SECONDARY danger view: any GT danger (ground + object occupancy + non-walk class).
        "false_free_any": {"numerator": false_free_any_n, "denominator": any_frames,
                           "rate": _rate(false_free_any_n, any_frames)},
        # PR #15 gate: of the frames with a road/curb in-path event (114 full-set frames).
        "false_free_pr15_gate": {
            "numerator": sum(1 for r in rows if r["false_free"] and r["pr15_event"]),
            "denominator": pr15_event_n,
            "rate": _rate(sum(1 for r in rows if r["false_free"] and r["pr15_event"]),
                          pr15_event_n)},
        "false_stop": {"numerator": false_stop_n, "denominator": false_stop_den,
                       "rate": _rate(false_stop_n, false_stop_den)},
        "false_stop_any": {"numerator": false_stop_any_n,
                           "denominator": false_stop_any_den,
                           "rate": _rate(false_stop_any_n, false_stop_any_den)},
        "unknown": {"numerator": int(hints.get(UNKNOWN, 0)), "denominator": n,
                    "rate": _rate(int(hints.get(UNKNOWN, 0)), n)},
        "coverage": {"frames_ge_min_coverage": int(frames_ge_min), "frames": n,
                     "rate": _rate(frames_ge_min, n),
                     "mean_coverage": (float(np.mean(coverages)) if coverages else None)},
        "guidance_coverage": {"numerator": guidance_n, "denominator": n,
                              "rate": _rate(guidance_n, n)},
        # Legacy alias kept for the original frozen key (now = PRIMARY ground view).
        "danger_handling": dict(danger_handling_ground),
        "danger_handling_ground": danger_handling_ground,
        "danger_handling_any": danger_handling_any,
        "pr15_23": {"frames": len(selection_23),
                    "walkable_guidance": sum(1 for r in selection_23
                                             if r["walkable_guidance"]),
                    "stop": sum(1 for r in selection_23 if r["hint"] == STOP),
                    "unknown": sum(1 for r in selection_23 if r["hint"] == UNKNOWN)},
        "action_flips": {"frames": n, "rate": _rate(len(flips), n),
                         "pairs": dict(Counter(str(r["action_flip_pair"]) for r in flips))},
        "events": {
            "road_in_path_ignored": sum(1 for r in rows
                                        if r["pr15_road_path_band"] >= MIN_EVENT_POINTS
                                        and r["hint"] == STRAIGHT),
            "curb_in_path_ignored": sum(1 for r in rows
                                        if r["pr15_curb_path_band"] >= MIN_EVENT_POINTS
                                        and r["hint"] == STRAIGHT),
            "unknown_when_gt_walkable": _count(rows, "unknown_when_gt_walkable"),
            "pr15_event_frames": pr15_event_n,
            "danger_ground_frames": ground_frames,
            "danger_any_frames": any_frames,
        },
        "evidence": {
            "hazard_points_total": sum(int(r["hazard_points"]) for r in rows),
            "non_walkable_points_total": sum(int(r["non_walkable_points"]) for r in rows),
            "unknown_points_total": sum(int(r["unknown_points"]) for r in rows),
            "walkable_swept_points_total": sum(int(r["walkable_swept_points"]) for r in rows),
            "hazard_bins_total": sum(int(r["hazard_bins"]) for r in rows),
            "non_walkable_bins_total": sum(int(r["non_walkable_bins"]) for r in rows),
            "unknown_bins_total": sum(int(r["unknown_bins"]) for r in rows),
            "frames_with_blocking_evidence": sum(
                1 for r in rows if int(r["hazard_bins"]) + int(r["non_walkable_bins"]) > 0),
        },
    }


def summarize(rows: Sequence[dict], pr15_23_set: set,
              conditions: Sequence[str] = CONDITIONS,
              cfg: ShadowWalkConfig = SW_CFG
              ) -> Tuple[Dict[str, object], Dict[str, object]]:
    """Build the frozen ``summary`` and ``per_session`` structures."""
    summary: Dict[str, object] = {}
    per_session: Dict[str, object] = {}
    sessions = sorted({str(r["session"]) for r in rows})
    for condition in conditions:
        for arm in ARMS:
            sel = [r for r in rows if r["condition"] == condition and r["arm"] == arm]
            if not sel:
                continue
            summary[f"{condition}/{arm}"] = core_summary(sel, pr15_23_set, cfg)
            for sid in sessions:
                ssel = [r for r in sel if r["session"] == sid]
                if not ssel:
                    continue
                per_session[f"{condition}/{arm}/{sid}"] = core_summary(
                    ssel, pr15_23_set, cfg)
    return summary, per_session


# ---------------------------------------------------------------------------
# Report writers
# ---------------------------------------------------------------------------
def _pct(x: Optional[float]) -> str:
    return "n/a" if x is None else f"{100.0 * x:.1f}%"


def _ratio(num, den) -> str:
    return f"{num}/{den}"


def write_per_frame_csv(path: str, rows: Sequence[dict]) -> None:
    with open(path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=PER_FRAME_FIELDS, extrasaction="ignore")
        writer.writeheader()
        bool_fields = ("walkable_guidance", "false_free", "false_free_any",
                       "correct_block", "correct_block_ground", "correct_block_any",
                       "unknown_on_danger", "unknown_on_danger_ground",
                       "unknown_on_danger_any", "false_stop", "false_stop_ground",
                       "false_stop_any", "unknown_when_gt_walkable", "pr15_event",
                       "pr15_23", "danger_ground", "danger_any", "gt_danger_swept",
                       "gt_plane_ok", "action_flip_vs_A", "coverage_bins_ok", "calib_ok",
                       "rotation_only")
        for row in rows:
            out = dict(row)
            for flag in bool_fields:
                out[flag] = bool(out.get(flag))
            writer.writerow(out)


def write_metrics_csv(path: str, summary: Dict[str, object],
                      conditions: Sequence[str]) -> None:
    fields = ["condition", "arm", "metric", "numerator", "denominator", "value"]
    with open(path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for condition in conditions:
            for arm in ARMS:
                key = f"{condition}/{arm}"
                if key not in summary:
                    continue
                s = summary[key]
                dhg = s["danger_handling_ground"]
                dha = s["danger_handling_any"]
                metrics = [
                    ("false_free ground (PRIMARY: GT non-walkable ground admitted)",
                     s["false_free"]),
                    ("false_free any (SECONDARY: any GT danger admitted)",
                     s["false_free_any"]),
                    ("false_free PR15 gate (in-path event frames)",
                     s["false_free_pr15_gate"]),
                    ("false_stop ground (PRIMARY)", s["false_stop"]),
                    ("false_stop any (SECONDARY)", s["false_stop_any"]),
                    ("unknown", s["unknown"]),
                    ("coverage>=min", {"numerator": s["coverage"]["frames_ge_min_coverage"],
                                       "denominator": s["coverage"]["frames"],
                                       "rate": s["coverage"]["rate"]}),
                    ("guidance coverage", s["guidance_coverage"]),
                    ("danger blocking recall ground (STOP+UNKNOWN)",
                     {"numerator": dhg["stop"] + dhg["unknown"],
                      "denominator": dhg["danger_frames"],
                      "rate": dhg["blocking_recall"]}),
                    ("danger blocking recall any (STOP+UNKNOWN)",
                     {"numerator": dha["stop"] + dha["unknown"],
                      "denominator": dha["danger_frames"],
                      "rate": dha["blocking_recall"]}),
                    ("road-in-path ignored", {"numerator": s["events"]["road_in_path_ignored"],
                                              "denominator": s["frames"],
                                              "rate": _rate(
                                                  s["events"]["road_in_path_ignored"],
                                                  s["frames"])}),
                    ("curb-in-path ignored", {"numerator": s["events"]["curb_in_path_ignored"],
                                              "denominator": s["frames"],
                                              "rate": _rate(
                                                  s["events"]["curb_in_path_ignored"],
                                                  s["frames"])}),
                    ("unknown when GT walkable",
                     {"numerator": s["events"]["unknown_when_gt_walkable"],
                      "denominator": s["frames"],
                      "rate": _rate(s["events"]["unknown_when_gt_walkable"], s["frames"])}),
                    ("action flips vs arm A", s["action_flips"]),
                ]
                for name, metric in metrics:
                    writer.writerow({"condition": condition, "arm": arm, "metric": name,
                                     "numerator": metric.get("numerator"),
                                     "denominator": metric.get("denominator"),
                                     "value": metric.get("rate")})
                writer.writerow({"condition": condition, "arm": arm,
                                 "metric": "mean coverage", "numerator": None,
                                 "denominator": s["coverage"]["frames"],
                                 "value": s["coverage"]["mean_coverage"]})


def write_summary_md(path: str, summary: Dict[str, object], meta: dict,
                     conditions: Sequence[str]) -> None:
    lines = [
        "# A/B/C corridor negative-evidence evaluation — machine summary",
        "",
        f"Frames: {meta['frames']} across {len(meta['sessions'])} SANPO-Real sessions "
        f"({', '.join(f'{k}={v}' for k, v in meta['sessions'].items())}).",
        "",
        "Conditions: **annotated** = GT SANPO segmentation remapped to the v5 5-class ids + "
        "oracle geometry (GT depth, GT ground-plane up/height); **production** = v5 "
        "SegFormer-B0 + Depth-Anything-V2-Small + the existing GroundCalibrator with "
        "pose-derived gravity.",
        "",
        f"Arms: A = {ARM_DESCRIPTION['A']}; B = {ARM_DESCRIPTION['B']}; "
        f"C = {ARM_DESCRIPTION['C']} "
        f"(temporal_confirm_frames={meta['temporal_confirm_frames']}).",
        "",
        "Decision swept body for GT scoring: body heading, "
        f"{meta['config']['near_m']}-{meta['config']['far_m']} m, "
        f"|lateral| <= {meta['config']['swept_half_width_m']} m "
        "(body_width/2 + margin); GT danger >= "
        f"{meta['min_event_points']} points.",
        "",
        f"**PRIMARY danger = GT non-walkable ground** (road/curb/water/inaccessible/rail/"
        f"terrain) in the swept body - the PR #15 first-loss / false-safe class. "
        f"**SECONDARY danger = any GT danger** (primary + object occupancy + non-walkable-"
        f"class ground points). Both denominators are shown side by side: no one-sided "
        f"safety number (BLINDNAV_EVAL_RULES).",
        "",
        f"PR #15 regression set: {meta['pr15_23_count']} frames "
        f"(expected {PR15_23_EXPECTED}; verified={meta['pr15_23_verified']}, "
        f"scope={meta['pr15_23_verified_scope']}). "
        f"PR #15 road/curb in-path event frames: {meta['pr15_event_frames']}.",
        "",
        "Denominators: false-free = GT-danger frames of the same view (ground / any); "
        "false-free PR15 gate = PR15 in-path event frames; false-stop = frames with no "
        "danger of that view and >= "
        f"{meta['min_event_points']} GT walkable points in the swept body; UNKNOWN and "
        "guidance coverage = all frames; action flips = all frames of the arm.",
        "",
        "Evidence level: **DESKTOP_REAL_DATA** (offline PC, CPU/MPS). Not device-verified, "
        "not user-tested. BlindNav is not a certified mobility aid; no safety claim.",
        "",
    ]
    for condition in conditions:
        lines += [f"## Input condition: {condition}", ""]
        lines += [
            "| arm | frames | false-free GROUND (primary) | false-free ANY (secondary) | "
            "false-free PR15 gate | false-stop GROUND | false-stop ANY | "
            "danger blocking recall GROUND | danger blocking recall ANY |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
        for arm in ARMS:
            key = f"{condition}/{arm}"
            if key not in summary:
                continue
            s = summary[key]
            dhg = s["danger_handling_ground"]
            dha = s["danger_handling_any"]
            lines.append(
                f"| {arm} | {s['frames']} | "
                f"{_ratio(s['false_free']['numerator'], s['false_free']['denominator'])} "
                f"({_pct(s['false_free']['rate'])}) | "
                f"{_ratio(s['false_free_any']['numerator'], s['false_free_any']['denominator'])} "
                f"({_pct(s['false_free_any']['rate'])}) | "
                f"{_ratio(s['false_free_pr15_gate']['numerator'], s['false_free_pr15_gate']['denominator'])} "
                f"({_pct(s['false_free_pr15_gate']['rate'])}) | "
                f"{_ratio(s['false_stop']['numerator'], s['false_stop']['denominator'])} "
                f"({_pct(s['false_stop']['rate'])}) | "
                f"{_ratio(s['false_stop_any']['numerator'], s['false_stop_any']['denominator'])} "
                f"({_pct(s['false_stop_any']['rate'])}) | "
                f"{dhg['stop']}+{dhg['unknown']}/{dhg['danger_frames']} "
                f"({_pct(dhg['blocking_recall'])}) | "
                f"{dha['stop']}+{dha['unknown']}/{dha['danger_frames']} "
                f"({_pct(dha['blocking_recall'])}) |")
        lines.append("")
        lines += [
            "| arm | UNKNOWN | mean coverage | coverage>=min | guidance coverage | "
            "flips vs A |",
            "|---|---:|---:|---:|---:|---:|",
        ]
        for arm in ARMS:
            key = f"{condition}/{arm}"
            if key not in summary:
                continue
            s = summary[key]
            mean_cov = s["coverage"]["mean_coverage"]
            lines.append(
                f"| {arm} | "
                f"{_ratio(s['unknown']['numerator'], s['unknown']['denominator'])} "
                f"({_pct(s['unknown']['rate'])}) | "
                f"{('n/a' if mean_cov is None else f'{mean_cov:.3f}')} | "
                f"{_ratio(s['coverage']['frames_ge_min_coverage'], s['coverage']['frames'])} "
                f"({_pct(s['coverage']['rate'])}) | "
                f"{_ratio(s['guidance_coverage']['numerator'], s['guidance_coverage']['denominator'])} "
                f"({_pct(s['guidance_coverage']['rate'])}) | "
                f"{_ratio(sum(s['action_flips']['pairs'].values()), s['action_flips']['frames'])} "
                f"({_pct(s['action_flips']['rate'])}) |")
        lines.append("")
        lines += [
            "| arm | of 23 walkable guidance | of 23 STOP | of 23 UNKNOWN | "
            "road-in-path ignored (STRAIGHT) | curb-in-path ignored (STRAIGHT) | "
            "unknown when GT walkable | hazard / non-walkable / unknown points | "
            "blocking-evidence frames | danger_ground frames | danger_any frames |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
        for arm in ARMS:
            key = f"{condition}/{arm}"
            if key not in summary:
                continue
            s = summary[key]
            ev = s["evidence"]
            lines.append(
                f"| {arm} | {s['pr15_23']['walkable_guidance']} | {s['pr15_23']['stop']} | "
                f"{s['pr15_23']['unknown']} | {s['events']['road_in_path_ignored']} | "
                f"{s['events']['curb_in_path_ignored']} | "
                f"{s['events']['unknown_when_gt_walkable']} | "
                f"{ev['hazard_points_total']} / {ev['non_walkable_points_total']} / "
                f"{ev['unknown_points_total']} | {ev['frames_with_blocking_evidence']} | "
                f"{s['events']['danger_ground_frames']} | "
                f"{s['events']['danger_any_frames']} |")
        lines.append("")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


def write_outputs(out_dir: str, rows: Sequence[dict], summary: Dict[str, object],
                  per_session: Dict[str, object], meta: dict,
                  conditions: Sequence[str]) -> None:
    os.makedirs(out_dir, exist_ok=True)
    write_per_frame_csv(os.path.join(out_dir, "per_frame.csv"), rows)
    with open(os.path.join(out_dir, "summary.json"), "w", encoding="utf-8") as fh:
        json.dump({"meta": meta, "summary": summary, "per_session": per_session},
                  fh, indent=2, ensure_ascii=False)
    write_metrics_csv(os.path.join(out_dir, "metrics.csv"), summary, conditions)
    write_summary_md(os.path.join(out_dir, "summary.md"), summary, meta, conditions)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        description="A/B/C corridor negative-evidence evaluation on SANPO-Real "
                    "(measurement only).",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data-root",
                    default=os.path.join(os.path.dirname(__file__), "..", "data",
                                         "sanpo_real", "sanpo-real"))
    ap.add_argument("--sessions", nargs="*", default=None,
                    help="session ids (default: every session under --data-root)")
    ap.add_argument("--camera", default="camera_chest")
    ap.add_argument("--v5-checkpoint", required=True,
                    help="local path to segformer_sanpo_b0_v5.pt (not committed)")
    ap.add_argument("--out",
                    default=os.path.join(os.path.dirname(__file__), "..", "out",
                                         "corridor_evidence"))
    ap.add_argument("--conditions", nargs="*", default=list(CONDITIONS),
                    choices=list(CONDITIONS),
                    help="input conditions to evaluate (default: both)")
    ap.add_argument("--temporal-confirm-frames", type=int, default=3,
                    help="arm C temporal confirmation streak (one tracker per session)")
    ap.add_argument("--max-width", type=int, default=640)
    ap.add_argument("--limit-per-session", type=int, default=0,
                    help="debug only: evaluate at most N frames per session (0 = all)")
    ap.add_argument("--device", default=None)
    ap.add_argument("--seed", type=int, default=0)
    return ap.parse_args(argv)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def _arm_configs(temporal_confirm_frames: int) -> Dict[str, ShadowWalkConfig]:
    return {
        "A": ShadowWalkConfig(),
        "B": ShadowWalkConfig(temporal_confirm_frames=0),
        "C": ShadowWalkConfig(temporal_confirm_frames=int(temporal_confirm_frames)),
    }


def evaluate_session(session: SanpoSession, model, device: str, depth_estimator,
                     cfg: Config, arm_cfgs: Dict[str, ShadowWalkConfig],
                     conditions: Sequence[str], limit_per_session: int = 0) -> List[dict]:
    """All A/B/C rows for one session, same frames/order as PR #15."""
    sid = os.path.basename(session.dir.rstrip("/"))
    indices = session.available_frames()
    if limit_per_session and limit_per_session > 0:
        indices = indices[:limit_per_session]
    if len(indices) < 2:
        print(f"skip {sid}: fewer than 2 usable frames", file=sys.stderr)
        return []
    pos, headings = route_frame(session, indices)
    tangents = route_tangents(pos)
    dt = frame_dt_s(indices, session.fps)
    rotation = rotation_only_flags(headings, tangents, dt)
    trackers = {(condition, arm): ShadowWalkTracker(CORRIDOR, arm_cfgs[arm])
                for condition in conditions for arm in ARMS}
    calibrator = GroundCalibrator(cfg.ground)
    print(f"[{sid}] frames={len(indices)} "
          f"path={np.linalg.norm(np.diff(pos, axis=0), axis=1).sum():.1f} m "
          f"rot_only_frames={int(rotation.sum())}", flush=True)
    rows: List[dict] = []
    t_start = time.time()
    for k, i in enumerate(indices):
        inp = session.inputs(i)
        gt = session.gt(i)
        gt_cls = gt_remap(gt.seg)
        v5_cls = predict_v5(model, device, inp.bgr, gt.seg.shape)
        q_dav2 = depth_estimator.predict(inp.bgr)
        up_pose = up_from_gravity(inp.gravity_cam)

        tang_yaw = math.atan2(float(tangents[k][0]), float(tangents[k][1]))
        yaw_err_pose = float(wrap_pi(headings[k] - tang_yaw))
        pose = Pose2D(0.0, 0.0, yaw_err_pose)
        gt_ref = compute_gt_reference(gt, gt_cls, session, i, pose, SW_CFG)

        # Production geometry: existing GroundCalibrator with the pose-derived gravity
        # proxy; one calibration call per frame (the input condition is identical for all
        # three arms), exactly as in PR #15's production branch.
        v5_walk = walkable_mask_from_classes(v5_cls)
        fit = calibrator.update(q_dav2, v5_walk, session.K, up_pose, up_source="attitude")
        if fit.ok:
            depth_prod = metric_depth(q_dav2, fit.a, fit.b, cfg.ground.max_depth_m)
            up_prod = fit.up if fit.up is not None else up_pose
        else:
            depth_prod = None
            up_prod = up_pose
        h_prod = float(cfg.ground.camera_height_m)

        for condition in conditions:
            if condition == COND_ANNOTATED:
                cls = gt_cls
                depth = gt.depth_m if gt_ref["plane_ok"] else None
                up = gt_ref["up"]
                h = gt_ref["h"]
                calib_ok = bool(gt_ref["plane_ok"])
                calib_reason = "" if gt_ref["plane_ok"] else "no_gt_plane"
            else:
                cls = v5_cls
                depth = depth_prod
                up = up_prod
                h = h_prod
                calib_ok = bool(fit.ok)
                calib_reason = fit.reason
            if depth is None:
                for arm in ARMS:
                    rows.append(make_no_geometry_row(
                        condition, arm, sid, i, calib_ok=calib_ok,
                        calib_reason=calib_reason, yaw_err_deg=math.degrees(yaw_err_pose),
                        rotation_only=bool(rotation[k]), gt_ref=gt_ref, cfg=SW_CFG))
                continue
            walk_pts = mask_to_map_points(walkable_mask_from_classes(cls), depth,
                                          session.K, up, h, pose, SW_CFG)
            evidence = build_corridor_evidence(
                role_map_from_classes(cls), depth, session.K, up, h, pose,
                pixel_stride=SW_CFG.pixel_stride)
            for arm in ARMS:
                tracker = trackers[(condition, arm)]
                if arm == "A":
                    hint = tracker.update(walk_pts, pose)
                else:
                    hint = tracker.update_evidence(evidence, pose)
                rows.append(make_row(
                    condition, arm, sid, i, hint, calib_ok=calib_ok,
                    calib_reason=calib_reason, yaw_err_deg=math.degrees(yaw_err_pose),
                    rotation_only=bool(rotation[k]), gt_ref=gt_ref, cfg=SW_CFG))
        if (k + 1) % 25 == 0 or k + 1 == len(indices):
            print(f"  {k + 1}/{len(indices)} frames, {time.time() - t_start:.0f}s",
                  flush=True)
    return rows


def _build_meta(args, frame_counts: Dict[str, int], pr15_23: Sequence[Tuple[str, int]],
                pr15_event_frames: int, conditions: Sequence[str]) -> dict:
    count = len(pr15_23)
    return {
        "tool": "pc/tools/corridor_evidence_eval.py",
        "ticket": "Issue #16 P1 Path Keeper: negative-evidence fusion in the corridor "
                  "decision layer",
        "data_root": os.path.abspath(args.data_root),
        "v5_checkpoint": os.path.abspath(args.v5_checkpoint),
        "frames": int(sum(frame_counts.values())),
        "sessions": {sid: int(n) for sid, n in frame_counts.items()},
        "conditions": list(conditions),
        "arms": list(ARMS),
        "arm_description": {arm: ARM_DESCRIPTION[arm] for arm in ARMS},
        "temporal_confirm_frames": int(args.temporal_confirm_frames),
        "max_width": int(args.max_width),
        "camera": str(args.camera),
        "seed": int(args.seed),
        "min_event_points": int(MIN_EVENT_POINTS),
        "limit_per_session": int(args.limit_per_session),
        "corridor": {"name": CORRIDOR.name, "width_m": CORRIDOR.width_m,
                     "pose": "recorded VIO route; Pose2D(0,0,heading-route_tangent)"},
        "config": {
            "near_m": SW_CFG.near_m, "far_m": SW_CFG.far_m, "bin_m": SW_CFG.bin_m,
            "points_per_bin": SW_CFG.points_per_bin,
            "min_coverage": SW_CFG.min_coverage, "min_points": SW_CFG.min_points,
            "body_width_m": getattr(SW_CFG, "body_width_m", None),
            "body_margin_m": getattr(SW_CFG, "body_margin_m", None),
            "swept_half_width_m": getattr(SW_CFG, "swept_half_width_m", None),
            "pixel_stride": SW_CFG.pixel_stride, "max_height_m": SW_CFG.max_height_m,
        },
        "role_map": {"CLASS_IGNORE": int(ROLE_IGNORE),
                     "CLASS_SAFE": int(ROLE_WALKABLE),
                     "CLASS_BOUNDARY": int(ROLE_HAZARD),
                     "CLASS_CONDITIONAL": int(ROLE_NON_WALKABLE),
                     "CLASS_NON": int(ROLE_NON_WALKABLE)},
        "gt_scoring": {
            "condition": "oracle geometry (GT depth + GT ground-plane up/height)",
            "safe_ids": list(SAFE_IDS),
            "danger_views": {
                "danger_ground_primary": "raw ids in danger_ground_ids on the ground plane",
                "danger_any_secondary": "danger_ground OR danger_obstacle_ids with "
                                        "max_height_m < height <= 2.0 m OR GT "
                                        "non-walkable-class ground points "
                                        "(role ROLE_NON_WALKABLE)",
            },
            "danger_ground_ids": list(DANGER_GROUND_IDS),
            "danger_obstacle_ids": list(DANGER_OBSTACLE_IDS),
            "danger_obstacle_height_m": [float(SW_CFG.max_height_m),
                                         float(OBSTACLE_MAX_HEIGHT_M)],
            "min_event_points": int(MIN_EVENT_POINTS),
            "pr15_body_half_m": float(BODY_HALF_M),
            "pr15_road_forward_m": [PR15_ROAD_NEAR_M, PR15_ROAD_FAR_M],
            "pr15_curb_forward_m": [PR15_CURB_NEAR_M, PR15_CURB_FAR_M],
        },
        "denominators": {
            # PRIMARY danger view: GT non-walkable ground (PR #15 first-loss class).
            "false_free": "GT danger_ground frames",
            "false_stop": "frames with no danger_ground and >=5 GT walkable points "
                          "in the swept body",
            "danger_handling_ground": "GT danger_ground frames; "
                                     "blocking_recall=(STOP+UNKNOWN)/danger_ground",
            # SECONDARY danger view: any GT danger (ground + object occupancy + non-walk class).
            "false_free_any": "GT danger_any frames",
            "false_stop_any": "frames with no danger_any and >=5 GT walkable points "
                              "in the swept body",
            "danger_handling_any": "GT danger_any frames; "
                                  "blocking_recall=(STOP+UNKNOWN)/danger_any",
            # Legacy alias of danger_handling_ground (original frozen key).
            "danger_handling": "alias of danger_handling_ground (PRIMARY view)",
            "false_free_pr15_gate": "PR15 in-path event frames",
            "unknown": "all frames",
            "coverage": "all frames (frames_ge_min_coverage / frames); mean over all frames",
            "guidance_coverage": "all frames",
            "action_flips": "all frames of the arm",
            "pr15_23": "fixed 23-frame set (denominator 23)",
        },
        "pr15_event_frames": int(pr15_event_frames),
        "pr15_23_expected": PR15_23_EXPECTED,
        "pr15_23_count": int(count),
        "pr15_23_verified": bool(count == PR15_23_EXPECTED),
        "pr15_23_verified_scope": (
            "full_run" if (int(args.limit_per_session) == 0
                           and COND_ANNOTATED in conditions) else
            ("partial_run" if int(args.limit_per_session) > 0
             else "annotated_condition_not_evaluated")),
        "pr15_23_frames": [[sid, int(frame)] for sid, frame in pr15_23],
        "evidence_level": "DESKTOP_REAL_DATA",
        "synthetic_acceptance_data": False,
        "algorithm_changed_by_this_tool": False,
        "notes": "Measurement only. One tracker per (session, condition, arm); arm C uses "
                 "temporal_confirm_frames. The 23-frame set is a reporting label only and "
                 "is never fed into the algorithm.",
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    np.random.seed(int(args.seed))
    if build_corridor_evidence is None:
        print("BLOCKED_MISSING_API: blindnav_pc.corridor_evidence.build_corridor_evidence "
              "is not importable (the evidence-fusion layer is not on disk yet).",
              file=sys.stderr)
        return 2
    data_root = os.path.abspath(args.data_root)
    if not os.path.isdir(data_root):
        print(f"BLOCKED_MISSING_DATA: {data_root} does not exist", file=sys.stderr)
        return 2
    sessions = args.sessions or sorted(d for d in os.listdir(data_root)
                                       if os.path.isdir(os.path.join(data_root, d)))
    if not sessions:
        print(f"BLOCKED_MISSING_DATA: no sessions under {data_root}", file=sys.stderr)
        return 2
    conditions = list(args.conditions)
    cfg = Config()
    arm_cfgs = _arm_configs(args.temporal_confirm_frames)
    model, device = load_v5(args.v5_checkpoint, args.device)
    from blindnav_pc.depth import DepthEstimator
    depth_estimator = DepthEstimator(device=device)

    all_rows: List[dict] = []
    frame_counts: Dict[str, int] = {}
    t_start = time.time()
    for sid in sessions:
        session = SanpoSession(os.path.join(data_root, sid), args.camera,
                               max_width=args.max_width)
        # Same per-session camera height handling as PR #15 (description.json / camera
        # default), used by the production ground calibration.
        cfg.ground.camera_height_m = float(session.camera_height_m)
        rows = evaluate_session(session, model, device, depth_estimator, cfg, arm_cfgs,
                                conditions, args.limit_per_session)
        if not rows:
            continue
        frame_counts[sid] = len(rows) // (len(conditions) * len(ARMS))
        all_rows.extend(rows)
        print(f"  session done in {time.time() - t_start:.0f}s total", flush=True)

    if not all_rows:
        print("BLOCKED_NO_FRAMES: no frames were evaluated", file=sys.stderr)
        return 2

    # Second pass 1: fixed PR #15 23-frame set (reporting label only).
    pr15_23 = pr15_23_frames(all_rows)
    if int(args.limit_per_session) == 0 and COND_ANNOTATED in conditions:
        # SPEC: the fixed PR #15 regression set is 23 frames; assert it on a full run.
        if len(pr15_23) != PR15_23_EXPECTED:
            raise SystemExit(
                f"PR15_23_ASSERTION_FAILED: annotated/A pr15_event & STRAIGHT count "
                f"{len(pr15_23)} != expected {PR15_23_EXPECTED}")
    pr15_23_set = set(pr15_23)
    for row in all_rows:
        row["pr15_23"] = bool((row["session"], int(row["frame"])) in pr15_23_set)
    # Second pass 2: action flips vs arm A of the same condition.
    compute_action_flips(all_rows)

    pr15_event_frames = sum(1 for r in all_rows
                            if r["condition"] == conditions[0] and r["arm"] == "A"
                            and bool(r["pr15_event"]))
    summary, per_session = summarize(all_rows, pr15_23_set, conditions)
    meta = _build_meta(args, frame_counts, pr15_23, pr15_event_frames, conditions)
    write_outputs(args.out, all_rows, summary, per_session, meta, conditions)

    print("")
    print(f"PR15_23: count={len(pr15_23)} expected={PR15_23_EXPECTED} "
          f"verified={len(pr15_23) == PR15_23_EXPECTED} "
          f"scope={meta['pr15_23_verified_scope']}")
    print(f"PR15_EVENT_FRAMES: {pr15_event_frames}")
    for condition in conditions:
        for arm in ARMS:
            s = summary.get(f"{condition}/{arm}")
            if s is None:
                continue
            print(f"SUMMARY {condition}/{arm}: frames={s['frames']} "
                  f"hints={s['hint_counts']} "
                  f"false_free[ground]={s['false_free']['numerator']}/"
                  f"{s['false_free']['denominator']} "
                  f"false_free[any]={s['false_free_any']['numerator']}/"
                  f"{s['false_free_any']['denominator']} "
                  f"false_stop[ground]={s['false_stop']['numerator']}/"
                  f"{s['false_stop']['denominator']} "
                  f"false_stop[any]={s['false_stop_any']['numerator']}/"
                  f"{s['false_stop_any']['denominator']} "
                  f"unknown={s['unknown']['numerator']}/{s['unknown']['denominator']} "
                  f"flips={sum(s['action_flips']['pairs'].values())}/"
                  f"{s['action_flips']['frames']} "
                  f"pr15_23=[walk={s['pr15_23']['walkable_guidance']},"
                  f"stop={s['pr15_23']['stop']},unk={s['pr15_23']['unknown']}]")
    print(f"\nwrote {args.out}/per_frame.csv, summary.json, metrics.csv, summary.md "
          f"({len(all_rows)} rows, {time.time() - t_start:.0f}s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
