#!/usr/bin/env python3
"""A/B/C responsibility-split evaluation on SANPO-Real (Issue #18) + rebuilt measurement.

One structural change (Path Keeper / Obstacle Guardian / Safety Coordinator) is measured against
the PR #17 policy as-is on exactly the same 481 SANPO-Real frames, first with human-annotated
segmentation + oracle geometry (``annotated``) and then with the production input chain
(``production``: v5 SegFormer-B0 SANPO fine-tune + Depth-Anything-V2-Small + existing ground
calibration).  The three arms share frames, order, corridor/pose setup and GT scoring:

    arm A  PR #17 as-is: four-channel negative-evidence fusion with no temporal confirmation
           (PR #17's evaluated policy; ``ShadowWalkTracker.update_evidence``,
           ``temporal_confirm_frames = 0``)
    arm B  responsibility split: Path Keeper (surface / curb / boundary / drift -> CLEAR /
           OBSERVE / UNKNOWN) + Obstacle Guardian in ``coarse`` mode (legacy PR #17 support rule)
           + Safety Coordinator fusion
    arm C  B + spatial risk fusion: Obstacle Guardian in ``spatial`` mode (3D occupancy cells,
           swept-body intersection, actual clearance, depth/height/distance gates, temporal
           continuity; no fixed N-point collision rule)

All arms in one condition see byte-identical RGB, masks and geometry.  One tracker / guardian per
(session, condition, arm) so temporal state is per session.

GT scoring is computed once per frame from oracle geometry + raw SANPO label ids and is identical
for every arm (same denominators).  The rebuilt measurement is implemented and unit-tested in
``responsibility_split_metrics.py``: curb/road miss, 3D occupancy miss, true false-stop on a
confirmed clear path, guidance coverage, UNKNOWN share, decision flips and latency to the first
hazard warning, each with numerator/denominator and per-session grouping, with NOT_EVALUABLE when
the data cannot decide.

Deterministic: fixed seed, no sampling, no random augmentation, no synthetic acceptance data.
Evidence level: DESKTOP_REAL_DATA.  Not device-verified, not user-tested; BlindNav is not a
certified mobility aid and nothing here is a safety claim.  No Android ``app/`` change; no STOP
Safety Policy / MotionAuthority / ArrivalAuthority change; frozen PR #15/#17 sets are read-only.

Usage (from ``pc/`` with the venv and the offline env vars, v5 checkpoint local)::

    HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 .venv/bin/python \
        tools/responsibility_split_eval.py --conditions annotated \
        --data-root data/sanpo_real/sanpo-real --out out/responsibility_split

    HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 .venv/bin/python \
        tools/responsibility_split_eval.py \
        --v5-checkpoint /Users/mima0000/blindnav-real-eval/models/segformer_sanpo_b0_v5.pt \
        --data-root data/sanpo_real/sanpo-real --out out/responsibility_split \
        --save-failures 8
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import sys
import time
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

_TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
_PC_DIR = os.path.abspath(os.path.join(_TOOLS_DIR, ".."))
for _p in (_TOOLS_DIR, _PC_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from blindnav_pc.corridor_evidence import (ROLE_IGNORE, ROLE_WALKABLE,
                                           build_split_corridor_evidence)
from blindnav_pc.ego import wrap_pi
from blindnav_pc.ground import GroundCalibrator, metric_depth, up_from_gravity
from blindnav_pc.obstacle_guardian import COARSE, SPATIAL, ObstacleGuardian, ObstacleRisk
from blindnav_pc.path_keeper import PathKeeper, PathObservation, PathState
from blindnav_pc.safety_coordinator import SafetyCoordinator
from blindnav_pc.safety_states import BLOCKED, CLEAR, UNKNOWN
from blindnav_pc.shadowwalk import (LEFT, RIGHT, STRAIGHT, STOP, UNKNOWN as HINT_UNKNOWN,
                                    Corridor, Pose2D, ShadowWalkConfig, ShadowWalkTracker,
                                    mask_to_map_points)
from blindnav_pc.config import Config

import responsibility_split_metrics as rsm
from corridor_evidence_eval import (DANGER_GROUND_IDS, DANGER_OBSTACLE_IDS, MIN_EVENT_POINTS,
                                    OBSTACLE_MAX_HEIGHT_M, SW_CFG, danger_swept_flag,
                                    gt_danger_any_points, gt_danger_counts, gt_walk_count,
                                    oracle_point_table, pr15_event_counts,
                                    role_map_from_classes, swept_body_mask,
                                    walkable_mask_from_classes)
from corridor_truth_eval import (SAFE_IDS, frame_dt_s, gt_remap, load_v5,
                                 oracle_plane, predict_v5, rotation_only_flags, route_frame,
                                 route_tangents)
from eval.sanpo import SanpoSession

ARMS = ("A", "B", "C")
COND_ANNOTATED = "annotated"
COND_PRODUCTION = "production"
CONDITIONS = (COND_ANNOTATED, COND_PRODUCTION)
CORRIDOR = Corridor("recorded-route", ((0.0, 0.0), (0.0, 60.0)), 2.5)

ARM_DESCRIPTION = {
    "A": "PR #17 as-is: four-channel negative-evidence fusion, no temporal confirmation",
    "B": "responsibility split: Path Keeper + Obstacle Guardian (coarse) + Safety Coordinator",
    "C": "B + spatial risk fusion (3D occupancy, swept intersection, clearance, continuity)",
}

#: Fixed frames expected on the full SANPO-Real run (PR #15/#17 frozen set).
EXPECTED_FRAMES = 481
PR15_23_EXPECTED = 23

#: GT walkable ground coverage bins (measurement convention, identical to the legacy decision
#: range; not an algorithm parameter).
GT_BIN_NEAR_M, GT_BIN_M, GT_BINS = 1.5, 1.0, 5

#: Per-frame CSV columns.
PER_FRAME_FIELDS = [
    "condition", "arm", "session", "frame",
    "effective_command", "safety_state", "guidance", "reason",
    "coverage", "n_points", "walkable_offset_m", "walker_offset_m", "yaw_err_deg",
    "calib_ok", "calib_reason", "rotation_only", "depth_confidence",
    "path_status", "path_reason", "path_non_walkable_bins", "path_curb_bins",
    "path_unknown_bins", "path_walkable_swept_points",
    "obstacle_state", "obstacle_reason", "obstacle_components", "obstacle_in_path",
    "obstacle_nearest_m", "obstacle_clearance_m", "obstacle_lateral_extent_m",
    "obstacle_height_extent_m", "obstacle_temporal_streak",
    "legacy_hint", "legacy_reason", "legacy_hazard_bins", "legacy_non_walkable_bins",
    "legacy_unknown_bins", "legacy_walkable_swept_points",
    "gt_plane_ok", "gt_walk_pts_swept", "gt_walk_bins_ok", "gt_unknown_ground_pts_swept",
    "gt_nonwalk_obstacle_pts_swept",
    "gt_road_pts_swept", "gt_curb_pts_swept", "gt_danger_ground_pts",
    "gt_danger_obstacle_pts", "gt_danger_any_pts",
    "danger_ground", "danger_any", "gt_road_in_path", "gt_curb_in_path",
    "gt_occupancy_in_path", "gt_hazard_event", "confirmed_clear", "clear_weak",
    "pr15_road_path_band", "pr15_curb_path_band", "pr15_event", "pr15_23",
    "gt_hint_A_oracle",
    # system flags
    "forward_guidance", "warning", "is_stop", "is_unknown", "is_observe", "is_blocked",
    "is_clear",
    # miss / blocking flags per danger view
    "road_miss", "road_miss_straight", "road_turn_guidance_unevaluable",
    "danger_blocked_road",
    "curb_miss", "curb_miss_straight", "curb_turn_guidance_unevaluable",
    "danger_blocked_curb",
    "ground_miss", "ground_miss_straight", "ground_turn_guidance_unevaluable",
    "danger_blocked_ground",
    "occupancy_miss", "occupancy_miss_straight", "occupancy_turn_guidance_unevaluable",
    "danger_blocked_occupancy",
    "any_miss", "any_miss_straight", "any_turn_guidance_unevaluable", "danger_blocked_any",
    # clear-path false positives
    "true_false_stop", "unnecessary_observe", "unknown_on_confirmed_clear",
    "guidance_on_confirmed_clear", "false_stop_on_weak_clear",
]


# ---------------------------------------------------------------------------
# GT scoring (oracle geometry + raw SANPO ids; computed once per frame)
# ---------------------------------------------------------------------------
def empty_gt_reference() -> Dict[str, object]:
    return {
        "plane_ok": False, "up": None, "h": None,
        "gt_walk_pts_swept": 0, "gt_walk_bins_ok": 0, "gt_unknown_ground_pts_swept": 0,
        "gt_nonwalk_obstacle_pts_swept": 0,
        "gt_road_pts_swept": 0, "gt_curb_pts_swept": 0,
        "gt_danger_ground_pts": 0, "gt_danger_obstacle_pts": 0, "gt_danger_any_pts": 0,
        "danger_ground": False, "danger_any": False,
        "gt_road_in_path": False, "gt_curb_in_path": False, "gt_occupancy_in_path": False,
        "gt_hazard_event": False, "confirmed_clear": False, "clear_weak": False,
        "pr15_road_path_band": 0, "pr15_curb_path_band": 0, "pr15_event": False,
        "gt_hint_A_oracle": None,
    }


def gt_walk_bins_ok(fwd: np.ndarray, ground: np.ndarray, raw: np.ndarray,
                    swept: np.ndarray) -> Tuple[int, int]:
    """(#bins with >= MIN_EVENT_POINTS GT walkable points, #GT walkable points)."""
    walk = swept & ground & np.isin(raw, SAFE_IDS)
    walk_fwd = fwd[walk]
    bins_ok = 0
    for b in range(GT_BINS):
        lo = GT_BIN_NEAR_M + b * GT_BIN_M
        hi = lo + GT_BIN_M
        if int(np.count_nonzero((walk_fwd >= lo) & (walk_fwd <= hi))) >= MIN_EVENT_POINTS:
            bins_ok += 1
    return bins_ok, int(np.count_nonzero(walk))


def compute_gt_reference(gt, gt_cls: np.ndarray, session: SanpoSession, i: int,
                         pose: Pose2D, cfg: ShadowWalkConfig) -> Dict[str, object]:
    """All GT fields for one frame, from oracle geometry + raw SANPO ids only."""
    out = empty_gt_reference()
    plane = oracle_plane(session, i, gt)
    if plane is None:
        return out
    up, h = plane
    table = oracle_point_table(gt.depth_m, session.K, up, h, pose, cfg)
    raw = np.asarray(gt.seg)[table["vs"], table["us"]]
    height = table["height"]
    ground = np.abs(height) <= cfg.max_height_m
    swept = swept_body_mask(table["fwd"], table["lat"], cfg)

    walk_pts = int(gt_walk_count(raw, height, swept, cfg))
    bins_ok, _ = gt_walk_bins_ok(table["fwd"], ground, raw, swept)
    danger_ground_pts, danger_obstacle_pts = gt_danger_counts(raw, height, swept, cfg)
    gt_role = role_map_from_classes(gt_cls)[table["vs"], table["us"]]
    danger_any_pts = gt_danger_any_points(raw, height, swept, gt_role, cfg)
    ignore_ground = int(np.count_nonzero(swept & ground & (gt_role == ROLE_IGNORE)))
    # Any GT non-walkable 3D point in the swept body volume (incl. vegetation / tree / wall),
    # not only the danger-id classes: a confirmed clear path must have no GT obstruction at all.
    nonwalk_obstacle = int(np.count_nonzero(
        swept & (height > cfg.max_height_m) & (height <= OBSTACLE_MAX_HEIGHT_M)
        & (gt_role != ROLE_WALKABLE)))
    road_pts = int(np.count_nonzero(
        swept & ground & (raw == 1)
        & (table["fwd"] >= SW_CFG.near_m) & (table["fwd"] <= SW_CFG.far_m)))
    curb_pts = int(np.count_nonzero(
        swept & ground & (raw == 2) & (table["fwd"] >= 2.5) & (table["fwd"] <= SW_CFG.far_m)))
    pr15 = pr15_event_counts(raw, height, table["map_fwd"], table["map_lat"], cfg)

    danger_ground = danger_swept_flag(danger_ground_pts)
    danger_any = danger_swept_flag(danger_any_pts)
    road_in_path = road_pts >= MIN_EVENT_POINTS
    curb_in_path = curb_pts >= MIN_EVENT_POINTS
    occupancy_in_path = danger_obstacle_pts >= MIN_EVENT_POINTS
    # Confirmed clear path: the GT geometry is valid AND the swept path 1.5-6.0 m is fully
    # observed by walkable ground (all five bins) AND there is no GT danger of any kind AND no
    # unlabelled GT ground.  This is the only denominator on which a STOP is a true false-stop.
    confirmed_clear = bool(plane is not None and bins_ok == GT_BINS
                           and danger_any_pts == 0 and ignore_ground == 0
                           and nonwalk_obstacle == 0)
    clear_weak = bool(plane is not None and danger_any_pts == 0
                      and walk_pts >= MIN_EVENT_POINTS)

    gt_points = mask_to_map_points(walkable_mask_from_classes(gt_cls), gt.depth_m,
                                   session.K, up, h, pose, cfg)
    gt_hint = ShadowWalkTracker(CORRIDOR, cfg).update(gt_points, pose)

    out.update({
        "plane_ok": True, "up": up, "h": float(h),
        "gt_walk_pts_swept": walk_pts, "gt_walk_bins_ok": int(bins_ok),
        "gt_unknown_ground_pts_swept": ignore_ground,
        "gt_nonwalk_obstacle_pts_swept": nonwalk_obstacle,
        "gt_road_pts_swept": road_pts, "gt_curb_pts_swept": curb_pts,
        "gt_danger_ground_pts": int(danger_ground_pts),
        "gt_danger_obstacle_pts": int(danger_obstacle_pts),
        "gt_danger_any_pts": int(danger_any_pts),
        "danger_ground": bool(danger_ground), "danger_any": bool(danger_any),
        "gt_road_in_path": bool(road_in_path), "gt_curb_in_path": bool(curb_in_path),
        "gt_occupancy_in_path": bool(occupancy_in_path),
        "gt_hazard_event": bool(danger_ground or occupancy_in_path or road_in_path
                                or curb_in_path),
        "confirmed_clear": confirmed_clear, "clear_weak": clear_weak,
        "pr15_road_path_band": int(pr15["road_path_band"]),
        "pr15_curb_path_band": int(pr15["curb_path_band"]),
        "pr15_event": bool(pr15["event"]),
        "gt_hint_A_oracle": gt_hint.hint,
    })
    return out


# ---------------------------------------------------------------------------
# Per-frame row construction
# ---------------------------------------------------------------------------
def _base_row(condition: str, arm: str, session_id: str, frame: int, *,
              calib_ok: bool, calib_reason: str, yaw_err_deg: float,
              rotation_only: bool, depth_confidence: Optional[float],
              gt_ref: Dict[str, object]) -> Dict[str, object]:
    row: Dict[str, object] = {field: None for field in PER_FRAME_FIELDS}
    row.update({
        "condition": condition, "arm": arm, "session": session_id, "frame": int(frame),
        "calib_ok": bool(calib_ok), "calib_reason": str(calib_reason),
        "yaw_err_deg": round(float(yaw_err_deg), 2), "rotation_only": bool(rotation_only),
        "depth_confidence": (round(float(depth_confidence), 4)
                             if depth_confidence is not None else None),
        "legacy_hazard_bins": 0, "legacy_non_walkable_bins": 0, "legacy_unknown_bins": 0,
        "legacy_walkable_swept_points": 0,
    })
    for key in ("plane_ok", "gt_walk_pts_swept", "gt_walk_bins_ok",
                "gt_unknown_ground_pts_swept", "gt_nonwalk_obstacle_pts_swept",
                "gt_road_pts_swept", "gt_curb_pts_swept",
                "gt_danger_ground_pts", "gt_danger_obstacle_pts", "gt_danger_any_pts",
                "danger_ground", "danger_any", "gt_road_in_path", "gt_curb_in_path",
                "gt_occupancy_in_path", "gt_hazard_event", "confirmed_clear", "clear_weak",
                "pr15_road_path_band", "pr15_curb_path_band", "pr15_event",
                "gt_hint_A_oracle"):
        row[key] = gt_ref[key]
    row["gt_plane_ok"] = bool(gt_ref["plane_ok"])
    row["pr15_23"] = False
    return row


def _path_fields(row: Dict[str, object], path: PathState) -> None:
    row.update({
        "path_status": path.status, "path_reason": path.reason,
        "coverage": round(float(path.coverage), 3),
        "n_points": int(path.n_points),
        "walkable_offset_m": (round(float(path.walkable_offset_m), 3)
                              if math.isfinite(path.walkable_offset_m) else None),
        "walker_offset_m": (round(float(path.walker_offset_m), 3)
                            if math.isfinite(path.walker_offset_m) else None),
        "path_non_walkable_bins": int(path.non_walkable_bins),
        "path_curb_bins": int(path.curb_bins),
        "path_unknown_bins": int(path.unknown_bins),
        "path_walkable_swept_points": int(path.walkable_swept_points),
    })


def _obstacle_fields(row: Dict[str, object], risk: Optional[ObstacleRisk]) -> None:
    if risk is None:
        row.update({"obstacle_state": None, "obstacle_reason": None})
        return
    row.update({
        "obstacle_state": risk.state, "obstacle_reason": risk.reason,
        "obstacle_components": int(risk.n_components),
        "obstacle_in_path": int(risk.n_in_path),
        "obstacle_nearest_m": (round(float(risk.nearest_m), 3)
                               if risk.nearest_m is not None else None),
        "obstacle_clearance_m": (round(float(risk.clearance_m), 3)
                                 if risk.clearance_m is not None else None),
        "obstacle_lateral_extent_m": (round(float(risk.lateral_extent_m), 3)
                                      if risk.lateral_extent_m is not None else None),
        "obstacle_height_extent_m": (round(float(risk.height_extent_m), 3)
                                     if risk.height_extent_m is not None else None),
        "obstacle_temporal_streak": int(risk.temporal_streak),
    })


def make_row_a(condition: str, arm: str, session_id: str, frame: int, hint, *,
               calib_ok: bool, calib_reason: str, yaw_err_deg: float, rotation_only: bool,
               depth_confidence: Optional[float], gt_ref: Dict[str, object]) -> Dict[str, object]:
    """Arm A row: PR #17 hint mapped into the new state vocabulary for measurement."""
    row = _base_row(condition, arm, session_id, frame, calib_ok=calib_ok,
                    calib_reason=calib_reason, yaw_err_deg=yaw_err_deg,
                    rotation_only=rotation_only, depth_confidence=depth_confidence,
                    gt_ref=gt_ref)
    hint_kind = str(hint.hint)
    if hint_kind == STOP:
        state = BLOCKED
    elif hint_kind == HINT_UNKNOWN:
        state = UNKNOWN
    else:
        state = CLEAR
    row.update({
        "effective_command": hint_kind, "safety_state": state,
        "guidance": hint_kind if hint_kind in (STRAIGHT, LEFT, RIGHT) else None,
        "reason": hint.reason,
        "coverage": round(float(hint.coverage), 3), "n_points": int(hint.n_points),
        "walkable_offset_m": (round(float(hint.walkable_offset_m), 3)
                              if math.isfinite(float(hint.walkable_offset_m)) else None),
        "walker_offset_m": None,
        "path_status": state, "path_reason": hint.reason,
        "path_non_walkable_bins": int(getattr(hint, "non_walkable_bins", 0)),
        "path_curb_bins": 0,
        "path_unknown_bins": int(getattr(hint, "unknown_bins", 0)),
        "path_walkable_swept_points": int(getattr(hint, "walkable_swept_points", 0)),
        "legacy_hint": hint_kind, "legacy_reason": hint.reason,
        "legacy_hazard_bins": int(getattr(hint, "hazard_bins", 0)),
        "legacy_non_walkable_bins": int(getattr(hint, "non_walkable_bins", 0)),
        "legacy_unknown_bins": int(getattr(hint, "unknown_bins", 0)),
        "legacy_walkable_swept_points": int(getattr(hint, "walkable_swept_points", 0)),
    })
    return rsm.add_row_flags(row)


def make_row_decision(condition: str, arm: str, session_id: str, frame: int, decision, *,
                      path: PathState, obstacle: ObstacleRisk,
                      calib_ok: bool, calib_reason: str, yaw_err_deg: float,
                      rotation_only: bool, depth_confidence: Optional[float],
                      gt_ref: Dict[str, object]) -> Dict[str, object]:
    """Arm B/C row: Path Keeper + Obstacle Guardian + Safety Coordinator decision."""
    row = _base_row(condition, arm, session_id, frame, calib_ok=calib_ok,
                    calib_reason=calib_reason, yaw_err_deg=yaw_err_deg,
                    rotation_only=rotation_only, depth_confidence=depth_confidence,
                    gt_ref=gt_ref)
    row.update({
        "effective_command": decision.effective_command(),
        "safety_state": decision.state, "guidance": decision.guidance,
        "reason": decision.reason,
    })
    _path_fields(row, path)
    _obstacle_fields(row, obstacle)
    return rsm.add_row_flags(row)


def make_no_geometry_row(condition: str, arm: str, session_id: str, frame: int, *,
                         calib_ok: bool, calib_reason: str, yaw_err_deg: float,
                         rotation_only: bool, depth_confidence: Optional[float],
                         gt_ref: Dict[str, object]) -> Dict[str, object]:
    """All arms UNKNOWN when metric geometry is unavailable (never a pass)."""
    row = _base_row(condition, arm, session_id, frame, calib_ok=calib_ok,
                    calib_reason=calib_reason, yaw_err_deg=yaw_err_deg,
                    rotation_only=rotation_only, depth_confidence=depth_confidence,
                    gt_ref=gt_ref)
    row.update({
        "effective_command": HINT_UNKNOWN, "safety_state": UNKNOWN, "guidance": None,
        "reason": "no_metric_depth", "coverage": 0.0, "n_points": 0,
        "path_status": UNKNOWN, "path_reason": "no_metric_depth",
        "obstacle_state": UNKNOWN, "obstacle_reason": "no_metric_depth",
        "legacy_hint": HINT_UNKNOWN, "legacy_reason": "no_metric_depth",
    })
    return rsm.add_row_flags(row)


# ---------------------------------------------------------------------------
# Session evaluation
# ---------------------------------------------------------------------------
def evaluate_session(session: SanpoSession, model, device: str, depth_estimator,
                     cfg: Config, conditions: Sequence[str],
                     limit_per_session: int = 0) -> List[dict]:
    """All A/B/C rows for one session in the same frame order as PR #15/#17."""
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

    # One stateful object per (condition, arm) so temporal evidence is per session.
    trackers = {(condition, "A"): ShadowWalkTracker(CORRIDOR, ShadowWalkConfig(
        temporal_confirm_frames=0)) for condition in conditions}
    keepers = {condition: PathKeeper(CORRIDOR, SW_CFG) for condition in conditions}
    guardians = {(condition, arm): ObstacleGuardian(
        COARSE if arm == "B" else SPATIAL)
        for condition in conditions for arm in ("B", "C")}
    coordinator = SafetyCoordinator()
    calibrator = GroundCalibrator(cfg.ground)

    print(f"[{sid}] frames={len(indices)} "
          f"path={np.linalg.norm(np.diff(pos, axis=0), axis=1).sum():.1f} m "
          f"dt={dt:.3f}s rot_only_frames={int(rotation.sum())}", flush=True)
    rows: List[dict] = []
    t_start = time.time()
    need_production = COND_PRODUCTION in conditions
    for k, i in enumerate(indices):
        inp = session.inputs(i)
        gt = session.gt(i)
        gt_cls = gt_remap(gt.seg)

        v5_cls = q_dav2 = None
        if need_production:
            v5_cls = predict_v5(model, device, inp.bgr, gt.seg.shape)
            q_dav2 = depth_estimator.predict(inp.bgr)
        up_pose = up_from_gravity(inp.gravity_cam)

        tang_yaw = math.atan2(float(tangents[k][0]), float(tangents[k][1]))
        yaw_err_pose = float(wrap_pi(headings[k] - tang_yaw))
        pose = Pose2D(0.0, 0.0, yaw_err_pose)
        gt_ref = compute_gt_reference(gt, gt_cls, session, i, pose, SW_CFG)

        # Production geometry: existing ground calibrator with the pose-derived gravity proxy;
        # one calibration call per frame, shared by all arms (identical input condition).
        fit = None
        depth_prod = None
        up_prod = up_pose
        h_prod = float(cfg.ground.camera_height_m)
        if need_production:
            v5_walk = walkable_mask_from_classes(v5_cls)
            fit = calibrator.update(q_dav2, v5_walk, session.K, up_pose, up_source="attitude")
            if fit.ok:
                depth_prod = metric_depth(q_dav2, fit.a, fit.b, cfg.ground.max_depth_m)
                up_prod = fit.up if fit.up is not None else up_pose
        prod_conf = float(fit.confidence) if (fit is not None and fit.ok) else None

        for condition in conditions:
            if condition == COND_ANNOTATED:
                cls = gt_cls
                depth = gt.depth_m if gt_ref["plane_ok"] else None
                up = gt_ref["up"]
                h = gt_ref["h"]
                calib_ok = bool(gt_ref["plane_ok"])
                calib_reason = "" if gt_ref["plane_ok"] else "no_gt_plane"
                depth_conf = None
            else:
                depth = depth_prod
                up = up_prod
                h = h_prod
                calib_ok = bool(fit is not None and fit.ok)
                calib_reason = "" if calib_ok else (fit.reason if fit is not None else "fit")
                depth_conf = prod_conf

            if depth is None:
                for arm in ARMS:
                    rows.append(make_no_geometry_row(
                        condition, arm, sid, i, calib_ok=calib_ok,
                        calib_reason=calib_reason, yaw_err_deg=math.degrees(yaw_err_pose),
                        rotation_only=bool(rotation[k]), depth_confidence=depth_conf,
                        gt_ref=gt_ref))
                continue

            split = build_split_corridor_evidence(
                role_map_from_classes(cls), depth, session.K, up, h, pose,
                pixel_stride=SW_CFG.pixel_stride)
            observation = PathObservation.from_split(split)
            path_state = keepers[condition].update(observation, pose)

            hint_a = trackers[(condition, "A")].update_evidence(split.evidence, pose)
            rows.append(make_row_a(
                condition, "A", sid, i, hint_a, calib_ok=calib_ok,
                calib_reason=calib_reason, yaw_err_deg=math.degrees(yaw_err_pose),
                rotation_only=bool(rotation[k]), depth_confidence=depth_conf,
                gt_ref=gt_ref))
            for arm in ("B", "C"):
                risk = guardians[(condition, arm)].update(
                    split.obstacle, depth_confidence=depth_conf, calib_ok=calib_ok)
                decision = coordinator.decide(path_state, risk)
                rows.append(make_row_decision(
                    condition, arm, sid, i, decision, path=path_state, obstacle=risk,
                    calib_ok=calib_ok, calib_reason=calib_reason,
                    yaw_err_deg=math.degrees(yaw_err_pose),
                    rotation_only=bool(rotation[k]), depth_confidence=depth_conf,
                    gt_ref=gt_ref))
        if (k + 1) % 25 == 0 or k + 1 == len(indices):
            print(f"  {k + 1}/{len(indices)} frames, {time.time() - t_start:.0f}s", flush=True)
    return rows


# ---------------------------------------------------------------------------
# PR #15 fixed 23-frame set (reporting label only, never fed to the algorithm)
# ---------------------------------------------------------------------------
def pr15_23_frames(rows: Sequence[dict]) -> List[Tuple[str, int]]:
    """Frozen PR #15 set: annotated-condition frames where GT oracle-A was STRAIGHT on a
    PR #15 road/curb in-path event.  Recovered from GT fields, independent of the new arms."""
    frames = sorted({(str(r["session"]), int(r["frame"])) for r in rows
                     if r["condition"] == COND_ANNOTATED and r["arm"] == "A"
                     and bool(r["pr15_event"])
                     and r["gt_hint_A_oracle"] == STRAIGHT})
    return frames


# ---------------------------------------------------------------------------
# PR #17 reference cross-check (arm A == PR #17's evaluated fusion policy)
# ---------------------------------------------------------------------------
_REFERENCE_FIELDS = ("hint", "hazard_points", "hazard_bins", "non_walkable_points",
                     "non_walkable_bins", "unknown_points", "unknown_bins",
                     "walkable_swept_points", "danger_ground", "danger_any",
                     "pr15_event")


def _flag(value) -> bool:
    return str(value).strip().lower() in ("true", "1", "1.0")


def check_pr17_reference(rows: Sequence[dict], reference_path: str) -> Dict[str, object]:
    """Compare arm A (annotated) with the committed PR #17 per-frame reference (its arm B).

    PR #17's ``B`` row is exactly the policy this ticket calls arm A ("PR #17 as-is"), so this
    check proves arm A is the same policy on the same frames, not a re-implementation.
    """
    ref: Dict[Tuple[str, int], dict] = {}
    with open(reference_path, newline="", encoding="utf-8") as fh:
        for raw in csv.DictReader(fh):
            if raw["condition"] != COND_ANNOTATED or raw["arm"] != "B":
                continue
            ref[(raw["session"], int(raw["frame"]))] = raw
    ours = {(str(r["session"]), int(r["frame"])): r for r in rows
            if r["condition"] == COND_ANNOTATED and r["arm"] == "A"}
    mismatches: List[dict] = []
    compared = 0
    int_fields = ("hazard_bins", "non_walkable_bins", "unknown_bins",
                  "walkable_swept_points")
    bool_fields = ("danger_ground", "danger_any", "pr15_event")
    for key, raw in ref.items():
        row = ours.get(key)
        if row is None:
            mismatches.append({"session": key[0], "frame": key[1], "field": "missing_row"})
            continue
        compared += 1
        pairs = {"hint": row["legacy_hint"]}
        for field in int_fields:
            pairs[field] = row[f"legacy_{field}"]
        for field in bool_fields:
            pairs[field] = bool(row[field])
        for field, our_value in pairs.items():
            expected = raw[field]
            if field in bool_fields:
                same = _flag(expected) == bool(our_value)
            elif field == "hint":
                same = str(expected) == str(our_value)
            else:
                same = int(float(expected)) == int(our_value)
            if not same:
                mismatches.append({"session": key[0], "frame": key[1], "field": field,
                                   "expected": expected, "got": our_value})
    missing = [{"session": k[0], "frame": k[1]} for k in ref if k not in ours]
    return {"reference": os.path.abspath(reference_path), "compared_rows": compared,
            "reference_rows": len(ref), "our_rows": len(ours),
            "mismatches": len(mismatches) + len(missing),
            "mismatch_examples": (mismatches + missing)[:10],
            "verified": bool(mismatches or missing) is False and compared == len(ref)
            and len(ref) == len(ours)}


# ---------------------------------------------------------------------------
# Failure evidence images
# ---------------------------------------------------------------------------
_FAILURE_KINDS = (
    ("road_miss_straight", lambda r: r["road_miss_straight"]),
    ("curb_miss_straight", lambda r: r["curb_miss_straight"]),
    ("occupancy_miss_straight", lambda r: r["occupancy_miss_straight"]),
    ("true_false_stop", lambda r: r["true_false_stop"]),
    ("unnecessary_observe", lambda r: r["unnecessary_observe"]),
)


def select_failure_cases(rows: Sequence[dict], max_total: int) -> List[dict]:
    """Pick at most ``max_total`` distinct failing frames, one per kind first, arms B/C first.

    Production-condition failures are selected first (the deployment-like input chain), then
    annotated; at most two per (condition, kind) so the evidence is not dominated by one case.
    """
    conditions = sorted({str(r["condition"]) for r in rows},
                        key=lambda c: 0 if c == COND_PRODUCTION else 1)
    if not conditions or max_total <= 0:
        return []
    per_condition = max(1, max_total // len(conditions))
    selected: List[dict] = []
    seen = set()
    for condition in conditions:
        condition_rows = [r for r in rows if r["condition"] == condition]
        for kind, predicate in _FAILURE_KINDS:
            if len(selected) >= max_total:
                break
            per_arm = {"B": 0, "C": 0}
            for row in condition_rows:
                if len(selected) >= max_total:
                    break
                arm = str(row["arm"])
                if arm not in per_arm or per_arm[arm] >= 1:
                    continue
                if not predicate(row):
                    continue
                key = (str(row["session"]), int(row["frame"]))
                if key in seen:
                    continue
                seen.add(key)
                selected.append({"kind": kind, "condition": row["condition"],
                                 "arm": row["arm"], "session": key[0], "frame": key[1],
                                 "effective_command": row["effective_command"],
                                 "safety_state": row["safety_state"],
                                 "reason": row["reason"],
                                 "gt_road_pts": row["gt_road_pts_swept"],
                                 "gt_curb_pts": row["gt_curb_pts_swept"],
                                 "gt_occupancy_pts": row["gt_danger_obstacle_pts"],
                                 "confirmed_clear": bool(row["confirmed_clear"])})
                per_arm[arm] += 1
                if sum(per_arm.values()) >= 2:
                    break
        if len(selected) >= max_total:
            break
        if len([c for c in selected if c["condition"] == condition]) >= per_condition:
            continue
    return selected[:max_total]


def save_failure_images(failures: Sequence[dict], out_dir: str, data_root: str,
                        camera: str, max_width: int) -> None:
    """Write raw + annotated JPEG evidence for the selected failing frames."""
    if not failures:
        return
    try:
        import cv2
    except ImportError:  # pragma: no cover - cv2 is a hard dependency of the pc package
        print("WARN: cv2 unavailable, skipping failure images", file=sys.stderr)
        return
    fail_dir = os.path.join(out_dir, "failures")
    if os.path.isdir(fail_dir):
        # Keep the evidence directory in sync with this run: never leave stale failure images
        # from an earlier run that could be mistaken for the current measurement.
        import shutil
        shutil.rmtree(fail_dir)
    os.makedirs(fail_dir, exist_ok=True)
    sessions: Dict[str, SanpoSession] = {}
    index: List[dict] = []
    for n, case in enumerate(failures):
        sid = case["session"]
        if sid not in sessions:
            sessions[sid] = SanpoSession(os.path.join(data_root, sid), camera,
                                         max_width=max_width)
        bgr = sessions[sid].inputs(int(case["frame"])).bgr
        tag = f"f{n + 1:02d}_{case['kind']}_{case['arm']}_{sid[:8]}_{case['frame']:06d}"
        raw_path = os.path.join(fail_dir, f"{tag}_raw.jpg")
        over_path = os.path.join(fail_dir, f"{tag}_overlay.jpg")
        cv2.imwrite(raw_path, bgr)
        overlay = np.array(bgr, dtype=np.uint8, copy=True)
        banner = (
            f"{case['kind']} | arm {case['arm']} | {case['safety_state']} "
            f"-> {case['effective_command']} | {case['reason']}")
        details = (f"GT road={case['gt_road_pts']} curb={case['gt_curb_pts']} "
                   f"occupancy={case['gt_occupancy_pts']} "
                   f"confirmed_clear={case['confirmed_clear']}")
        cv2.rectangle(overlay, (0, 0), (overlay.shape[1], 52), (0, 0, 0), -1)
        cv2.putText(overlay, banner.encode("ascii", "replace").decode("ascii"), (8, 20),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1, cv2.LINE_AA)
        cv2.putText(overlay, details, (8, 42),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 255, 255), 1, cv2.LINE_AA)
        cv2.imwrite(over_path, overlay)
        index.append({**case, "raw": os.path.relpath(raw_path, out_dir),
                      "overlay": os.path.relpath(over_path, out_dir)})
    with open(os.path.join(fail_dir, "failures.json"), "w", encoding="utf-8") as fh:
        json.dump(index, fh, indent=1)
    print(f"wrote {len(index)} failure cases to {fail_dir}")


# ---------------------------------------------------------------------------
# Output writers
# ---------------------------------------------------------------------------
def write_per_frame_csv(path: str, rows: Sequence[dict]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=PER_FRAME_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def write_metrics_csv(path: str, metric_rows: Sequence[dict]) -> None:
    fields = ["condition", "arm", "scope", "metric", "numerator", "denominator", "rate",
              "value", "unit", "status", "low_sample", "note"]
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for row in metric_rows:
            writer.writerow(row)


def _summary_headline(summary: Dict[str, object], condition: str, arm: str) -> str:
    block = summary.get(f"{condition}/{arm}")
    if block is None:
        return f"{condition}/{arm}: n/a"
    m = block["metrics"]

    def cell(key):
        value = m[key]
        if value["status"] != "EVALUABLE":
            return f"{key}=NOT_EVALUABLE"
        if value.get("numerator") is not None:
            return f"{key}={value['numerator']}/{value['denominator']}"
        return f"{key}={value.get('value')}{value.get('unit', '')}"

    return (f"{condition}/{arm}: frames={block['frames']} "
            + " ".join(cell(k) for k in (
                "road_miss", "curb_miss", "occupancy_miss", "true_false_stop",
                "guidance_coverage_all", "unknown_share", "flips",
                "warning_within_horizon")))


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        description="A/B/C responsibility-split evaluation on SANPO-Real with rebuilt "
                    "measurement (Issue #18).",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    default_root = os.path.join(_PC_DIR, "data", "sanpo_real", "sanpo-real")
    ap.add_argument("--data-root", default=default_root)
    ap.add_argument("--sessions", nargs="*", default=None,
                    help="session ids (default: every session under --data-root)")
    ap.add_argument("--camera", default="camera_chest")
    ap.add_argument("--v5-checkpoint", default=None,
                    help="local segformer_sanpo_b0_v5.pt (required for production condition)")
    ap.add_argument("--out", default=os.path.join(_PC_DIR, "out", "responsibility_split"))
    ap.add_argument("--conditions", nargs="*", default=list(CONDITIONS),
                    choices=list(CONDITIONS))
    ap.add_argument("--max-width", type=int, default=640)
    ap.add_argument("--limit-per-session", type=int, default=0,
                    help="debug only: evaluate at most N frames per session (0 = all)")
    ap.add_argument("--save-failures", type=int, default=0,
                    help="write up to N failing-frame evidence images (0 = none)")
    ap.add_argument("--reference-per-frame", default=os.path.join(
        _PC_DIR, "..", "docs", "assets", "path_keeper_negative_evidence_20261106",
        "per_frame.csv"), help="committed PR #17 per-frame reference for the arm-A check")
    ap.add_argument("--skip-reference-check", action="store_true")
    ap.add_argument("--allow-reference-mismatch", action="store_true")
    ap.add_argument("--device", default=None)
    ap.add_argument("--seed", type=int, default=0)
    return ap.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    np.random.seed(int(args.seed))
    data_root = os.path.abspath(args.data_root)
    if not os.path.isdir(data_root):
        print(f"BLOCKED_MISSING_DATA: {data_root} does not exist", file=sys.stderr)
        return 2
    conditions = list(args.conditions)
    need_production = COND_PRODUCTION in conditions
    if need_production and not args.v5_checkpoint:
        print("BLOCKED_MISSING_CHECKPOINT: --v5-checkpoint is required for the production "
              "condition.", file=sys.stderr)
        return 2
    sessions = args.sessions or sorted(d for d in os.listdir(data_root)
                                       if os.path.isdir(os.path.join(data_root, d)))
    if not sessions:
        print(f"BLOCKED_MISSING_DATA: no sessions under {data_root}", file=sys.stderr)
        return 2

    cfg = Config()
    model = device = None
    depth_estimator = None
    if need_production:
        model, device = load_v5(args.v5_checkpoint, args.device)
        from blindnav_pc.depth import DepthEstimator
        depth_estimator = DepthEstimator(device=device)

    all_rows: List[dict] = []
    frame_counts: Dict[str, int] = {}
    dt_by_session: Dict[str, float] = {}
    fps_by_session: Dict[str, float] = {}
    t_start = time.time()
    for sid in sessions:
        session = SanpoSession(os.path.join(data_root, sid), args.camera,
                               max_width=args.max_width)
        cfg.ground.camera_height_m = float(session.camera_height_m)
        rows = evaluate_session(session, model, device, depth_estimator, cfg, conditions,
                                args.limit_per_session)
        if not rows:
            continue
        frame_counts[sid] = len(rows) // (len(conditions) * len(ARMS))
        indices = session.available_frames()
        if args.limit_per_session:
            indices = indices[:args.limit_per_session]
        dt_by_session[sid] = frame_dt_s(indices, session.fps)
        fps_by_session[sid] = float(session.fps)
        all_rows.extend(rows)
        print(f"  session done in {time.time() - t_start:.0f}s total", flush=True)

    if not all_rows:
        print("BLOCKED_NO_FRAMES: no frames were evaluated", file=sys.stderr)
        return 2
    total_frames = int(sum(frame_counts.values()))
    if args.limit_per_session == 0 and total_frames != EXPECTED_FRAMES:
        raise SystemExit(f"FRAME_SET_ASSERTION_FAILED: {total_frames} frames != frozen "
                         f"{EXPECTED_FRAMES}")

    # Fixed PR #15 23-frame reporting label (verified on a full run).
    pr15_23 = pr15_23_frames(all_rows)
    if args.limit_per_session == 0 and COND_ANNOTATED in conditions:
        if len(pr15_23) != PR15_23_EXPECTED:
            raise SystemExit(
                f"PR15_23_ASSERTION_FAILED: annotated PR #15 event & oracle-A STRAIGHT count "
                f"{len(pr15_23)} != expected {PR15_23_EXPECTED}")
    pr15_23_set = set(pr15_23)
    for row in all_rows:
        row["pr15_23"] = bool((row["session"], int(row["frame"])) in pr15_23_set)

    summary = rsm.summarize(all_rows, conditions, ARMS, dt_by_session)
    metric_rows = rsm.metrics_csv_rows(summary, conditions, ARMS)
    meta = {
        "tool": "pc/tools/responsibility_split_eval.py",
        "ticket": "Issue #18 P1 Path Keeper: separate corridor risk from obstacle risk",
        "data_root": data_root,
        "v5_checkpoint": os.path.abspath(args.v5_checkpoint) if args.v5_checkpoint else None,
        "frames": total_frames,
        "sessions": {sid: int(n) for sid, n in frame_counts.items()},
        "session_fps": fps_by_session,
        "session_dt_s": dt_by_session,
        "conditions": conditions,
        "arms": list(ARMS),
        "arm_description": ARM_DESCRIPTION,
        "max_width": int(args.max_width),
        "seed": int(args.seed),
        "min_event_points": int(MIN_EVENT_POINTS),
        "limit_per_session": int(args.limit_per_session),
        "corridor": {"name": CORRIDOR.name, "width_m": CORRIDOR.width_m,
                     "pose": "recorded VIO route; Pose2D(0,0,heading-route_tangent)"},
        "config": {
            "shadowwalk": {"near_m": SW_CFG.near_m, "far_m": SW_CFG.far_m,
                           "bin_m": SW_CFG.bin_m, "points_per_bin": SW_CFG.points_per_bin,
                           "min_coverage": SW_CFG.min_coverage,
                           "min_points": SW_CFG.min_points,
                           "swept_half_width_m": SW_CFG.swept_half_width_m,
                           "pixel_stride": SW_CFG.pixel_stride},
        },
        "gt_scoring": {
            "danger_ground_ids": list(DANGER_GROUND_IDS),
            "danger_obstacle_ids": list(DANGER_OBSTACLE_IDS),
            "danger_obstacle_height_m": [float(SW_CFG.max_height_m),
                                         float(OBSTACLE_MAX_HEIGHT_M)],
            "min_event_points": int(MIN_EVENT_POINTS),
            "confirmed_clear": "GT plane valid AND all five 1.5-6.0 m walkable bins observed "
                               "(>=5 points each) AND no GT danger of any kind AND no "
                               "unlabelled GT ground AND no GT non-walkable 3D point in the "
                               "body swept volume (vegetation/tree/wall included)",
            "pr15_23_expected": PR15_23_EXPECTED,
            "pr15_23_count": len(pr15_23),
            "pr15_23_verified": len(pr15_23) == PR15_23_EXPECTED,
            "pr15_23_scope": ("full_run" if (args.limit_per_session == 0
                                             and COND_ANNOTATED in conditions)
                              else "partial_run"),
        },
        "evidence_level": "DESKTOP_REAL_DATA",
        "synthetic_acceptance_data": False,
        "algorithm_changed_by_this_tool": False,
        "notes": "Measurement only. One Path Keeper / Obstacle Guardian / Coordinator pair per "
                 "(session, condition, arm) for temporal state. The PR #15 23-frame set is a "
                 "reporting label only and is never fed to the algorithm.",
    }

    out_dir = os.path.abspath(args.out)
    os.makedirs(out_dir, exist_ok=True)
    write_per_frame_csv(os.path.join(out_dir, "per_frame.csv"), all_rows)
    write_metrics_csv(os.path.join(out_dir, "metrics.csv"), metric_rows)
    with open(os.path.join(out_dir, "summary.json"), "w", encoding="utf-8") as fh:
        json.dump({"meta": meta, "arms": summary}, fh, indent=1)
    with open(os.path.join(out_dir, "summary.md"), "w", encoding="utf-8") as fh:
        fh.write(rsm.render_summary_md(summary, meta, conditions, ARMS))

    # Arm A must be the PR #17 policy as-is on the same frames.
    reference: Dict[str, object] = {"skipped": True}
    if not args.skip_reference_check and os.path.isfile(args.reference_per_frame):
        reference = check_pr17_reference(all_rows, args.reference_per_frame)
        with open(os.path.join(out_dir, "reference_check.json"), "w", encoding="utf-8") as fh:
            json.dump(reference, fh, indent=1)
        print(f"PR17_REFERENCE: compared={reference['compared_rows']} "
              f"mismatches={reference['mismatches']} verified={reference['verified']}")
        if not reference["verified"] and not args.allow_reference_mismatch:
            raise SystemExit(
                "PR17_REFERENCE_CHECK_FAILED: arm A rows differ from the committed PR #17 "
                f"reference: {reference['mismatch_examples']}")

    failures = select_failure_cases(all_rows, args.save_failures) if args.save_failures else []
    if failures:
        save_failure_images(failures, out_dir, data_root, args.camera, args.max_width)

    print("")
    print(f"FRAMES: {total_frames} sessions={frame_counts}")
    print(f"PR15_23: count={len(pr15_23)} expected={PR15_23_EXPECTED} "
          f"verified={len(pr15_23) == PR15_23_EXPECTED} scope={meta['gt_scoring']['pr15_23_scope']}")
    for condition in conditions:
        for arm in ARMS:
            print(_summary_headline(summary, condition, arm))
    print(f"\nwrote {out_dir}/per_frame.csv, metrics.csv, summary.json, summary.md "
          f"({len(all_rows)} rows, {time.time() - t_start:.0f}s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
