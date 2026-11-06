#!/usr/bin/env python3
"""Same-input SANPO A/B replay for the frozen spatial baseline and Mobilio candidate.

The tool keeps model/depth/pose products shared between both arms.  It is
intentionally a PC experiment: SANPO's recorded pose is named as an offline
VIO proxy and no Android or AR Foundation component is imported.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import sys
import time
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np

_TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
_PC_DIR = os.path.abspath(os.path.join(_TOOLS_DIR, ".."))
for _path in (_TOOLS_DIR, _PC_DIR):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from blindnav_pc.ground import metric_depth  # noqa: E402
from blindnav_pc.mobilio_fusion import MobilioDecision, fuse  # noqa: E402
from blindnav_pc.mobilio_occupancy import OccupancyRoute  # noqa: E402
from blindnav_pc.mobilio_surface import SurfaceRoute  # noqa: E402
from blindnav_pc.config import Config  # noqa: E402
from blindnav_pc.bev import depth_to_level_points  # noqa: E402
from blindnav_pc.corridor_evidence import build_split_corridor_evidence  # noqa: E402
from blindnav_pc.ego import wrap_pi  # noqa: E402
from blindnav_pc.ground import GroundCalibrator, up_from_gravity  # noqa: E402
from blindnav_pc.mobilio_occupancy import OccupancyGrid3D  # noqa: E402
from blindnav_pc.mobilio_surface import SurfaceGrid2D, SurfacePose  # noqa: E402
from blindnav_pc.obstacle_guardian import SPATIAL, ObstacleGuardian  # noqa: E402
from blindnav_pc.path_keeper import PathKeeper, PathObservation  # noqa: E402
from blindnav_pc.safety_coordinator import SafetyCoordinator  # noqa: E402
from blindnav_pc.safety_states import BLOCKED, CLEAR, OBSERVE, UNKNOWN  # noqa: E402
from blindnav_pc.shadowwalk import Pose2D  # noqa: E402

import responsibility_split_eval as rse  # noqa: E402
import responsibility_split_metrics as rsm  # noqa: E402
from corridor_evidence_eval import role_map_from_classes  # noqa: E402
from corridor_truth_eval import frame_dt_s, gt_remap, load_v5, predict_v5, route_frame, route_tangents  # noqa: E402
from eval.sanpo import SanpoSession  # noqa: E402
from mobilio_viz import render_replay_frame  # noqa: E402
from blindnav_pc.mobilio_reference import HarvardSurfaceReference  # noqa: E402
from blindnav_pc.mobilio_occupancy_reference import HarvardOccupancyReference  # noqa: E402
from blindnav_pc.mobilio_reference_adapters import update_reference_maps  # noqa: E402
from blindnav_pc.mobilio_surface import SurfaceRoute  # noqa: E402
from blindnav_pc.mobilio_occupancy import OccupancyRoute  # noqa: E402


BASELINE_ARM = "baseline_split_c"
CANDIDATE_ARM = "mobilio"
CONDITIONS = ("annotated", "production", "A_gt_gt", "B_v5_gt", "C_gt_dav2", "D_v5_dav2",
              "harvard_A", "harvard_D")
MOBILIO_EXTRA_FIELDS = [
    "depth_source", "pose_mode", "surface_state", "surface_distance_m",
    "surface_unknown_cells", "surface_fully_observed", "occupancy_state",
    "occupancy_clear_distance_m", "occupancy_blocking_cells",
    "occupancy_unknown_cells", "guidance_command",
    "risk_state", "observation_hint", "authorized_motion",
    "surface_angle_rad", "occupancy_angle_rad",
    "surface_failure", "surface_failure_cell", "surface_last_walkable_cell",
]
MOBILIO_FIELDS = list(dict.fromkeys(list(rse.PER_FRAME_FIELDS) + MOBILIO_EXTRA_FIELDS))


@dataclass(frozen=True)
class ResolvedDepth:
    depth_m: Optional[np.ndarray]
    up: Optional[np.ndarray]
    camera_height_m: float
    ok: bool
    source: str
    confidence: Optional[float]


def resolve_condition_depth(condition: str, q_dav2: Optional[np.ndarray], fit,
                            gt_depth: Optional[np.ndarray], camera_height_m: float = 1.35,
                            max_depth_m: float = 15.0) -> ResolvedDepth:
    """Resolve geometry for one condition without letting production read SANPO GT."""
    condition = str(condition)
    if condition == "annotated":
        if gt_depth is None:
            return ResolvedDepth(None, None, float(camera_height_m), False,
                                 "missing_sanpo_gt", None)
        return ResolvedDepth(np.asarray(gt_depth, dtype=np.float32),
                             getattr(fit, "up", None), float(camera_height_m), True,
                             "sanpo_gt", None)
    if condition != "production":
        raise ValueError(f"unknown condition {condition!r}")
    if q_dav2 is None or fit is None or not bool(getattr(fit, "ok", False)):
        return ResolvedDepth(None, getattr(fit, "up", None) if fit is not None else None,
                             float(camera_height_m), False, "no_metric_depth", None)
    depth = metric_depth(np.asarray(q_dav2), float(fit.a), float(fit.b), float(max_depth_m))
    return ResolvedDepth(depth, getattr(fit, "up", None), float(camera_height_m), True,
                         "dav2_calibrated", float(getattr(fit, "confidence", 0.0)))


def candidate_frame_keys(rows: Sequence[dict]) -> List[Tuple[str, int]]:
    """Return unique frame keys in deterministic order for arm-alignment checks."""
    return sorted({(str(row["session"]), int(row["frame"])) for row in rows})


def _effective_command(decision) -> str:
    state = str(decision.state)
    if state == "BLOCKED":
        return "STOP"
    if state in ("UNKNOWN", "OBSERVE"):
        return state
    return str(decision.guidance or "UNKNOWN")


def make_candidate_row(condition: str, arm: str, session: str, frame: int, *,
                       gt_ref: dict, decision, surface_route: Optional[SurfaceRoute] = None,
                       occupancy_route: Optional[OccupancyRoute] = None,
                       calib_ok: bool = True, depth_source: str = "unknown",
                       pose_mode: str = "sanpo_recorded_pose") -> dict:
    """Build the common row prefix; metric flags are added after GT fields are complete."""
    row = {field: None for field in MOBILIO_FIELDS}
    row.update({
        "condition": str(condition), "arm": str(arm), "session": str(session),
        "frame": int(frame), "effective_command": _effective_command(decision),
        "safety_state": str(decision.state), "guidance": decision.guidance,
        "reason": str(decision.reason), "calib_ok": bool(calib_ok),
        "depth_source": str(depth_source), "pose_mode": str(pose_mode),
        "surface_state": getattr(surface_route, "state", None),
        "surface_distance_m": getattr(surface_route, "observed_distance_m", None),
        "surface_unknown_cells": getattr(surface_route, "unknown_cells", None),
        "surface_fully_observed": getattr(surface_route, "fully_observed", None),
        "occupancy_state": getattr(occupancy_route, "state", None),
        "occupancy_clear_distance_m": getattr(occupancy_route, "clear_distance_m", None),
        "occupancy_blocking_cells": getattr(occupancy_route, "blocking_cells", None),
        "occupancy_unknown_cells": getattr(occupancy_route, "unknown_cells", None),
        "guidance_command": getattr(decision, "guidance", None),
        "risk_state": str(decision.state),
        "observation_hint": (getattr(decision, "guidance", None)
                              if str(decision.state) == OBSERVE else None),
        "authorized_motion": (getattr(decision, "guidance", None)
                               if str(decision.state) == CLEAR else None),
    })
    row.update({str(key): value for key, value in gt_ref.items()})
    return row


def _fill_common_defaults(row: dict, *, condition: str, arm: str, session: str,
                          frame: int, gt_ref: dict, calib_ok: bool,
                          depth_source: str, pose_mode: str) -> dict:
    row.update({
        "condition": str(condition), "arm": str(arm), "session": str(session),
        "frame": int(frame), "calib_ok": bool(calib_ok),
        "depth_source": str(depth_source), "pose_mode": str(pose_mode),
    })
    row.update({str(key): value for key, value in gt_ref.items()})
    row["gt_plane_ok"] = bool(gt_ref.get("plane_ok", False))
    for key in ("effective_command", "safety_state", "guidance", "reason"):
        row.setdefault(key, None)
    return row


def _blank_row(condition: str, arm: str, session: str, frame: int, *,
               gt_ref: dict, calib_ok: bool, depth_source: str,
               pose_mode: str = "sanpo_recorded_pose") -> dict:
    return _fill_common_defaults(
        {field: None for field in MOBILIO_FIELDS}, condition=condition, arm=arm,
        session=session, frame=frame, gt_ref=gt_ref, calib_ok=calib_ok,
        depth_source=depth_source, pose_mode=pose_mode)


def _finalize_row(row: dict) -> dict:
    """Add the shared metric flags after all prediction and GT fields are present."""
    return rsm.add_row_flags(row)


def _baseline_row(condition: str, session: str, frame: int, decision,
                  path, obstacle, *, gt_ref: dict, calib_ok: bool,
                  depth_source: str, pose_mode: str) -> dict:
    row = _blank_row(condition, BASELINE_ARM, session, frame, gt_ref=gt_ref,
                     calib_ok=calib_ok, depth_source=depth_source, pose_mode=pose_mode)
    row.update({
        "effective_command": decision.effective_command(),
        "safety_state": decision.state, "guidance": decision.guidance,
        "reason": decision.reason,
        "path_status": path.status, "path_reason": path.reason,
        "coverage": float(path.coverage), "n_points": int(path.n_points),
        "walkable_offset_m": float(path.walkable_offset_m),
        "walker_offset_m": float(path.walker_offset_m),
        "path_non_walkable_bins": int(path.non_walkable_bins),
        "path_curb_bins": int(path.curb_bins),
        "path_unknown_bins": int(path.unknown_bins),
        "path_walkable_swept_points": int(path.walkable_swept_points),
        "obstacle_state": obstacle.state, "obstacle_reason": obstacle.reason,
        "obstacle_components": int(obstacle.n_components),
        "obstacle_in_path": int(obstacle.n_in_path),
        "obstacle_nearest_m": obstacle.nearest_m,
        "obstacle_clearance_m": obstacle.clearance_m,
        "obstacle_lateral_extent_m": obstacle.lateral_extent_m,
        "obstacle_height_extent_m": obstacle.height_extent_m,
        "obstacle_temporal_streak": int(obstacle.temporal_streak),
        "risk_state": str(decision.state),
        "observation_hint": None,
        "authorized_motion": (decision.effective_command()
                               if str(decision.state) == CLEAR else None),
    })
    return _finalize_row(row)


def _candidate_row(condition: str, session: str, frame: int, decision: MobilioDecision,
                   surface: SurfaceRoute, occupancy: OccupancyRoute, *, gt_ref: dict,
                   calib_ok: bool, depth_source: str, pose_mode: str) -> dict:
    row = make_candidate_row(
        condition, CANDIDATE_ARM, session, frame, gt_ref=gt_ref, decision=decision,
        surface_route=surface, occupancy_route=occupancy, calib_ok=calib_ok,
        depth_source=depth_source, pose_mode=pose_mode)
    row.update({
        "path_status": surface.state, "path_reason": surface.reason,
        "coverage": min(1.0, float(surface.observed_distance_m) / 6.0),
        "n_points": int(surface.candidate_count),
        "path_unknown_bins": int(surface.unknown_cells),
        "path_non_walkable_bins": int(surface.terminated_role == 3),
        "path_curb_bins": int(surface.terminated_role == 2),
        "path_walkable_swept_points": int(surface.observed_distance_m > 0.0),
        "obstacle_state": occupancy.state, "obstacle_reason": occupancy.reason,
        "obstacle_in_path": int(occupancy.blocking_cells > 0),
        "obstacle_nearest_m": float(occupancy.clear_distance_m),
        "obstacle_clearance_m": float(occupancy.clear_distance_m),
        "obstacle_temporal_streak": int(occupancy.confirmed),
    })
    return _finalize_row(row)


def _no_geometry_rows(condition: str, session: str, frame: int, *, gt_ref: dict,
                      calib_ok: bool, calib_reason: str, depth_source: str,
                      pose_mode: str) -> List[dict]:
    surface = SurfaceRoute(UNKNOWN, None, 0.0, 0, 0, 0, False, calib_reason)
    occupancy = OccupancyRoute(UNKNOWN, 0.0, 0.0, 0, 0, False, calib_reason)
    decision = MobilioDecision(UNKNOWN, None, None, calib_reason, UNKNOWN, UNKNOWN)
    candidate = _candidate_row(condition, session, frame, decision, surface, occupancy,
                               gt_ref=gt_ref, calib_ok=calib_ok,
                               depth_source=depth_source, pose_mode=pose_mode)
    baseline = _blank_row(condition, BASELINE_ARM, session, frame, gt_ref=gt_ref,
                          calib_ok=calib_ok, depth_source=depth_source,
                          pose_mode=pose_mode)
    baseline.update({
        "effective_command": UNKNOWN, "safety_state": UNKNOWN, "guidance": None,
        "reason": calib_reason, "path_status": UNKNOWN, "path_reason": calib_reason,
        "obstacle_state": UNKNOWN, "obstacle_reason": calib_reason,
        "risk_state": UNKNOWN, "observation_hint": None, "authorized_motion": None,
    })
    return [_finalize_row(baseline), candidate]


def _visible_ground_cells(occupancy: OccupancyGrid3D, points_body: np.ndarray,
                          roles: np.ndarray, pose: SurfacePose, ground_band_m: float) -> List:
    points = np.asarray(points_body, dtype=np.float64).reshape(-1, 3)
    role_arr = np.asarray(roles).reshape(-1)
    if len(points) != len(role_arr):
        raise ValueError("body points and roles must be aligned")
    keep = ((np.abs(points[:, 2]) <= float(ground_band_m))
            & (role_arr == int(rse.ROLE_WALKABLE)))
    if not np.any(keep):
        return []
    world = occupancy.body_to_map(points[keep], pose)
    return [occupancy.world_to_cell(float(x), float(z), 0.0)
            for x, z, _height in world]


def evaluate_session(session: SanpoSession, model, device: Optional[str], depth_estimator,
                     cfg: Config, conditions: Sequence[str],
                     limit_per_session: int = 0,
                     visual_cases: Optional[List[dict]] = None,
                     visual_max: int = 0, video_writer=None) -> List[dict]:
    """Evaluate both arms on one session with shared frame/model/depth products."""
    sid = os.path.basename(session.dir.rstrip("/"))
    indices = session.available_frames()
    if limit_per_session and limit_per_session > 0:
        indices = indices[:int(limit_per_session)]
    if len(indices) < 1:
        return []

    positions, headings = route_frame(session, indices)
    tangents = route_tangents(positions)
    need_model = any(c in conditions for c in ("production", "B_v5_gt", "D_v5_dav2", "harvard_D"))
    need_dav2 = any(c in conditions for c in ("production", "C_gt_dav2", "D_v5_dav2", "harvard_D"))
    calibrator = GroundCalibrator(cfg.ground)
    keepers = {condition: PathKeeper(rse.CORRIDOR, rse.SW_CFG) for condition in conditions}
    guardians = {condition: ObstacleGuardian(SPATIAL) for condition in conditions}
    coordinators = {condition: SafetyCoordinator() for condition in conditions}
    surface_grids = {condition: SurfaceGrid2D() for condition in conditions}
    occupancy_grids = {condition: OccupancyGrid3D() for condition in conditions}
    harvard_surfaces = {condition: HarvardSurfaceReference() for condition in conditions
                        if condition in ("harvard_A", "harvard_D")}
    harvard_occupancies = {condition: HarvardOccupancyReference() for condition in conditions
                           if condition in ("harvard_A", "harvard_D")}
    rows: List[dict] = []

    for k, frame in enumerate(indices):
        inp = session.inputs(frame)
        gt = session.gt(frame)
        gt_cls = gt_remap(gt.seg)
        up_pose = up_from_gravity(inp.gravity_cam)

        tangent_yaw = math.atan2(float(tangents[k][0]), float(tangents[k][1]))
        yaw_err = float(wrap_pi(headings[k] - tangent_yaw))
        relative_pose = Pose2D(0.0, 0.0, yaw_err)
        map_pose = SurfacePose(float(positions[k, 0]), float(positions[k, 1]),
                               float(headings[k]))
        target_angle = float(wrap_pi(tangent_yaw - headings[k]))
        gt_ref = rse.compute_gt_reference(gt, gt_cls, session, frame, relative_pose, rse.SW_CFG)

        v5_cls = q_dav2 = fit = None
        if need_model or need_dav2:
            v5_cls = predict_v5(model, device, inp.bgr, gt.seg.shape)
        if need_dav2:
            q_dav2 = depth_estimator.predict(inp.bgr)
            calib_cls = v5_cls if v5_cls is not None else gt_cls
            calib_walk = rse.walkable_mask_from_classes(calib_cls)
            fit = calibrator.update(q_dav2, calib_walk, session.K, up_pose, up_source="attitude")

        for condition in conditions:
            if condition in ("annotated", "A_gt_gt", "B_v5_gt", "harvard_A"):
                resolved = resolve_condition_depth(
                    "annotated", None, type("Fit", (), {"up": gt_ref.get("up")})(),
                    gt.depth_m, camera_height_m=float(gt_ref.get("h") or session.camera_height_m))
                cls = v5_cls if condition == "B_v5_gt" else gt_cls
                up = gt_ref.get("up")
                camera_height = float(gt_ref.get("h") or session.camera_height_m)
                calib_reason = "" if resolved.ok else "no_gt_plane"
            elif condition in ("production", "C_gt_dav2", "D_v5_dav2", "harvard_D"):
                resolved = resolve_condition_depth(
                    "production", q_dav2, fit, gt.depth_m,
                    camera_height_m=float(session.camera_height_m),
                    max_depth_m=float(cfg.ground.max_depth_m))
                cls = gt_cls if condition == "C_gt_dav2" else v5_cls
                up = resolved.up if resolved.up is not None else up_pose
                camera_height = float(session.camera_height_m)
                calib_reason = "" if resolved.ok else "no_metric_depth"
            else:
                raise ValueError(f"unknown condition {condition!r}")

            if (not resolved.ok or resolved.depth_m is None or up is None or cls is None):
                pair = _no_geometry_rows(
                    condition, sid, frame, gt_ref=gt_ref, calib_ok=resolved.ok,
                    calib_reason=calib_reason, depth_source=resolved.source,
                    pose_mode="sanpo_recorded_pose")
                for row in pair:
                    row.update({"yaw_err_deg": math.degrees(yaw_err),
                                "rotation_only": False, "depth_confidence": resolved.confidence,
                                "calib_reason": calib_reason})
                if (visual_cases is not None and len(visual_cases) < visual_max
                        and k == 0):
                    for row in pair:
                        if len(visual_cases) >= visual_max:
                            break
                        visual_cases.append({"rgb": inp.bgr.copy(), "row": row,
                                             "surface": {"cells": []},
                                             "occupancy": {"cells": []}})
                if video_writer is not None:
                    video_writer.write(render_replay_frame({
                        "rgb": inp.bgr, "row": row, "baseline": pair[0],
                        "candidate": pair[1], "surface": {"cells": []},
                        "occupancy": {"cells": []}}))
                rows.extend(pair)
                continue

            depth = np.asarray(resolved.depth_m, dtype=np.float32)
            role_map = role_map_from_classes(cls)
            split = build_split_corridor_evidence(
                role_map, depth, session.K, np.asarray(up), camera_height, relative_pose,
                pixel_stride=rse.SW_CFG.pixel_stride)

            path = keepers[condition].update(PathObservation.from_split(split), relative_pose)
            obstacle = guardians[condition].update(
                split.obstacle, depth_confidence=resolved.confidence, calib_ok=resolved.ok)
            baseline_decision = coordinators[condition].decide(path, obstacle)
            baseline_row = _baseline_row(
                condition, sid, frame, baseline_decision, path, obstacle,
                gt_ref=gt_ref, calib_ok=resolved.ok, depth_source=resolved.source,
                pose_mode="sanpo_recorded_pose")
            baseline_row.update({"yaw_err_deg": math.degrees(yaw_err),
                                 "rotation_only": False,
                                 "depth_confidence": resolved.confidence,
                                 "calib_reason": calib_reason})
            rows.append(baseline_row)

            if condition in ("harvard_A", "harvard_D"):
                hsurface, hocc = harvard_surfaces[condition], harvard_occupancies[condition]
                update_reference_maps(hsurface, hocc, role_map, depth, session.K,
                                      np.asarray(up), camera_height, map_pose,
                                      pixel_stride=rse.SW_CFG.pixel_stride)
                h_angle_deg, _ = hsurface.raycast_grid(math.degrees(target_angle),
                                                       math.degrees(target_angle),
                                                       (map_pose.x_m, map_pose.z_m))
                hdiag = hsurface.raycast_diagnostics(math.degrees(target_angle),
                                                     (map_pose.x_m, map_pose.z_m))
                hfail = next((d for d in hdiag if d["distance_m"] <= hsurface.cfg.min_path_length),
                             hdiag[0] if hdiag else {})
                h_surface_angle = None if h_angle_deg is None else math.radians(h_angle_deg)
                h_surface_dist = 0.0 if h_angle_deg is None else hsurface.raycast_grid_walkable(
                    map_pose.x_m, map_pose.z_m, h_angle_deg)
                hsurface_route = SurfaceRoute(
                    "CLEAR" if h_angle_deg is not None and h_surface_dist > hsurface.cfg.min_path_length else "UNKNOWN",
                    h_surface_angle, h_surface_dist, 0, 0, 1 if h_angle_deg is not None else 0,
                    h_angle_deg is not None and h_surface_dist > hsurface.cfg.min_path_length,
                    "walkable" if h_angle_deg is not None else "no_candidate_ray")
                h_dir, h_closest = hocc.check_for_obstacle(
                    (map_pose.x_m, map_pose.z_m), math.degrees(headings[k]))
                hocc_route = OccupancyRoute(
                    "BLOCKED" if h_closest < hocc.cfg.distance_to_obstacle else "CLEAR",
                    math.radians(h_dir), h_closest, 1 if h_closest < hocc.cfg.distance_to_obstacle else 0,
                    0, h_closest < hocc.cfg.distance_to_obstacle,
                    "obstacle_ahead" if h_closest < hocc.cfg.distance_to_obstacle else "clear")
                h_state = "BLOCKED" if hocc_route.state == "BLOCKED" else (
                    "CLEAR" if hsurface_route.state == "CLEAR" else "UNKNOWN")
                h_guidance = ("STRAIGHT" if h_surface_angle is not None and abs(math.degrees(h_surface_angle)) < 15
                              else ("RIGHT" if h_surface_angle is not None and h_surface_angle > 0 else "LEFT")) 
                h_decision = MobilioDecision(h_state, h_surface_angle,
                                             h_guidance if h_state == "CLEAR" else None,
                                             "harvard_reference", hsurface_route.state, hocc_route.state)
                candidate_row = _candidate_row(
                    condition, sid, frame, h_decision, hsurface_route, hocc_route,
                    gt_ref=gt_ref, calib_ok=resolved.ok, depth_source=resolved.source,
                    pose_mode="sanpo_recorded_pose")
                candidate_row.update({"yaw_err_deg": math.degrees(yaw_err),
                                      "rotation_only": False,
                                      "depth_confidence": resolved.confidence,
                                      "calib_reason": calib_reason,
                                      "surface_angle_rad": h_surface_angle,
                                      "occupancy_angle_rad": math.radians(h_dir),
                                      "surface_failure": hfail.get("failure"),
                                      "surface_failure_cell": hfail.get("failure_cell"),
                                      "surface_last_walkable_cell": hfail.get("last_walkable_cell")})
                rows.append(candidate_row)
                continue

            surface_grid = surface_grids[condition]
            occupancy_grid = occupancy_grids[condition]
            surface_grid.update_from_frame(
                role_map, depth, session.K, np.asarray(up), camera_height, map_pose,
                int(frame), pixel_stride=rse.SW_CFG.pixel_stride)
            x_lat, z_fwd, height, (vs, us) = depth_to_level_points(
                depth, session.K, np.asarray(up), camera_height,
                stride=rse.SW_CFG.pixel_stride)
            points_body = np.column_stack([x_lat, z_fwd, height])
            point_roles = role_map[vs, us]
            visible = _visible_ground_cells(
                occupancy_grid, points_body, point_roles, map_pose,
                SurfaceGrid2D().cfg.ground_band_m)
            obstacle_mask = (
                (points_body[:, 2] >= occupancy_grid.cfg.min_obstacle_height_m)
                & (points_body[:, 2] <= occupancy_grid.cfg.person_height_m)
                & (point_roles != int(rse.ROLE_WALKABLE)))
            occupancy_grid.update_body_points(
                points_body[obstacle_mask], map_pose, int(frame), visible)
            surface_route = surface_grid.raycast_route(map_pose, target_angle)
            occupancy_route = occupancy_grid.raycast_clearance(map_pose, target_angle)
            candidate_decision = fuse(surface_route, occupancy_route)
            candidate_row = _candidate_row(
                condition, sid, frame, candidate_decision, surface_route, occupancy_route,
                gt_ref=gt_ref, calib_ok=resolved.ok, depth_source=resolved.source,
                pose_mode="sanpo_recorded_pose")
            candidate_row.update({"yaw_err_deg": math.degrees(yaw_err),
                                  "rotation_only": False,
                                  "depth_confidence": resolved.confidence,
                                  "calib_reason": calib_reason})
            candidate_row["surface_angle_rad"] = surface_route.angle_rad
            candidate_row["occupancy_angle_rad"] = occupancy_route.angle_rad
            surface_snapshot = surface_grid.snapshot(map_pose)
            occupancy_snapshot = occupancy_grid.snapshot(map_pose)
            if visual_cases is not None and len(visual_cases) < visual_max:
                failure = any(bool(candidate_row.get(flag, False)) for flag in (
                    "any_miss", "occupancy_miss", "ground_miss",
                    "true_false_stop", "unknown_on_confirmed_clear"))
                if k == 0 or failure:
                    for row in (baseline_row, candidate_row):
                        if len(visual_cases) >= visual_max:
                            break
                        visual_cases.append({"rgb": inp.bgr.copy(), "row": row,
                                             "surface": surface_snapshot,
                                             "occupancy": occupancy_snapshot})
            if video_writer is not None:
                video_writer.write(render_replay_frame({
                    "rgb": inp.bgr, "row": candidate_row, "baseline": baseline_row,
                    "candidate": candidate_row, "surface": surface_snapshot,
                    "occupancy": occupancy_snapshot}))
            rows.append(candidate_row)
    return rows


def write_per_frame_csv(path: str, rows: Sequence[dict]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=MOBILIO_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def write_metrics_csv(path: str, metric_rows: Sequence[dict]) -> None:
    fields = ["condition", "arm", "scope", "metric", "numerator", "denominator",
              "rate", "value", "unit", "status", "low_sample", "note"]
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for row in metric_rows:
            writer.writerow(row)


def _metric_text(value: Optional[dict]) -> str:
    if not value or value.get("status") == "NOT_EVALUABLE":
        return "NOT_EVALUABLE"
    if value.get("numerator") is not None and value.get("denominator") is not None:
        den = int(value["denominator"])
        rate = value.get("rate")
        return f"{value['numerator']}/{den} ({100.0 * float(rate):.1f}%)" if den else "0/0"
    return f"{value.get('value')} {value.get('unit', '')}".strip()


def render_mobilio_summary_md(summary: dict, meta: dict,
                              conditions: Sequence[str], arms: Sequence[str]) -> str:
    lines = [
        "# Mobilio-inspired PC SANPO A/B replay",
        "",
        f"Frames: {meta.get('frames')} across {len(meta.get('sessions', {}))} sessions.",
        "",
        "Evidence level: **DESKTOP_REAL_DATA** (offline SANPO-Real analysis). "
        "The pose is a SANPO recorded pose used as an offline VIO proxy; this is not a "
        "phone-device or LiDAR validation.",
        "",
        f"Baseline: `{BASELINE_ARM}` = frozen c000 spatial Path Keeper + Obstacle Guardian.",
        f"Candidate: `{CANDIDATE_ARM}` = persistent SurfaceGrid2D + OccupancyGrid3D + monotone fusion.",
        "",
    ]
    headline = [
        "road_miss", "curb_miss", "ground_miss", "occupancy_miss", "any_miss",
        "true_false_stop", "guidance_coverage_all", "unknown_share", "flips",
        "warning_within_horizon",
    ]
    for condition in conditions:
        lines.extend([f"## Condition `{condition}`", "", "| metric | "
                      + " | ".join(arms) + " |", "|---|" + "---:|" * len(arms)])
        for metric in headline:
            cells = []
            for arm in arms:
                block = summary.get(f"{condition}/{arm}")
                cells.append(_metric_text(block["metrics"].get(metric)) if block else "n/a")
            lines.append(f"| `{metric}` | " + " | ".join(cells) + " |")
        lines.append("")
        block = summary.get(f"{condition}/{arms[0]}")
        if block:
            lines.append("GT/frame counts: " + ", ".join(
                f"{k}={v}" for k, v in block.get("hazard_views", {}).items()))
            lines.append("")
    lines.extend([
        "## Interpretation rules", "",
        "* Production decisions use v5 + calibrated DAV2 only; GT is used after the decision for scoring.",
        "* Annotated is a geometry/semantic attribution condition, not a deployment result.",
        "* `UNKNOWN` and missing surface/occupancy observations are not counted as a fully observed clear path.",
        "* Any improvement or regression is specific to these SANPO sessions and does not reproduce Mobilio's paper numbers.",
        "",
    ])
    return "\n".join(lines)


def _json_default(value):
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return float(value)
    if isinstance(value, np.ndarray):
        return value.tolist()
    raise TypeError(f"cannot serialize {type(value).__name__}")


def _select_failure_rows(rows: Sequence[dict], limit: int) -> List[dict]:
    if limit <= 0:
        return []
    priority = ("any_miss", "occupancy_miss", "ground_miss", "true_false_stop",
                "unknown_on_confirmed_clear")
    selected: List[dict] = []
    seen = set()
    for flag in priority:
        for row in rows:
            if len(selected) >= limit:
                return selected
            key = (row["condition"], row["arm"], row["session"], int(row["frame"]))
            if key in seen or not bool(row.get(flag, False)):
                continue
            seen.add(key)
            selected.append({"condition": row["condition"], "arm": row["arm"],
                             "session": row["session"], "frame": int(row["frame"]),
                             "kind": flag, "effective_command": row.get("effective_command"),
                             "safety_state": row.get("safety_state"),
                             "reason": row.get("reason")})
    return selected


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Same-input SANPO A/B replay: frozen split-C baseline vs Mobilio-inspired maps.")
    default_root = os.path.join(_PC_DIR, "data", "sanpo_real", "sanpo-real")
    parser.add_argument("--data-root", default=default_root)
    parser.add_argument("--sessions", nargs="*", default=None)
    parser.add_argument("--camera", default="camera_chest")
    parser.add_argument("--v5-checkpoint", default=None)
    parser.add_argument("--out", default=os.path.join(_PC_DIR, "out", "mobilio_sanpo_ab_20261106"))
    parser.add_argument("--conditions", nargs="*", choices=list(CONDITIONS),
                        default=list(CONDITIONS))
    parser.add_argument("--max-width", type=int, default=640)
    parser.add_argument("--limit-per-session", type=int, default=0)
    parser.add_argument("--save-failures", type=int, default=0)
    parser.add_argument("--device", default=None)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--video-out", default=None,
                        help="optional MP4 path for a frame-aligned SANPO A/B replay")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    np.random.seed(int(args.seed))
    data_root = os.path.abspath(args.data_root)
    if not os.path.isdir(data_root):
        print(f"BLOCKED_MISSING_DATA: {data_root}", file=sys.stderr)
        return 2
    conditions = list(args.conditions)
    needs_checkpoint = any(c in conditions for c in ("production", "B_v5_gt", "C_gt_dav2", "D_v5_dav2", "harvard_D"))
    if needs_checkpoint and not args.v5_checkpoint:
        print("BLOCKED_MISSING_CHECKPOINT: --v5-checkpoint is required for production", file=sys.stderr)
        return 2
    sessions = args.sessions or sorted(
        name for name in os.listdir(data_root) if os.path.isdir(os.path.join(data_root, name)))
    if not sessions:
        print(f"BLOCKED_MISSING_DATA: no sessions under {data_root}", file=sys.stderr)
        return 2

    model = device = depth_estimator = None
    if needs_checkpoint:
        model, device = load_v5(args.v5_checkpoint, args.device)
        from blindnav_pc.depth import DepthEstimator
        depth_estimator = DepthEstimator(device=device)

    cfg = Config()
    all_rows: List[dict] = []
    frame_counts: Dict[str, int] = {}
    dt_by_session: Dict[str, float] = {}
    fps_by_session: Dict[str, float] = {}
    visual_cases: List[dict] = []
    visual_max = max(2, int(args.save_failures) * 2)
    video_writer = None
    started = time.time()
    for sid in sessions:
        session = SanpoSession(os.path.join(data_root, sid), args.camera,
                               max_width=args.max_width)
        if args.video_out and video_writer is None:
            import cv2
            video_path = os.path.abspath(args.video_out)
            os.makedirs(os.path.dirname(video_path), exist_ok=True)
            video_writer = cv2.VideoWriter(
                video_path, cv2.VideoWriter_fourcc(*"mp4v"), float(session.fps), (720, 480))
            if not video_writer.isOpened():
                raise SystemExit(f"VIDEO_OPEN_FAILED: {video_path}")
        cfg.ground.camera_height_m = float(session.camera_height_m)
        session_rows = evaluate_session(session, model, device, depth_estimator, cfg,
                                         conditions, args.limit_per_session,
                                         visual_cases=visual_cases, visual_max=visual_max,
                                         video_writer=video_writer)
        if not session_rows:
            continue
        all_rows.extend(session_rows)
        unique_keys = candidate_frame_keys(session_rows)
        expected_per_frame = len(conditions) * 2
        if len(session_rows) != len(unique_keys) * expected_per_frame:
            raise SystemExit(f"FRAME_ALIGNMENT_FAILED: {sid} rows={len(session_rows)} "
                             f"keys={len(unique_keys)} arms/conditions={expected_per_frame}")
        frame_counts[sid] = len(unique_keys)
        indices = session.available_frames()
        if args.limit_per_session:
            indices = indices[:args.limit_per_session]
        dt_by_session[sid] = frame_dt_s(indices, session.fps)
        fps_by_session[sid] = float(session.fps)
        print(f"[{sid}] frames={len(unique_keys)} elapsed={time.time() - started:.0f}s", flush=True)

    if not all_rows:
        print("BLOCKED_NO_FRAMES: no frames were evaluated", file=sys.stderr)
        return 2
    for condition in conditions:
        condition_rows = [row for row in all_rows if row["condition"] == condition]
        baseline_keys = candidate_frame_keys([r for r in condition_rows if r["arm"] == BASELINE_ARM])
        candidate_keys = candidate_frame_keys([r for r in condition_rows if r["arm"] == CANDIDATE_ARM])
        if baseline_keys != candidate_keys:
            raise SystemExit(f"FRAME_KEY_MISMATCH: {condition}")

    total_frames = sum(frame_counts.values())
    if args.limit_per_session == 0 and total_frames != int(rse.EXPECTED_FRAMES):
        raise SystemExit(f"FRAME_SET_ASSERTION_FAILED: {total_frames} frames != {rse.EXPECTED_FRAMES}")

    summary = rsm.summarize(all_rows, conditions, (BASELINE_ARM, CANDIDATE_ARM), dt_by_session)
    metric_rows = rsm.metrics_csv_rows(summary, conditions, (BASELINE_ARM, CANDIDATE_ARM))
    meta = {
        "tool": "pc/tools/mobilio_sanpo_ab.py",
        "frames": int(total_frames), "sessions": {k: int(v) for k, v in frame_counts.items()},
        "session_fps": fps_by_session, "session_dt_s": dt_by_session,
        "conditions": conditions, "arms": [BASELINE_ARM, CANDIDATE_ARM],
        "arm_description": {
            BASELINE_ARM: "frozen c000 spatial Path Keeper + Obstacle Guardian + Safety Coordinator",
            CANDIDATE_ARM: "persistent 0.20 m SurfaceGrid2D + 0.10 m OccupancyGrid3D + monotone fusion",
        },
        "data_root": data_root,
        "v5_checkpoint": os.path.abspath(args.v5_checkpoint) if args.v5_checkpoint else None,
        "max_width": int(args.max_width), "limit_per_session": int(args.limit_per_session),
        "seed": int(args.seed), "pose_mode": "sanpo_recorded_pose_offline_vio_proxy",
        "depth_sources": {"production": "dav2_calibrated", "annotated": "sanpo_gt"},
        "grid": {"surface_cell_m": 0.20, "surface_extent_m": 15.0,
                 "blind_zone_m": 2.2, "occupancy_cell_m": 0.10,
                 "occupancy_horizon_m": 4.0, "occupancy_min_height_m": 0.15},
        "evidence_level": "DESKTOP_REAL_DATA",
        "production_gt_isolation": "GT is used only after production decisions for scoring",
    }
    out_dir = os.path.abspath(args.out)
    os.makedirs(out_dir, exist_ok=True)
    write_per_frame_csv(os.path.join(out_dir, "per_frame.csv"), all_rows)
    write_metrics_csv(os.path.join(out_dir, "metrics.csv"), metric_rows)
    with open(os.path.join(out_dir, "summary.json"), "w", encoding="utf-8") as fh:
        json.dump({"meta": meta, "summary": summary}, fh, indent=2, default=_json_default)
    with open(os.path.join(out_dir, "run_metadata.json"), "w", encoding="utf-8") as fh:
        json.dump(meta, fh, indent=2, default=_json_default)
    with open(os.path.join(out_dir, "summary.md"), "w", encoding="utf-8") as fh:
        fh.write(render_mobilio_summary_md(summary, meta, conditions,
                                           (BASELINE_ARM, CANDIDATE_ARM)))
    from mobilio_viz import render_contact_sheet
    if visual_cases:
        render_contact_sheet(visual_cases, os.path.join(out_dir, "contact_sheet.png"))
    if video_writer is not None:
        video_writer.release()
    failures = _select_failure_rows(all_rows, int(args.save_failures))
    with open(os.path.join(out_dir, "failure_manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(failures, fh, indent=2, default=_json_default)
    print(f"wrote {len(all_rows)} rows to {out_dir}", flush=True)
    return 0
if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
