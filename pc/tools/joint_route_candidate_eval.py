#!/usr/bin/env python3
"""Compare unchanged Mobilio queries with an isolated joint-route candidate.

These are NEW, explicitly oracle-assisted public-data conditions, not the historical
production/C_gt_dav2 experiment. Both arms receive identical frame products but own
independent surface/occupancy maps. No production module is patched at runtime.

public_gt_reference: SANPO semantic labels, depth and fitted ground plane.
public_gt_calibration_dav2: SANPO semantic labels (also calibration mask), actual
DAV2-Small inference and existing GroundCalibrator; no GT depth enters this arm.
Recorded pose and recorded-trajectory target direction are offline inputs in both.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import subprocess
import sys
import time
import traceback
from collections import Counter
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

PC_DIR = Path(__file__).resolve().parents[1]
REPO_DIR = PC_DIR.parent
sys.path.insert(0, str(PC_DIR))
sys.path.insert(0, str(PC_DIR / "tools"))

import mobilio_sanpo_ab as ab
import responsibility_split_eval as rse
from blindnav_pc.bev import depth_to_level_points
from blindnav_pc.config import Config
from blindnav_pc.ego import wrap_pi
from blindnav_pc.ground import GroundCalibrator, up_from_gravity
from blindnav_pc.mobilio_fusion import MobilioDecision, fuse
from blindnav_pc.mobilio_occupancy import OccupancyGrid3D, OccupancyRoute
from blindnav_pc.mobilio_surface import SurfaceGrid2D, SurfacePose, SurfaceRoute
from blindnav_pc.shadowwalk import Pose2D
from corridor_evidence_eval import role_map_from_classes
from corridor_truth_eval import gt_remap, route_frame, route_tangents
from eval.sanpo import SanpoSession

CONDITIONS = ("public_gt_reference", "public_gt_calibration_dav2")
ARMS = ("original_mobilio", "joint_route_candidate")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def json_default(value):
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, Path):
        return str(value)
    raise TypeError(type(value).__name__)


def write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False,
                               default=json_default, allow_nan=False) + "\n",
                    encoding="utf-8")


def json_hash(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=json_default,
                                     allow_nan=False).encode()).hexdigest()


def array_identity(value):
    if value is None:
        return None
    array = np.ascontiguousarray(value)
    return {"dtype": array.dtype.str, "shape": list(array.shape),
            "sha256": hashlib.sha256(array.tobytes()).hexdigest()}


def git_output(*args) -> str:
    return subprocess.check_output(["git", *args], cwd=REPO_DIR, text=True).strip()


def update_maps(surface, occupancy, roles, depth, K, up, height, pose, frame):
    """Same update contract as mobilio_sanpo_ab.evaluate_session, without queries."""
    stride = rse.SW_CFG.pixel_stride
    surface.update_from_frame(roles, depth, K, up, height, pose, frame,
                              pixel_stride=stride)
    x, z, h, (vs, us) = depth_to_level_points(depth, K, up, height, stride=stride)
    points = np.column_stack([x, z, h])
    point_roles = roles[vs, us]
    visible = ab._visible_ground_cells(occupancy, points, point_roles, pose,
                                       surface.cfg.ground_band_m)
    keep = ((points[:, 2] >= occupancy.cfg.min_obstacle_height_m)
            & (points[:, 2] <= occupancy.cfg.person_height_m)
            & (point_roles != int(rse.ROLE_WALKABLE)))
    occupancy.update_body_points(points[keep], pose, frame, visible)


def map_digest(surface, occupancy) -> str:
    """Hash all query/update state, including evidence and visibility history."""
    cell_key = lambda key: (key.ix, key.iz, key.ih)
    return json_hash({
        "surface": {"cfg": asdict(surface.cfg), "last_frame": surface._last_frame,
            "cells": [(key, sorted(cell.scores.items()), cell.last_frame)
                      for key, cell in sorted(surface._cells.items())]},
        "occupancy": {"cfg": asdict(occupancy.cfg), "last_frame": occupancy._last_frame,
            "cells": [(cell_key(key), cell.evidence, cell.last_frame, list(cell.hit_frames),
                       cell.hit_frames.maxlen) for key, cell in sorted(occupancy._cells.items())],
            "visible_free": [(cell_key(key), frame) for key, frame in
                             sorted(occupancy._visible_free.items())],
            "visible_free_xy": sorted(occupancy._visible_free_xy),
            "xy_index": [(key, sorted(cell_key(cell) for cell in cells))
                         for key, cells in sorted(occupancy._xy_index.items())],
            "frame_history": list(occupancy._frame_history),
            "frame_history_maxlen": occupancy._frame_history.maxlen}})


def unavailable(reason):
    surface = SurfaceRoute("UNKNOWN", None, 0.0, 0, 0, 0, False, reason)
    occupancy = OccupancyRoute("UNKNOWN", 0.0, 0.0, 0, 0, False, reason)
    decision = MobilioDecision("UNKNOWN", None, None, reason, "UNKNOWN", "UNKNOWN")
    return decision, surface, occupancy


def result_row(condition, arm, sid, frame, result, gt_ref, resolved, extra):
    decision, surface, occupancy = result
    row = ab._candidate_row(condition, sid, frame, decision, surface, occupancy,
                            gt_ref=gt_ref, calib_ok=resolved.ok,
                            depth_source=resolved.source, pose_mode="sanpo_recorded_pose")
    row.update(arm=arm, selected_angle_rad=decision.angle_rad,
               surface_angle_rad=surface.angle_rad, occupancy_angle_rad=occupancy.angle_rad,
               **extra)
    return row


def paired_row(old, new):
    row = {key: old.get(key) for key in (
        "condition", "session", "frame", "relative_time_ms", "pose_tracking_ok",
        "calib_ok", "calib_reason", "depth_source", "shared_input_sha256",
        "effective_camera_height_m", "calib_fit_reason", "calib_up_source",
        "calib_attitude_visual_deg", "calib_n_ground_px", "calib_confidence",
        "confirmed_clear", "danger_any", "gt_road_in_path", "gt_curb_in_path",
        "gt_occupancy_in_path", "target_angle_rad")}
    for prefix, source in (("old", old), ("new", new)):
        for key in ("safety_state", "effective_command", "guidance", "authorized_motion",
                    "selected_angle_rad", "surface_angle_rad", "occupancy_angle_rad",
                    "surface_distance_m", "occupancy_clear_distance_m", "reason"):
            row[f"{prefix}_{key}"] = source.get(key)
    row["candidate_map_contract_authorized"] = new.get("map_contract_authorized", False)
    row["full_volume_visibility_certified"] = False
    row["changed"] = any(old.get(k) != new.get(k) for k in
                          ("safety_state", "guidance", "selected_angle_rad"))
    return row


def rates(rows):
    n = len(rows)
    clear = [r for r in rows if r.get("confirmed_clear")]
    danger = [r for r in rows if r.get("danger_any")]
    authorized = lambda r: r.get("authorized_motion") in ("LEFT", "RIGHT", "STRAIGHT")
    def rate(subset):
        count = sum(authorized(r) for r in subset)
        return {"numerator": count, "denominator": len(subset),
                "rate": count / len(subset) if subset else None}
    ready = [r for r in rows if r.get("pose_tracking_ok")]
    return {"frames": n, "pose_ready_frames": len(ready), "pose_not_ready_frames": n - len(ready),
            "states": dict(Counter(r["safety_state"] for r in rows)),
            "calibrated_frames": sum(bool(r["calib_ok"]) for r in rows),
            "authorized_all": rate(rows), "authorized_on_pose_ready": rate(ready),
            "authorized_on_clear_proxy": rate(clear),
            "authorized_on_danger_proxy": rate(danger),
            "observation_hints": sum(bool(r.get("observation_hint")) for r in rows),
            "reason_counts": dict(Counter(r["reason"] for r in rows))}


class SharedDepth:
    """One real DAV2 inference per image, optionally cached with source hashes."""
    def __init__(self, args, model_inventory):
        self.args = args
        self.model_inventory = model_inventory
        self.estimator = None
        self.calls = 0
        self.cache_hits = 0
        self.model_load_seconds = 0.0
        self.inference_seconds = []
        self.records = []

    def predict(self, session, frame, bgr):
        image_file = Path(session.ldir) / "video_frames" / f"{frame:06d}.png"
        identity = {"image_sha256": sha256_file(image_file), "image_hw": list(bgr.shape[:2]),
                    "model_inventory": self.model_inventory,
                    "input_size": self.args.depth_input_size,
                    "depth_wrapper_sha256": sha256_file(PC_DIR / "blindnav_pc/depth.py")}
        signature = json_hash(identity)
        cache_file = None
        if self.args.inference_cache:
            cache_file = Path(self.args.inference_cache) / f"{signature}.npz"
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            if cache_file.exists():
                with np.load(cache_file, allow_pickle=False) as cached:
                    if str(cached["signature"].item()) != signature:
                        raise ValueError(f"cache signature mismatch: {cache_file}")
                    q = cached["q_dav2"].copy()
                if q.shape != bgr.shape[:2] or not np.isfinite(q).all():
                    raise ValueError(f"invalid cached DAV2 output: {cache_file}")
                self.cache_hits += 1
                self.records.append({"session": Path(session.dir).name, "frame": int(frame),
                                     "signature": signature, "source": "verified_cached_real_dav2",
                                     "q_dav2": array_identity(q), "cache_file_sha256": sha256_file(cache_file)})
                return q, signature, "verified_cached_real_dav2", 0.0
        if self.estimator is None:
            import torch
            from blindnav_pc.depth import DepthEstimator
            torch.set_num_threads(self.args.torch_threads)
            loaded_at = time.perf_counter()
            self.estimator = DepthEstimator(device=self.args.device,
                input_size=self.args.depth_input_size, local_snapshot=self.args.depth_snapshot,
                revision=self.args.depth_revision)
            self.model_load_seconds = time.perf_counter() - loaded_at
        predicted_at = time.perf_counter()
        q = self.estimator.predict(bgr)
        inference_seconds = time.perf_counter() - predicted_at
        if q.shape != bgr.shape[:2] or not np.isfinite(q).all():
            raise ValueError("DAV2 returned invalid dimensions or nonfinite output")
        self.calls += 1
        self.inference_seconds.append(inference_seconds)
        self.records.append({"session": Path(session.dir).name, "frame": int(frame),
                             "signature": signature, "source": "fresh_real_dav2",
                             "q_dav2": array_identity(q)})
        if cache_file is not None:
            np.savez_compressed(cache_file, q_dav2=q, signature=np.array(signature),
                                provenance=np.array(json.dumps(identity, sort_keys=True)))
        return q, signature, "fresh_real_dav2", inference_seconds


def evaluate_session(session, conditions, cfg, depth_source, certificate_stream,
                     limit=0, progress_every=1):
    from blindnav_pc.mobilio_joint_route import query_joint_route
    sid = Path(session.dir).name
    indices = session.available_frames()
    if limit:
        indices = indices[:limit]
    if not indices:
        raise ValueError(f"no scored public frames: {sid}")
    ready_indices = [frame for frame in indices if session.tracking[frame]]
    positions, headings = route_frame(session, ready_indices)
    tangents = route_tangents(positions)
    contexts = {frame: (positions[k], float(headings[k]), tangents[k])
                for k, frame in enumerate(ready_indices)}
    calibrator = GroundCalibrator(cfg.ground)
    maps = {(condition, arm): (SurfaceGrid2D(), OccupancyGrid3D())
            for condition in conditions for arm in ARMS}
    assert len({id(m[0]) for m in maps.values()}) == len(maps)
    assert len({id(m[1]) for m in maps.values()}) == len(maps)
    rows, pairs = [], []
    started = time.perf_counter()
    for k, frame in enumerate(indices):
        inp, gt = session.inputs(frame), session.gt(frame)
        cls = gt_remap(gt.seg)
        pose = target = relative_pose = None
        if inp.tracking_ok:
            position, heading, tangent = contexts[frame]
            pose = SurfacePose(float(position[0]), float(position[1]), heading)
            tangent_yaw = math.atan2(float(tangent[0]), float(tangent[1]))
            target = float(wrap_pi(tangent_yaw - heading))
            relative_pose = Pose2D(0.0, 0.0, -target)
        # In public_gt_reference the oracle plane is explicitly an input. In the
        # DAV2 mode gt_ref is only used below for scoring, never depth calibration.
        gt_ref = (rse.compute_gt_reference(gt, cls, session, frame, relative_pose, rse.SW_CFG)
                  if inp.tracking_ok else rse.empty_gt_reference())
        q = fit = None
        inference_id = inference_source = None
        inference_seconds = 0.0
        if "public_gt_calibration_dav2" in conditions:
            q, inference_id, inference_source, inference_seconds = depth_source.predict(session, frame, inp.bgr)
            if inp.tracking_ok:
                fit = calibrator.update(q, rse.walkable_mask_from_classes(cls), session.K,
                                        up_from_gravity(inp.gravity_cam), up_source="attitude")
        for condition in conditions:
            this_inference_source = ("sanpo_gt_reference_no_model" if condition == "public_gt_reference"
                                     else inference_source)
            if not inp.tracking_ok:
                height = float(session.camera_height_m)
                up = None
                resolved = ab.ResolvedDepth(depth_m=None, up=None, camera_height_m=height,
                    ok=False, source="recorded_pose_not_ready", confidence=None)
            elif condition == "public_gt_reference":
                height = float(gt_ref.get("h") or session.camera_height_m)
                up = gt_ref.get("up")
                resolved = ab.resolve_condition_depth("annotated", None,
                    type("ReferencePlane", (), {"up": up})(), gt.depth_m, camera_height_m=height)
            else:
                height = float(session.camera_height_m)
                resolved = ab.resolve_condition_depth("production", q, fit, None,
                    camera_height_m=height, max_depth_m=cfg.ground.max_depth_m)
                up = resolved.up
            geometry_ok = bool(inp.tracking_ok and resolved.ok and resolved.depth_m is not None and up is not None
                               and np.isfinite(np.asarray(up)).all())
            resolved = replace(resolved, ok=geometry_ok)
            reason = ("recorded_pose_not_ready" if not inp.tracking_ok else
                      ("" if geometry_ok else "no_metric_depth_or_ground_plane"))
            old_result = new_result = unavailable(reason)
            certificate = {"status": "POSE_NOT_READY" if not inp.tracking_ok else "NO_GEOMETRY",
                           "movement_authorized": False,
                           "full_volume_visibility_certified": False, "reason": reason}
            depth = np.asarray(resolved.depth_m, dtype=np.float32) if geometry_ok else None
            roles = role_map_from_classes(cls)
            shared_id = json_hash({"condition": condition, "session": sid, "frame": int(frame),
                "inference": inference_id if condition.endswith("dav2") else "sanpo_gt",
                "semantic_classes": array_identity(cls), "role_map": array_identity(roles),
                "resolved_depth": array_identity(depth), "up": array_identity(up),
                "K": array_identity(session.K), "camera_height_m": height,
                "geometry_ok": geometry_ok, "pose": asdict(pose) if pose else None,
                "target_angle_rad": target})
            if geometry_ok:
                # Both queries get exactly these inputs. Mutating them is an error.
                depth.setflags(write=False)
                roles.setflags(write=False)
                up = np.asarray(up)
                old_maps, new_maps = maps[(condition, ARMS[0])], maps[(condition, ARMS[1])]
                for surface, occupancy in (old_maps, new_maps):
                    update_maps(surface, occupancy, roles, depth, session.K, up,
                                height, pose, int(frame))
                before_old, before_new = map_digest(*old_maps), map_digest(*new_maps)
                if before_old != before_new:
                    raise AssertionError("map states diverged before route queries")
                old_surface = old_maps[0].raycast_route(pose, target)
                old_occupancy = old_maps[1].raycast_clearance(pose, target)
                old_result = (fuse(old_surface, old_occupancy), old_surface, old_occupancy)
                result = query_joint_route(*new_maps, pose, target, frame_id=int(frame))
                new_result = (result.decision, result.surface, result.occupancy)
                certificate = dict(result.certificate)
                certificate["movement_authorized"] = bool(result.movement_authorized)
                if before_old != map_digest(*old_maps) or before_new != map_digest(*new_maps):
                    raise AssertionError("a route query mutated its map")
            fit_log = fit.to_log() if condition.endswith("dav2") and fit is not None else None
            extra = {"relative_time_ms": float(inp.t_ms), "pose_tracking_ok": bool(inp.tracking_ok),
                     "yaw_err_deg": math.degrees(-target) if target is not None else None,
                     "target_angle_rad": target, "geometry_ok": geometry_ok,
                     "calib_reason": reason, "depth_confidence": resolved.confidence,
                     "effective_camera_height_m": height,
                     "calib_fit_reason": fit_log.get("reason") if fit_log else
                         ("recorded_pose_not_ready" if not inp.tracking_ok else "oracle_ground_plane"),
                     "calib_up_source": fit_log.get("up_source") if fit_log else None,
                     "calib_attitude_visual_deg": fit_log.get("attitude_visual_deg") if fit_log else None,
                     "calib_n_ground_px": fit_log.get("n_ground_px") if fit_log else None,
                     "calib_confidence": fit_log.get("confidence") if fit_log else None,
                     "shared_input_sha256": shared_id, "inference_source": this_inference_source,
                     "dav2_inference_seconds": inference_seconds if condition.endswith("dav2") else None,
                     "rotation_only": False}
            old = result_row(condition, ARMS[0], sid, frame, old_result, gt_ref, resolved, extra)
            new = result_row(condition, ARMS[1], sid, frame, new_result, gt_ref, resolved, extra)
            new["map_contract_authorized"] = bool(certificate["movement_authorized"])
            declared = new["authorized_motion"] in ("LEFT", "RIGHT", "STRAIGHT")
            if declared != new["map_contract_authorized"]:
                raise AssertionError("candidate decision and authorization certificate disagree")
            rows.extend((old, new))
            pairs.append(paired_row(old, new))
            record = {"condition": condition, "session": sid, "frame": int(frame),
                      "relative_time_ms": float(inp.t_ms), "shared_input_sha256": shared_id,
                      "source": this_inference_source, "map_state_independent": True,
                      "dav2_inference_seconds": inference_seconds if condition.endswith("dav2") else None,
                      "ground_fit": fit_log, "effective_camera_height_m": height,
                      "pose_tracking_ok": bool(inp.tracking_ok),
                      "old_decision": asdict(old_result[0]), "new_decision": asdict(new_result[0]),
                      "certificate": certificate}
            certificate_stream.write(json.dumps(record, default=json_default, allow_nan=False) + "\n")
            certificate_stream.flush()
        if progress_every and ((k + 1) % progress_every == 0 or k + 1 == len(indices)):
            print(f"[{sid}] {k + 1}/{len(indices)} frames, {time.perf_counter() - started:.1f}s; "
                  f"DAV2={inference_seconds:.3f}s; old={old['safety_state']} new={new['safety_state']}",
                  flush=True)
    return rows, pairs, indices


def write_csv(path, rows):
    fields = list(dict.fromkeys(key for row in rows for key in row))
    # Up/height are scoring internals, not scalar CSV columns.
    fields = [key for key in fields if key not in ("up", "h")]
    with Path(path).open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", required=True)
    parser.add_argument("--sessions", nargs="+", default=None)
    parser.add_argument("--camera", default="camera_chest")
    parser.add_argument("--conditions", nargs="+", choices=CONDITIONS, default=list(CONDITIONS))
    parser.add_argument("--out", required=True)
    parser.add_argument("--max-width", type=int, default=640)
    parser.add_argument("--limit-per-session", type=int, default=0)
    parser.add_argument("--expected-frames", type=int)
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--torch-threads", type=int, default=4)
    parser.add_argument("--depth-snapshot")
    parser.add_argument("--depth-revision")
    parser.add_argument("--depth-input-size", type=int)
    parser.add_argument("--inference-cache")
    parser.add_argument("--inference-source-report",
                        help="metadata JSON of the earlier fresh-inference stage, linked and checked")
    parser.add_argument("--progress-every", type=int, default=1)
    parser.add_argument("--seed", type=int, default=0)
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    out = Path(args.out).resolve()
    if out.exists():
        raise SystemExit(f"refusing to overwrite existing output directory: {out}")
    out.mkdir(parents=True)
    started = time.perf_counter()
    metadata = {"tool": "pc/tools/joint_route_candidate_eval.py", "argv": sys.argv,
        "started_utc": datetime.now(timezone.utc).isoformat(), "status": "RUNNING",
        "base_commit": git_output("rev-parse", "HEAD"),
        "worktree_status": git_output("status", "--short"),
        "conditions": args.conditions, "arms": list(ARMS), "seed": args.seed,
        "torch_threads": args.torch_threads,
        "scope": "offline public-data map-contract comparison; not production or phone validation",
        "oracle_inputs": {"public_gt_reference": ["gt_semantics", "gt_depth", "gt_fitted_ground_plane",
            "recorded_pose", "target_direction_from_recorded_trajectory"],
            "public_gt_calibration_dav2": ["gt_semantics", "gt_walkable_calibration_mask",
            "recorded_pose_gravity_and_position", "target_direction_from_recorded_trajectory"]},
        "map_state_independent": True, "full_volume_visibility_certified": False,
        "notes": ["GT hazard/clear labels are frame proxies, not collision or straight-line accuracy.",
                  "GT proxies refer to the recorded-trajectory target corridor, not either selected route.",
                  "Target direction uses a noncausal +/-4m TLS fit on READY recorded poses in this 36-frame subset.",
                  "Subset target directions differ from a full 481-frame trajectory; historical totals are not a baseline.",
                  "NOT_READY frames remain in totals but do not calibrate, update/query maps or receive GT route-proxy labels.",
                  "Ground visibility does not establish observed free space at every body height.",
                  "Existing surface decay and all map-update semantics are unchanged."]}
    depth_source = None
    code = 2
    try:
        if len(args.conditions) != len(set(args.conditions)):
            raise ValueError("duplicate conditions")
        root = Path(args.data_root).resolve()
        names = args.sessions or sorted(p.name for p in root.iterdir() if (p / "description.json").exists())
        if not names or len(names) != len(set(names)):
            raise ValueError("missing or duplicate sessions")
        sessions = {name: SanpoSession(str(root / name), args.camera, max_width=args.max_width)
                    for name in names}
        selected = {sid: session.available_frames()[:args.limit_per_session or None]
                    for sid, session in sessions.items()}
        total = sum(map(len, selected.values()))
        if not total or (args.expected_frames is not None and total != args.expected_frames):
            raise ValueError(f"frame count {total} differs from required {args.expected_frames}")
        metadata["frame_keys"] = selected
        metadata["frames"] = total
        metadata["pose_ready_frame_keys"] = {sid: [frame for frame in frames if sessions[sid].tracking[frame]]
                                             for sid, frames in selected.items()}
        metadata["pose_ready_frames"] = sum(map(len, metadata["pose_ready_frame_keys"].values()))
        metadata["pose_not_ready_frames"] = total - metadata["pose_ready_frames"]
        metadata["session_geometry"] = {sid: {"K": session.K.tolist(),
            "dav2_calibration_height_m": session.camera_height_m,
            "dav2_height_source": "existing SANPO loader camera-height prior",
            "reference_height_source": "GT fitted ground plane; effective values in per-frame rows",
            "pose_file": Path(session.pose_file).name} for sid, session in sessions.items()}
        inventory = []
        for sid, session in sessions.items():
            files = [Path(session.dir) / "description.json", Path(session.pose_file)]
            for frame in selected[sid]:
                files.extend(Path(session.ldir) / folder / f"{frame:06d}{suffix}"
                    for folder, suffix in (("video_frames", ".png"), ("segmentation_masks", ".png"),
                                           ("depth_maps", ".float16.gz")))
            inventory.extend({"path": str(path.relative_to(root)), "size": path.stat().st_size,
                              "sha256": sha256_file(path)} for path in files)
        write_json(out / "input_inventory.json", inventory)
        metadata["input_inventory_sha256"] = sha256_file(out / "input_inventory.json")
        model_inventory = []
        if "public_gt_calibration_dav2" in args.conditions:
            # A local snapshot gives the run a reproducible weight inventory. There
            # is deliberately no unpinned remote-download fallback in this harness.
            if not args.depth_snapshot:
                raise ValueError("--depth-snapshot is required for real DAV2 inference")
            snapshot = Path(args.depth_snapshot).resolve()
            model_files = sorted(p for p in snapshot.rglob("*") if p.is_file())
            if not any(p.suffix in (".bin", ".safetensors") for p in model_files):
                raise ValueError("DAV2 snapshot has no weights")
            model_inventory = [{"path": str(path.relative_to(snapshot)), "size": path.stat().st_size,
                                "sha256": sha256_file(path)} for path in model_files]
        metadata["depth_model_inventory"] = model_inventory
        metadata["depth_revision"] = args.depth_revision
        metadata["depth_input_size_override"] = args.depth_input_size
        metadata["max_width"] = args.max_width
        tracked_code = ["pc/tools/joint_route_candidate_eval.py", "pc/tools/mobilio_sanpo_ab.py",
                        "pc/tools/responsibility_split_eval.py", "pc/tools/corridor_truth_eval.py",
                        "pc/blindnav_pc/mobilio_joint_route.py", "pc/blindnav_pc/mobilio_surface.py",
                        "pc/blindnav_pc/mobilio_occupancy.py", "pc/blindnav_pc/mobilio_fusion.py",
                        "pc/blindnav_pc/ground.py", "pc/blindnav_pc/depth.py", "pc/eval/sanpo.py"]
        metadata["code_sha256"] = {p: sha256_file(REPO_DIR / p) for p in tracked_code}
        upstream = {}
        if args.inference_source_report:
            source_path = Path(args.inference_source_report).resolve()
            source_report = json.loads(source_path.read_text())
            source_frames = source_path.parent / "per_frame_inference.json"
            source_records = json.loads(source_frames.read_text())
            if source_report.get("status") != "COMPLETED":
                raise ValueError("linked fresh-inference stage did not complete")
            upstream = {(r["session"], int(r["frame"])): r for r in source_records}
            metadata["upstream_fresh_inference_stage"] = {
                "metadata_path": str(source_path), "metadata_sha256": sha256_file(source_path),
                "per_frame_path": str(source_frames), "per_frame_sha256": sha256_file(source_frames),
                "fresh_inference_calls": source_report.get("fresh_inference_calls"),
                "elapsed_seconds": source_report.get("elapsed_seconds"),
                "model_load_seconds": source_report.get("model_load_seconds"),
                "source_frame_count": len(upstream)}
        write_json(out / "run_metadata.json", metadata)
        np.random.seed(args.seed)
        cfg = Config()
        cfg.ground.seed = args.seed
        metadata["base_config_template"] = cfg.to_dict()
        metadata["effective_config_by_session"] = {}
        depth_source = SharedDepth(args, model_inventory)
        all_rows, all_pairs = [], []
        with (out / "route_certificates.jsonl").open("w", encoding="utf-8") as certificates:
            for sid, session in sessions.items():
                cfg.ground.camera_height_m = float(session.camera_height_m)
                metadata["effective_config_by_session"][sid] = cfg.to_dict()
                rows, pairs, indices = evaluate_session(session, args.conditions, cfg, depth_source,
                    certificates, args.limit_per_session, args.progress_every)
                if indices != selected[sid]:
                    raise AssertionError("frame inventory changed during evaluation")
                all_rows.extend(rows)
                all_pairs.extend(pairs)
        expected_pairs = total * len(args.conditions)
        if len(all_pairs) != expected_pairs or len(all_rows) != 2 * expected_pairs:
            raise AssertionError("old/new frame alignment failed")
        if upstream:
            for record in depth_source.records:
                key = (record["session"], record["frame"])
                if key not in upstream or upstream[key]["signature"] != record["signature"]:
                    raise AssertionError(f"fresh-inference source signature mismatch: {key}")
            metadata["upstream_fresh_inference_stage"]["matched_input_signatures"] = len(depth_source.records)
        write_csv(out / "before_after.csv", all_pairs)
        write_csv(out / "per_frame.csv", all_rows)
        summary = {"frames": total, "conditions": {}, "scope": metadata["scope"],
                   "proxy_warning": metadata["notes"][0], "interpretation_notes": metadata["notes"],
                   "pose_ready_frames": metadata["pose_ready_frames"],
                   "pose_not_ready_frames": metadata["pose_not_ready_frames"]}
        for condition in args.conditions:
            summary["conditions"][condition] = {}
            for arm in ARMS:
                subset = [r for r in all_rows if r["condition"] == condition and r["arm"] == arm]
                summary["conditions"][condition][arm] = {"overall": rates(subset), "sessions": {
                    sid: rates([r for r in subset if r["session"] == sid]) for sid in names}}
        summary["changed_frames_by_condition"] = {
            condition: sum(bool(r["changed"]) for r in all_pairs if r["condition"] == condition)
            for condition in args.conditions}
        write_json(out / "summary.json", summary)
        metadata["status"] = "COMPLETED"
        code = 0
        print(json.dumps({"status": "COMPLETED", "frames": total,
                          "conditions": args.conditions, "output": str(out)}), flush=True)
    except Exception as exc:
        metadata["status"] = "FAILED"
        metadata["error"] = f"{type(exc).__name__}: {exc}"
        (out / "error.txt").write_text(traceback.format_exc(), encoding="utf-8")
        print(metadata["error"], file=sys.stderr, flush=True)
    finally:
        metadata["exit_code"] = code
        metadata["elapsed_seconds"] = time.perf_counter() - started
        metadata["finished_utc"] = datetime.now(timezone.utc).isoformat()
        metadata["dav2_fresh_inference_calls"] = depth_source.calls if depth_source else 0
        metadata["dav2_verified_cache_hits"] = depth_source.cache_hits if depth_source else 0
        metadata["dav2_model_load_seconds"] = depth_source.model_load_seconds if depth_source else None
        metadata["dav2_inference_seconds_per_frame"] = depth_source.inference_seconds if depth_source else []
        if depth_source:
            write_json(out / "shared_inference_manifest.json", depth_source.records)
            metadata["shared_inference_manifest_sha256"] = sha256_file(out / "shared_inference_manifest.json")
        metadata["cache_validation"] = ["input/model signature", "shape", "finite values",
                                        "q content and cache-file hashes recorded on read",
                                        "upstream fresh-inference signatures when source report supplied"]
        write_json(out / "run_metadata.json", metadata)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
