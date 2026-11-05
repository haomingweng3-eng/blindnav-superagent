#!/usr/bin/env python3
"""Four-arm corridor truth-loss diagnostic on SANPO-Real (measurement only).

This tool measures *where* annotated corridor truth is lost by a real-data corridor
algorithm.  It does not change any algorithm, model or threshold; it only runs the
existing corridor stub (``blindnav_pc.shadowwalk.ShadowWalkTracker``) and the existing
segmenter / depth / ground-calibration interfaces on the local SANPO-Real sessions.

Four arms (the corridor algorithm is identical in all four; only the segmentation
input and the uncertain boundary band change)::

    A: SANPO human-annotated segmentation        -> corridor algorithm
    B: v5 predicted segmentation                 -> corridor algorithm
    C: A, with the road/curb boundary band made uncertain
    D: B, with the same uncertain band construction

The v5 model is the team's SegFormer-B0 SANPO fine-tune (5 classes:
0 ignore, 1 SAFE, 2 BOUNDARY, 3 CONDITIONAL, 4 NON), loaded from a local
checkpoint that is deliberately NOT committed (``pc/models/`` is gitignored).
SANPO ``road`` (id 1) is CONDITIONAL, not SAFE, so a segmentation that labels road
as SAFE is the "Road-as-Sidewalk" error this diagnostic is about.

Two geometry inputs are reported, because the pipeline has two layers that can lose
the truth and the ticket asks which one loses it first:

``production``
    Depth-Anything-V2-Small inverse depth + the existing per-frame ground
    calibration (``blindnav_pc.ground.GroundCalibrator``) with the pose-derived
    gravity proxy.  This is the honest deployment-like input chain.
``oracle-geometry``
    GT depth + the GT ground-plane up vector / camera height.  Diagnostic ablation:
    it removes depth and gravity error so the corridor construction itself can be
    scored.  It is never a system input.

Corridor / pose setup (documented because the real sessions have no route file):
the corridor centreline is the recorded route itself -- the VIO pose trajectory
projected on the local horizontal plane.  The route tangent at a frame is a total
least-squares line over the trajectory within +/- 4 m of path arclength, so the
walker sits on the centreline by construction and any LEFT/RIGHT output is a
correction the system invented, not one the walker needed to follow the route.
The camera yaw error is the pose heading minus the route tangent; a body/camera
rotation that does not change the walking direction therefore becomes the
"phone rotation -> LEFT/RIGHT" wrong-correction case the operator asked about.

Metrics, all with numerator/denominator:

* Road-as-Sidewalk: of the GT road ground points inside the forward corridor band
  (1.5-6.0 m, |lateral| <= half width + band margin, GT geometry), the share the
  arm's corridor mask labels walkable.
* curb recall: of the GT curb ground points inside that same band, the share the
  arm marks uncertain (inside the uncertain boundary band).  A/B have no uncertain
  class by construction -- the current corridor consumes a binary walkable mask and
  discards the boundary label; C/D add it back through the band.
* corridor coverage: the tracker's forward-bin coverage (1.5-6.0 m, 1.0 m bins,
  >= 5 walkable points per bin); also the share of frames whose coverage reaches
  ``ShadowWalkConfig.min_coverage``.
* wrong-correction count: LEFT/RIGHT emitted on a frame where the camera heading
  rotates >= 6 deg within 1 s while the route tangent turns <= 2 deg (rotation-only
  event, using the walking state machine's documented 6 deg rotation suspicion
  threshold).  All LEFT/RIGHT outputs are also counted for context.
* UNKNOWN rate: UNKNOWN output frames / frames.

Usage (from ``pc/`` with the venv active, offline)::

    HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 python tools/corridor_truth_eval.py \
        --data-root data/sanpo_real/sanpo-real \
        --v5-checkpoint ../blindnav-real-eval/models/segformer_sanpo_b0_v5.pt \
        --out out/corridor_truth

The output directory gets ``summary.json``, ``metrics.csv``, ``per_frame.csv`` and
``summary.md``; ``--save-failures`` additionally writes raw frames + overlays.
No number produced by this tool is a device or blind-user result.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import sys
import time
from collections import Counter, defaultdict
from typing import Dict, List, Optional, Sequence, Tuple

import cv2
import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from blindnav_pc.bev import depth_to_level_points
from blindnav_pc.config import Config
from blindnav_pc.ego import quat_to_matrix, wrap_pi
from blindnav_pc.ground import GroundCalibrator, metric_depth, up_from_gravity
from blindnav_pc.shadowwalk import (LEFT, RIGHT, STRAIGHT, STOP, UNKNOWN, Corridor, Pose2D,
                                    ShadowWalkConfig, ShadowWalkTracker, mask_to_map_points)

SW_CFG = ShadowWalkConfig()   # the existing corridor algorithm config; unchanged by this tool
from eval.sanpo import SanpoSession

# ---------------------------------------------------------------------------
# SANPO label ids and the v5 5-class remap used by the training/eval scripts.
# (tools/eval/scripts/train_sanpo_segformer.py, eval_segformer_v2.py)
# ---------------------------------------------------------------------------
SAFE_IDS = (3, 5, 6, 17)        # sidewalk, crosswalk, paved trail, other walkable
BOUNDARY_IDS = (2,)             # curb / boundary
CONDITIONAL_IDS = (1, 30)       # road, terrain
CLASS_SAFE, CLASS_BOUND, CLASS_COND, CLASS_NON = 1, 2, 3, 4

ARMS = ("A", "B", "C", "D")
MODE_PRODUCTION = "production"
MODE_ORACLE = "oracle-geometry"
MODES = (MODE_PRODUCTION, MODE_ORACLE)
ARM_DESCRIPTION = {
    "A": "annotated segmentation -> corridor algorithm",
    "B": "v5 predicted segmentation -> corridor algorithm",
    "C": "A + uncertain road/curb boundary band",
    "D": "B + uncertain road/curb boundary band",
}

#: Band width used by C/D.  This is the corridor stub's own lateral deadband constant
#: (``ShadowWalkConfig.deadband_m``), re-used as a ground-distance tolerance instead of
#: being tuned for this experiment.
BAND_M = ShadowWalkConfig().deadband_m

#: Existing rotation-suspicion threshold from the walking state machine report
#: (docs/BLINDNAV_CORE_OPTIMIZATION_REPORT.md: "heading change >= 6 deg within 1 s").
ROTATION_DEG = 6.0
ROTATION_WINDOW_S = 1.0
#: A rotation is only "rotation-only" if the route tangent barely turns over the same window.
ROTATION_TANGENT_DEG = 2.0

#: A frame counts as a curb / road event only if at least this many GT ground points of
#: that class fall in the corridor band.  Kept small so that narrow curbs are still events;
#: every rate below reports its own numerator and denominator.
MIN_EVENT_POINTS = 5

#: Swept-body half width used by the curb/road-in-path events: 0.3 m body half width plus
#: a 0.3 m safety margin.  Fixed a priori; this tool never tunes it against results.
BODY_HALF_M = 0.6

COMMANDS = (STRAIGHT, LEFT, RIGHT, STOP, UNKNOWN)


# ---------------------------------------------------------------------------
# SANPO class remap / arm masks
# ---------------------------------------------------------------------------
def gt_remap(seg: np.ndarray) -> np.ndarray:
    """SANPO semantic ids -> v5 5-class ids (0 ignore, 1 SAFE, 2 BOUNDARY, 3 COND, 4 NON)."""
    out = np.zeros(seg.shape, dtype=np.uint8)
    out[np.isin(seg, SAFE_IDS)] = CLASS_SAFE
    out[np.isin(seg, BOUNDARY_IDS)] = CLASS_BOUND
    out[np.isin(seg, CONDITIONAL_IDS)] = CLASS_COND
    known = (seg != 0) & ~np.isin(seg, (27,))
    out[known & (out == 0)] = CLASS_NON
    return out


def make_arm_classes(arm: str, gt_cls: np.ndarray, v5_cls: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    """Return (binary walkable mask, boundary-label mask) before the uncertain band."""
    if arm in ("A", "C"):
        cls = gt_cls
    else:
        cls = v5_cls
    walk = (cls == CLASS_SAFE).astype(np.uint8)
    # The corridor consumes a binary walkable mask; the boundary label is the only extra
    # information C/D are allowed to reintroduce.
    boundary = (cls == CLASS_BOUND).astype(np.uint8)
    return walk, boundary


def uncertain_band(walk: np.ndarray, boundary_label: np.ndarray, depth_m: np.ndarray,
                   fx: float, band_m: float = BAND_M) -> np.ndarray:
    """Mask of pixels within ``band_m`` ground distance of the road/curb boundary.

    The boundary is the morphological gradient of the walkable region plus any pixels the
    arm's segmentation explicitly labels BOUNDARY.  The radius is converted from a ground
    distance to pixels with the per-pixel metric depth (``r = band_m * fx / z``), so the
    band is physical rather than a hand-picked pixel width.
    """
    walk_u8 = (walk > 0).astype(np.uint8)
    if walk_u8.any():
        grad = cv2.morphologyEx(walk_u8, cv2.MORPH_GRADIENT, np.ones((3, 3), np.uint8))
    else:
        grad = np.zeros_like(walk_u8)
    boundary = ((grad > 0) | (boundary_label > 0)).astype(np.uint8)
    if not boundary.any():
        return np.zeros(walk.shape, dtype=bool)
    dist = cv2.distanceTransform(1 - boundary, cv2.DIST_L2, 5)
    z = np.asarray(depth_m, dtype=np.float64)
    z = np.where(np.isfinite(z) & (z > 0.5), z, 20.0)
    radius_px = band_m * float(fx) / np.clip(z, 0.5, 20.0)
    return dist <= radius_px


# ---------------------------------------------------------------------------
# v5 checkpoint loading / inference (same preprocessing as the existing eval scripts)
# ---------------------------------------------------------------------------
def load_v5(checkpoint: str, device: Optional[str] = None):
    import torch
    from transformers.models.segformer import SegformerForSemanticSegmentation
    dev = device or ("mps" if torch.backends.mps.is_available() else "cpu")
    ckpt = torch.load(checkpoint, map_location="cpu")
    if "state_dict" not in ckpt:
        raise SystemExit(f"unexpected v5 checkpoint format in {checkpoint!r}: no state_dict")
    model = SegformerForSemanticSegmentation.from_pretrained(
        ckpt.get("model", "nvidia/segformer-b0-finetuned-cityscapes-1024-1024"),
        num_labels=5, ignore_mismatched_sizes=True).to(dev).eval()
    model.load_state_dict(ckpt["state_dict"])
    return model, dev


_MEAN = np.array([0.485, 0.456, 0.406], np.float32)
_STD = np.array([0.229, 0.224, 0.225], np.float32)


def predict_v5(model, device: str, bgr: np.ndarray, out_hw: Tuple[int, int]) -> np.ndarray:
    """Return a uint8 HxW 5-class map at ``out_hw`` (H, W)."""
    import torch
    import torch.nn.functional as F
    from PIL import Image
    im = Image.fromarray(bgr[..., ::-1]).resize((512, 288), Image.BILINEAR)
    x = np.asarray(im, np.float32) / 255.0
    x = (x - _MEAN) / _STD
    xt = torch.from_numpy(np.ascontiguousarray(x.transpose(2, 0, 1)))[None].to(device)
    with torch.inference_mode():
        logits = model(pixel_values=xt).logits
        logits = F.interpolate(logits, size=tuple(out_hw), mode="bilinear", align_corners=False)
        return logits.argmax(1)[0].cpu().numpy().astype(np.uint8)


# ---------------------------------------------------------------------------
# Corridor / pose geometry from the recorded route
# ---------------------------------------------------------------------------
def route_frame(session: SanpoSession, indices: Sequence[int]):
    """Project pose positions/headings to a local horizontal map frame.

    Map frame: x = right, z = route-forward at session start, up = world up.  The
    absolute choice is irrelevant; all geometry below uses offsets from the walker.
    """
    world_up = np.asarray(session.world_up, dtype=np.float64)
    world_up = world_up / np.linalg.norm(world_up)
    z_dir = np.array([1.0, 0.0, 0.0])                     # world-X reference forward
    z_dir = z_dir - (z_dir @ world_up) * world_up
    if np.linalg.norm(z_dir) < 1e-6:
        z_dir = np.array([0.0, 1.0, 0.0])
    z_dir /= np.linalg.norm(z_dir)
    x_dir = np.cross(z_dir, world_up)
    x_dir /= np.linalg.norm(x_dir)
    pos = np.zeros((len(indices), 2), dtype=np.float64)
    heading = np.zeros(len(indices), dtype=np.float64)
    for k, i in enumerate(indices):
        R = quat_to_matrix(session.quat[i])
        P = np.asarray(session.pos[i], dtype=np.float64)
        pos[k] = (P @ x_dir, P @ z_dir)
        fw = R @ np.array([0.0, 0.0, 1.0])                # camera z forward, world
        fh = fw - (fw @ world_up) * world_up
        if np.linalg.norm(fh) < 1e-6:
            heading[k] = heading[k - 1] if k else 0.0
        else:
            heading[k] = math.atan2(float(fh @ x_dir), float(fh @ z_dir))
    return pos, heading


def route_tangents(pos: np.ndarray, half_m: float = 4.0) -> np.ndarray:
    """Unit route tangent per frame: robust TLS line over +/- ``half_m`` of arclength."""
    n = len(pos)
    if n < 2:
        return np.tile(np.array([0.0, 1.0]), (max(n, 1), 1))
    seg = np.linalg.norm(np.diff(pos, axis=0), axis=1)
    arc = np.concatenate([[0.0], np.cumsum(seg)])
    tangents = np.zeros((n, 2))
    for i in range(n):
        a = max(0, int(np.searchsorted(arc, arc[i] - half_m, side="right")) - 1)
        b = min(n - 1, int(np.searchsorted(arc, arc[i] + half_m, side="left")))
        if b - a < 2:
            a, b = max(0, i - 1), min(n - 1, i + 1)
        P = pos[a:b + 1]
        c = P.mean(0)
        try:
            d = np.linalg.svd(P - c, full_matrices=False)[2][0]
        except np.linalg.LinAlgError:
            d = np.array([0.0, 1.0])
        travel = pos[min(i + 2, n - 1)] - pos[max(i - 2, 0)]
        if float(d @ travel) < 0:
            d = -d
        norm = float(np.linalg.norm(d))
        tangents[i] = d / norm if norm > 1e-9 else np.array([0.0, 1.0])
    return tangents


def frame_dt_s(indices: Sequence[int], fps: float) -> float:
    if len(indices) < 2:
        return 1.0 / max(fps, 1e-6)
    step = np.median(np.diff(indices)) if len(indices) > 1 else 1.0
    return float(step) / max(fps, 1e-6)


def rotation_only_flags(headings: np.ndarray, tangents: np.ndarray, dt_s: float,
                        rotation_deg: float = ROTATION_DEG,
                        tangent_deg: float = ROTATION_TANGENT_DEG,
                        window_s: float = ROTATION_WINDOW_S) -> np.ndarray:
    """Frames where the camera rotated but the walking direction did not (phone rotation)."""
    n = len(headings)
    flags = np.zeros(n, dtype=bool)
    if n < 2:
        return flags
    w = max(1, int(round(window_s / max(dt_s, 1e-6))))
    for i in range(n):
        j = i - w
        if j < 0:
            continue
        d_head = math.degrees(abs(wrap_pi(headings[i] - headings[j])))
        t0 = math.atan2(tangents[j][0], tangents[j][1])
        t1 = math.atan2(tangents[i][0], tangents[i][1])
        d_tan = math.degrees(abs(wrap_pi(t1 - t0)))
        flags[i] = (d_head >= rotation_deg) and (d_tan <= tangent_deg)
    return flags


# ---------------------------------------------------------------------------
# Reference GT geometry (oracle up/height) and ground points
# ---------------------------------------------------------------------------
def oracle_plane(session: SanpoSession, i: int, gt) -> Optional[Tuple[np.ndarray, float]]:
    r = session._gt_plane(i, gt=gt)
    if r is None:
        return None
    normal, height = r
    up = np.asarray(normal, dtype=np.float64)
    up = up / np.linalg.norm(up)
    return up, float(height)


def ground_point_table(depth_m: np.ndarray, K: np.ndarray, up: np.ndarray, h: float,
                       pose: Pose2D, cfg: ShadowWalkConfig) -> dict:
    """Ground points (stride 4) in corridor coordinates for the given pose.

    Returns dict with fwd/lat (meters in the corridor frame), pixel indices and the
    ground-plane height residual.
    """
    x_lat, z_fwd, height, (vs, us) = depth_to_level_points(
        depth_m, K, up, h, stride=cfg.pixel_stride)
    ok = (np.abs(height) <= cfg.max_height_m) & np.isfinite(x_lat) & np.isfinite(z_fwd)
    x_lat, z_fwd, height, vs, us = x_lat[ok], z_fwd[ok], height[ok], vs[ok], us[ok]
    cy, sy = math.cos(pose.yaw_rad), math.sin(pose.yaw_rad)
    mx = x_lat * cy + z_fwd * sy
    mz = -x_lat * sy + z_fwd * cy
    return {"fwd": mz, "lat": mx, "vs": vs, "us": us, "height": height}


def in_corridor_band(fwd: np.ndarray, lat: np.ndarray, cfg: ShadowWalkConfig,
                     half_width_m: float, near_m: Optional[float] = None,
                     far_m: Optional[float] = None) -> np.ndarray:
    near = cfg.near_m if near_m is None else near_m
    far = cfg.far_m if far_m is None else far_m
    return ((fwd >= near) & (fwd <= far) &
            (np.abs(lat) <= half_width_m + cfg.band_margin_m))


# ---------------------------------------------------------------------------
# Per-frame evaluation
# ---------------------------------------------------------------------------
def eval_frame(session: SanpoSession, model, device: str, depth_estimator, cfg: Config,
               gt_calibrators: Dict[str, GroundCalibrator], i: int, pose_yaw: float,
               tangent: np.ndarray, rotation_only: bool, band_m: float,
               modes: Sequence[str]) -> List[dict]:
    inp = session.inputs(i)
    gt = session.gt(i)
    gt_cls = gt_remap(gt.seg)
    v5_cls = predict_v5(model, device, inp.bgr, gt.seg.shape)
    q_dav2 = depth_estimator.predict(inp.bgr)
    up_pose = up_from_gravity(inp.gravity_cam)
    plane = oracle_plane(session, i, gt)
    up_oracle, h_oracle = plane if plane is not None else (None, None)

    tang_yaw = math.atan2(float(tangent[0]), float(tangent[1]))
    yaw_err_pose = float(wrap_pi(pose_yaw - tang_yaw))
    # Work in a per-frame corridor frame: the walker sits at the origin looking down +z and
    # the corridor is +z.  The pose yaw is therefore the camera-heading *minus* the route
    # tangent (the heading error the corridor algorithm actually sees).
    pose = Pose2D(0.0, 0.0, yaw_err_pose)
    corridor = Corridor("recorded-route", ((0.0, 0.0), (0.0, 60.0)), 2.5)

    # Ground truth reference table (oracle geometry), used for band selection and scoring
    # for every arm so the denominators are identical across arms.
    ref = None
    gt_ref_hint = None
    if up_oracle is not None:
        ref = ground_point_table(gt.depth_m, session.K, up_oracle, h_oracle, pose, SW_CFG)
        fwd, lat = ref["fwd"], ref["lat"]
        ref_band = in_corridor_band(fwd, lat, SW_CFG, corridor.width_m / 2.0)
        # Road-as-Sidewalk ROI: the near field the corridor guidance is actually about
        # (1.5-10 m ahead, 3 m either side).  Wide enough to include the road beside the
        # pavement, which is where "walkable" invites the user across a curb.
        ref_near = in_corridor_band(fwd, lat, SW_CFG, 3.0, near_m=1.5, far_m=10.0)
        # Swept-body corridor: where the user's own body would go.  BODY_HALF_M =
        # 0.3 m body half width + 0.3 m safety margin, fixed a priori and not tuned.
        body = np.abs(lat) <= BODY_HALF_M
        road_path = (fwd >= 1.5) & (fwd <= 6.0) & body
        curb_path = (fwd >= 2.5) & (fwd <= 6.0) & body
        raw = gt.seg[ref["vs"], ref["us"]]
        ref["road_pts"] = np.nonzero(ref_near & (raw == 1))[0]         # SANPO id 1 = road
        ref["road_path_pts"] = np.nonzero(road_path & (raw == 1))[0]
        ref["curb_pts"] = np.nonzero(ref_band & (raw == 2))[0]         # SANPO id 2 = curb/boundary
        ref["curb_path_pts"] = np.nonzero(curb_path & (raw == 2))[0]
        ref["walk_pts"] = np.nonzero(ref_band & np.isin(raw, SAFE_IDS))[0]
        ref["band"] = ref_band
        ref["raw"] = raw
        # Reference command from the existing corridor algorithm fed with the annotated
        # mask under oracle geometry: the "GT corridor" the arms are compared against.
        gt_walk = (gt_cls == CLASS_SAFE).astype(np.uint8)
        gt_points = mask_to_map_points(gt_walk, gt.depth_m, session.K, up_oracle, h_oracle, pose, SW_CFG)
        gt_ref_hint = ShadowWalkTracker(corridor, SW_CFG).update(gt_points, pose)
    else:
        ref = None

    rows: List[dict] = []
    for mode in modes:
        for arm in ARMS:
            walk, boundary = make_arm_classes(arm, gt_cls, v5_cls)
            calib_fit = None
            if mode == MODE_PRODUCTION:
                fit = gt_calibrators[arm].update(q_dav2, walk, session.K, up_pose, up_source="attitude")
                calib_fit = fit
                if fit.ok:
                    depth_m = metric_depth(q_dav2, fit.a, fit.b, cfg.ground.max_depth_m)
                    up = fit.up if fit.up is not None else up_pose
                    h = float(cfg.ground.camera_height_m)
                else:
                    depth_m = None
                    up, h = up_pose, float(cfg.ground.camera_height_m)
            else:
                if up_oracle is None:
                    depth_m = None
                    up, h = up_pose, float(cfg.ground.camera_height_m)
                else:
                    depth_m, up, h = gt.depth_m, up_oracle, h_oracle

            band = np.zeros(walk.shape, dtype=bool)
            if arm in ("C", "D") and depth_m is not None:
                band = uncertain_band(walk, boundary, depth_m, session.K[0, 0], band_m)
            walk_in = walk & (~band)

            row: Dict[str, object] = {
                "mode": mode, "arm": arm, "session": os.path.basename(session.dir.rstrip("/")),
                "frame": int(i), "yaw_err_deg": round(math.degrees(yaw_err_pose), 2),
                "rotation_only": bool(rotation_only),
            }
            if mode == MODE_PRODUCTION:
                row["calib_ok"] = bool(calib_fit is not None and calib_fit.ok)
                row["calib_reason"] = "" if calib_fit is None else calib_fit.reason
                row["calib_conf"] = (None if calib_fit is None
                                     else round(float(calib_fit.confidence), 3))
            else:
                row["calib_ok"] = up_oracle is not None
                row["calib_reason"] = "" if up_oracle is not None else "no_gt_plane"
                row["calib_conf"] = None

            row["road_pts_band"] = int(len(ref["road_pts"])) if ref is not None else 0
            row["road_path_band"] = int(len(ref["road_path_pts"])) if ref is not None else 0
            row["curb_pts_band"] = int(len(ref["curb_pts"])) if ref is not None else 0
            row["curb_path_band"] = int(len(ref["curb_path_pts"])) if ref is not None else 0
            row["gt_walk_pts_band"] = int(len(ref["walk_pts"])) if ref is not None else 0
            row["gt_band_offset_m"] = (
                round(float(gt_ref_hint.walkable_offset_m), 3)
                if gt_ref_hint is not None and math.isfinite(gt_ref_hint.walkable_offset_m) else None)
            row["gt_hint"] = gt_ref_hint.hint if gt_ref_hint is not None else None

            if depth_m is not None:
                pts = mask_to_map_points(walk_in, depth_m, session.K, up, h, pose, SW_CFG)
                tracker = ShadowWalkTracker(corridor, SW_CFG)
                hint = tracker.update(pts, pose)
                row.update({
                    "hint": hint.hint, "reason": hint.reason,
                    "coverage": round(float(hint.coverage), 3),
                    "coverage_bins_ok": bool(hint.coverage >= SW_CFG.min_coverage),
                    "n_points": int(hint.n_points),
                    "walkable_offset_m": (round(float(hint.walkable_offset_m), 3)
                                          if math.isfinite(hint.walkable_offset_m) else None),
                    "yaw_correction_deg": round(math.degrees(hint.yaw_error_rad), 2),
                })
            else:
                row.update({"hint": UNKNOWN, "reason": "no_metric_depth", "coverage": 0.0,
                            "coverage_bins_ok": False, "n_points": 0,
                            "walkable_offset_m": None,
                            "yaw_correction_deg": round(math.degrees(yaw_err_pose), 2)})

            road_walkable = 0
            curb_uncertain = 0
            road_path_walkable = 0
            if ref is not None:
                if len(ref["road_pts"]):
                    road_walkable = int(np.count_nonzero(
                        walk_in[ref["vs"][ref["road_pts"]], ref["us"][ref["road_pts"]]]))
                if len(ref["curb_pts"]):
                    curb_uncertain = int(np.count_nonzero(
                        band[ref["vs"][ref["curb_pts"]], ref["us"][ref["curb_pts"]]]))
                if len(ref["road_path_pts"]):
                    road_path_walkable = int(np.count_nonzero(
                        walk_in[ref["vs"][ref["road_path_pts"]], ref["us"][ref["road_path_pts"]]]))
            row["road_pts_walkable"] = road_walkable
            row["curb_pts_uncertain"] = curb_uncertain
            row["road_path_walkable"] = road_path_walkable

            # Event flags used by the failure analysis (all explicit, no tuned thresholds).
            hint = row["hint"]
            row["false_safe_road"] = bool(row["road_path_band"] >= MIN_EVENT_POINTS
                                          and hint == STRAIGHT)
            row["missed_curb"] = bool(row["curb_path_band"] >= MIN_EVENT_POINTS and hint == STRAIGHT)
            row["unknown_when_gt_walkable"] = bool(
                hint == UNKNOWN and row["gt_walk_pts_band"] >= SW_CFG.min_points)
            # A product of the requested "phone rotation -> LEFT/RIGHT" class: the camera rotated
            # without the walking direction turning, and the yaw rule (not the lateral band rule)
            # produced the command.
            row["wrong_correction"] = bool(rotation_only and hint in (LEFT, RIGHT)
                                           and row["reason"] == "yaw")
            # Lateral corrections driven by the apparent walkable-band offset, not by the walker
            # leaving the route (the route is the recorded walk, so the walker is on it).
            row["band_correction"] = bool(hint in (LEFT, RIGHT) and row["reason"] == "lateral")
            rows.append(row)
    return rows


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------
def _rate(num: int, den: int) -> Optional[float]:
    return float(num) / float(den) if den else None


def summarize(rows: Sequence[dict], cfg=None) -> Dict[str, object]:
    out: Dict[str, object] = {}
    for mode in MODES:
        for arm in ARMS:
            sel = [r for r in rows if r["mode"] == mode and r["arm"] == arm]
            if not sel:
                continue
            n = len(sel)
            hints = Counter(str(r["hint"]) for r in sel)
            road_num = sum(int(r["road_pts_walkable"]) for r in sel)
            road_den = sum(int(r["road_pts_band"]) for r in sel)
            curb_num = sum(int(r["curb_pts_uncertain"]) for r in sel)
            curb_den = sum(int(r["curb_pts_band"]) for r in sel)
            corr = sum(1 for r in sel if r["hint"] in (LEFT, RIGHT))
            wrong = sum(1 for r in sel if r["wrong_correction"])
            band_corr = sum(1 for r in sel if r["band_correction"])
            rot = sum(1 for r in sel if r["rotation_only"])
            corr_reasons = Counter(str(r["reason"]) for r in sel if r["hint"] in (LEFT, RIGHT))
            cov_vals = [float(r["coverage"]) for r in sel if r["calib_ok"]]
            usable = sum(1 for r in sel if r["calib_ok"] and r["coverage_bins_ok"])
            known = sum(1 for r in sel if r["calib_ok"])
            entry = {
                "frames": n,
                "calib_ok": known, "calib_ok_rate": _rate(known, n),
                "hint_counts": {c: hints.get(c, 0) for c in COMMANDS},
                "unknown_rate": _rate(hints.get(UNKNOWN, 0), n),
                "unknown_n": hints.get(UNKNOWN, 0),
                "road_as_sidewalk": {"numerator": road_num, "denominator": road_den,
                                     "rate": _rate(road_num, road_den)},
                "curb_recall": {"numerator": curb_num, "denominator": curb_den,
                                "rate": _rate(curb_num, curb_den)},
                "corridor_coverage": {
                    "mean_coverage_known_frames": (float(np.mean(cov_vals)) if cov_vals else None),
                    "frames_ge_min_coverage": usable, "frames": n,
                    "rate_ge_min_coverage": _rate(usable, n),
                    "min_coverage": SW_CFG.min_coverage,
                },
                "corrections": {
                    "left_right_count": corr,
                    "left_right_reasons": dict(corr_reasons),
                    "wrong_correction_count": wrong,
                    "band_driven_correction_count": band_corr,
                    "rotation_only_frames": rot,
                    "wrong_correction_per_100_frames": (100.0 * wrong / n if n else None),
                },
                "events": {
                    "road_in_path_ignored_frames": sum(1 for r in sel if r["false_safe_road"]),
                    "road_in_path_ignored_rate": _rate(
                        sum(1 for r in sel if r["false_safe_road"]), n),
                    "road_in_path_walkable_frames": sum(
                        1 for r in sel if r["false_safe_road"] and r["road_path_walkable"] > 0),
                    "curb_in_path_ignored_frames": sum(1 for r in sel if r["missed_curb"]),
                    "curb_in_path_ignored_rate": _rate(
                        sum(1 for r in sel if r["missed_curb"]), n),
                    "curb_in_path_event_frames": sum(
                        1 for r in sel if int(r["curb_path_band"]) >= MIN_EVENT_POINTS),
                    "unknown_when_gt_walkable_frames": sum(
                        1 for r in sel if r["unknown_when_gt_walkable"]),
                    "unknown_when_gt_walkable_rate": _rate(
                        sum(1 for r in sel if r["unknown_when_gt_walkable"]), n),
                },
            }
            failing = [r for r in sel if failure_reason(r)]
            entry["failing_frames"] = len(failing)
            entry["failure_reasons"] = dict(Counter(failure_reason(r) for r in failing))
            entry["failure_layers"] = dict(Counter(classify_layer(r) for r in failing))
            out[f"{mode}/{arm}"] = entry
    return out


def per_session_summary(rows: Sequence[dict]) -> Dict[str, object]:
    out: Dict[str, object] = {}
    sessions = sorted({r["session"] for r in rows})
    for mode in MODES:
        for arm in ARMS:
            for sid in sessions:
                sel = [r for r in rows if r["mode"] == mode and r["arm"] == arm and r["session"] == sid]
                if not sel:
                    continue
                n = len(sel)
                hints = Counter(str(r["hint"]) for r in sel)
                out[f"{mode}/{arm}/{sid}"] = {
                    "frames": n,
                    "unknown_rate": _rate(hints.get(UNKNOWN, 0), n),
                    "hint_counts": {c: hints.get(c, 0) for c in COMMANDS},
                    "road_as_sidewalk": _rate(sum(int(r["road_pts_walkable"]) for r in sel),
                                              sum(int(r["road_pts_band"]) for r in sel)),
                    "curb_recall": _rate(sum(int(r["curb_pts_uncertain"]) for r in sel),
                                         sum(int(r["curb_pts_band"]) for r in sel)),
                    "wrong_correction_count": sum(1 for r in sel if r["wrong_correction"]),
                    "band_driven_correction_count": sum(1 for r in sel if r["band_correction"]),
                    "failure_layers": dict(Counter(
                        classify_layer(r) for r in sel if failure_reason(r))),
                }
    return out


# ---------------------------------------------------------------------------
# Failure-frame analysis and rendering
# ---------------------------------------------------------------------------
def classify_layer(row: dict, cfg=None) -> str:
    """Mechanical first-layer attribution for one failing frame.

    Order is the pipeline order (segmentation -> geometry -> motion -> state machine): the
    first layer at which the annotated truth is demonstrably absent from the corridor input
    wins.  Uses only fields recorded per frame; no tuned thresholds.
    """
    if int(row.get("road_path_walkable", 0) or 0) > 0:
        return "segmentation"                     # arm's mask calls road ground walkable
    if not row.get("calib_ok", True):
        return "geometry"                         # no metric geometry at all
    hint = row.get("hint")
    if hint == UNKNOWN and int(row.get("n_points", 0) or 0) < SW_CFG.min_points:
        return "geometry"                         # walkable ground exists but is not projected
    if row.get("wrong_correction"):
        return "motion"                           # camera rotation became a LEFT/RIGHT command
    if row.get("band_correction"):
        arm_b = row.get("walkable_offset_m")
        gt_b = row.get("gt_band_offset_m")
        if (row.get("arm") in ("B", "D") and arm_b is not None and gt_b is not None
                and abs(float(arm_b) - float(gt_b)) > SW_CFG.deadband_m):
            return "segmentation"                 # v5's visible band is shifted vs annotation
        return "geometry"                         # visible walkable band itself is off-centre
    return "state_machine"                        # mask and geometry fine, decision is not


def failure_reason(row: dict) -> Optional[str]:
    """Name of the first truth-loss event on this frame (None if the frame has none)."""
    if row.get("false_safe_road") and int(row.get("road_path_walkable", 0) or 0) > 0:
        return "road_in_path_walkable"
    if row.get("wrong_correction"):
        return "rotation_wrong_correction"
    if row.get("false_safe_road"):
        return "road_in_path_ignored"
    if row.get("missed_curb"):
        return "curb_in_path_ignored"
    if row.get("unknown_when_gt_walkable"):
        return "unknown_when_gt_walkable"
    return None


def render_overlay(session: SanpoSession, i: int, row: dict, gt_cls: np.ndarray,
                   v5_cls: np.ndarray, band: Optional[np.ndarray]) -> Optional[np.ndarray]:
    inp = session.inputs(i)
    img = inp.bgr.copy()
    overlay = img.copy()
    if band is not None:
        overlay[band] = (0, 165, 255)                       # uncertain band, orange
    road = (gt_cls == CLASS_COND)
    overlay[road] = (0, 0, 160)                             # GT road/terrain, dark red
    img = cv2.addWeighted(overlay, 0.45, img, 0.55, 0)
    h, w = img.shape[:2]
    cv2.line(img, (w // 2, h), (w // 2, int(h * 0.55)), (255, 255, 255), 1)
    lines = [
        f"#{i} {row['mode']}/{row['arm']} -> {row['hint']} ({row['reason']})",
        f"coverage={row['coverage']} n={row['n_points']} yaw={row['yaw_err_deg']}deg "
        f"gt_cmd={row.get('gt_hint')}",
        f"road in swept path={row['road_path_band']} walkable={row['road_path_walkable']}; "
        f"curb in swept path={row['curb_path_band']} uncertain={row['curb_pts_uncertain']}",
        f"layer={classify_layer(row)} event={failure_reason(row)}",
    ]
    for k, text in enumerate(lines):
        cv2.putText(img, text, (8, 18 + 17 * k), cv2.FONT_HERSHEY_SIMPLEX, 0.45,
                    (255, 255, 255), 1, cv2.LINE_AA)
    return img


def save_failure_frames(session: SanpoSession, model, device: str,
                        cfg: Config, rows: Sequence[dict], out_dir: str,
                        limit_per_kind: int = 2, max_total: int = 12,
                        band_m: float = BAND_M) -> List[dict]:
    """Save raw RGB + overlay for representative failing frames (production mode)."""
    os.makedirs(out_dir, exist_ok=True)
    prod = [r for r in rows if r["mode"] == MODE_PRODUCTION]
    by_kind: Dict[str, List[dict]] = defaultdict(list)
    for r in prod:
        reason = failure_reason(r)
        if reason:
            by_kind[reason].append(r)
    picked: List[Tuple[str, dict]] = []
    # Order follows the pipeline so the saved set spans both the segmentation and the
    # corridor/geometry/decision layers.
    for kind in ("road_in_path_walkable", "road_in_path_ignored", "rotation_wrong_correction",
                 "curb_in_path_ignored", "unknown_when_gt_walkable"):
        seen = set()
        for r in by_kind.get(kind, []):
            key = (r["session"], r["frame"])
            if key in seen:                       # A and C can flag the same frame
                continue
            seen.add(key)
            picked.append((kind, r))
            if len([p for p in picked if p[0] == kind]) >= limit_per_kind:
                break
        if len(picked) >= max_total:
            break
    manifest = []
    for kind, r in picked:
        i = int(r["frame"])
        raw_path = os.path.join(session.ldir, "video_frames", f"{i:06d}.png")
        raw = cv2.imread(raw_path, cv2.IMREAD_COLOR)
        stem = f"{kind}_{os.path.basename(session.dir.rstrip('/'))[:8]}_{i:06d}"
        raw_out = os.path.join(out_dir, stem + "_raw.jpg")
        if raw is not None:
            cv2.imwrite(raw_out, raw, [cv2.IMWRITE_JPEG_QUALITY, 92])
        gt = session.gt(i)
        gt_cls = gt_remap(gt.seg)
        v5_cls = predict_v5(model, device, session.inputs(i).bgr, gt.seg.shape)
        band = None
        if r["arm"] in ("C", "D"):
            walk, boundary = make_arm_classes(r["arm"], gt_cls, v5_cls)
            plane = oracle_plane(session, i, gt)
            if plane is not None:
                band = uncertain_band(walk, boundary, gt.depth_m, session.K[0, 0], band_m=band_m)
        overlay = render_overlay(session, i, r, gt_cls, v5_cls, band)
        ov_out = os.path.join(out_dir, stem + "_overlay.jpg")
        if overlay is not None:
            cv2.imwrite(ov_out, overlay, [cv2.IMWRITE_JPEG_QUALITY, 92])
        manifest.append({"kind": kind, "mode": r["mode"], "arm": r["arm"],
                         "session": r["session"], "frame": i, "hint": r["hint"],
                         "reason": r["reason"], "layer": classify_layer(r, cfg),
                         "raw": os.path.basename(raw_out), "overlay": os.path.basename(ov_out)})
    return manifest


# ---------------------------------------------------------------------------
# Report writers
# ---------------------------------------------------------------------------
def _pct(x: Optional[float]) -> str:
    return "n/a" if x is None else f"{100.0 * x:.1f}%"


def write_report(out_dir: str, summary: Dict[str, object], per_session: Dict[str, object],
                 per_frame: Sequence[dict], meta: dict) -> None:
    with open(os.path.join(out_dir, "summary.json"), "w", encoding="utf-8") as fh:
        json.dump({"meta": meta, "summary": summary, "per_session": per_session},
                  fh, indent=2, ensure_ascii=False)
    metric_fields = ["mode", "arm", "metric", "numerator", "denominator", "value"]
    with open(os.path.join(out_dir, "metrics.csv"), "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=metric_fields)
        w.writeheader()
        for mode in MODES:
            for arm in ARMS:
                key = f"{mode}/{arm}"
                if key not in summary:
                    continue
                s = summary[key]
                rows = [
                    ("Road-as-Sidewalk", s["road_as_sidewalk"]["numerator"],
                     s["road_as_sidewalk"]["denominator"], s["road_as_sidewalk"]["rate"]),
                    ("curb recall", s["curb_recall"]["numerator"],
                     s["curb_recall"]["denominator"], s["curb_recall"]["rate"]),
                    ("corridor coverage>=min", s["corridor_coverage"]["frames_ge_min_coverage"],
                     s["corridor_coverage"]["frames"],
                     s["corridor_coverage"]["rate_ge_min_coverage"]),
                    ("UNKNOWN", s["unknown_n"], s["frames"], s["unknown_rate"]),
                    ("wrong-correction (rotation->LEFT/RIGHT)",
                     s["corrections"]["wrong_correction_count"], s["frames"],
                     (s["corrections"]["wrong_correction_count"] / s["frames"]
                      if s["frames"] else None)),
                    ("band-driven LEFT/RIGHT", s["corrections"]["band_driven_correction_count"],
                     s["frames"],
                     (s["corrections"]["band_driven_correction_count"] / s["frames"]
                      if s["frames"] else None)),
                    ("road-in-path ignored", s["events"]["road_in_path_ignored_frames"],
                     s["frames"],
                     s["events"]["road_in_path_ignored_rate"]),
                    ("curb-in-path ignored", s["events"]["curb_in_path_ignored_frames"],
                     s["events"]["curb_in_path_event_frames"],
                     (s["events"]["curb_in_path_ignored_frames"] /
                      s["events"]["curb_in_path_event_frames"]
                      if s["events"]["curb_in_path_event_frames"] else None)),
                ]
                for name, num, den, val in rows:
                    w.writerow({"mode": mode, "arm": arm, "metric": name,
                                "numerator": num, "denominator": den, "value": val})
    fields = ["mode", "arm", "session", "frame", "hint", "reason", "coverage", "n_points",
              "coverage_bins_ok", "calib_ok", "calib_reason", "yaw_err_deg", "yaw_correction_deg",
              "walkable_offset_m", "gt_band_offset_m", "gt_hint",
              "road_pts_band", "road_pts_walkable", "road_path_band", "road_path_walkable",
              "curb_pts_band", "curb_pts_uncertain", "curb_path_band", "gt_walk_pts_band",
              "rotation_only", "wrong_correction", "band_correction",
              "false_safe_road", "missed_curb", "unknown_when_gt_walkable",
              "failure_reason", "failure_layer"]
    with open(os.path.join(out_dir, "per_frame.csv"), "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in per_frame:
            reason = failure_reason(r)
            out_row = dict(r, failure_reason=reason or "",
                           failure_layer=(classify_layer(r) if reason else ""))
            w.writerow(out_row)

    lines = [
        "# Four-arm corridor truth-loss diagnostic — machine summary",
        "",
        f"Generated: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"Frames: {meta.get('frames')} across {meta.get('sessions')} SANPO-Real sessions; "
        f"v5 checkpoint: `{meta.get('v5_checkpoint')}`",
        f"Evidence level: DESKTOP_REAL_DATA (offline, CPU/MPS); not device-verified, not user-tested.",
        "",
        "Metric definitions and the full failure analysis are in "
        "`docs/CORRIDOR_TRUTH_LOSS_20261106.md`.",
        "",
    ]
    for mode in MODES:
        lines += [f"## Geometry input: {mode}", "",
                  "| arm | frames | Road-as-Sidewalk | curb recall | coverage>=min | "
                  "UNKNOWN | wrong corrections |",
                  "|---|---:|---:|---:|---:|---:|---:|"]
        for arm in ARMS:
            key = f"{mode}/{arm}"
            if key not in summary:
                continue
            s = summary[key]
            lines.append(
                f"| {arm} | {s['frames']} | "
                f"{s['road_as_sidewalk']['numerator']}/{s['road_as_sidewalk']['denominator']} "
                f"({_pct(s['road_as_sidewalk']['rate'])}) | "
                f"{s['curb_recall']['numerator']}/{s['curb_recall']['denominator']} "
                f"({_pct(s['curb_recall']['rate'])}) | "
                f"{s['corridor_coverage']['frames_ge_min_coverage']}/"
                f"{s['corridor_coverage']['frames']} "
                f"({_pct(s['corridor_coverage']['rate_ge_min_coverage'])}) | "
                f"{_pct(s['unknown_rate'])} | "
                f"{s['corrections']['wrong_correction_count']}"
                f"/{s['corrections']['band_driven_correction_count']} |")
        lines.append("")
    lines += ["", "wrong corrections column: `rotation->LEFT/RIGHT` / `band-driven LEFT/RIGHT`.", "",
              "## First-layer attribution (production geometry)", "",
              "| arm | failing frames | segmentation | geometry | motion | state machine | "
              "failure events |", "|---|---:|---:|---:|---:|---:|---|"]
    for arm in ARMS:
        key = f"{MODE_PRODUCTION}/{arm}"
        if key not in summary:
            continue
        s = summary[key]
        layers = s["failure_layers"]
        lines.append(
            f"| {arm} | {s['failing_frames']} | {layers.get('segmentation', 0)} | "
            f"{layers.get('geometry', 0)} | {layers.get('motion', 0)} | "
            f"{layers.get('state_machine', 0)} | "
            + ", ".join(f"{k}={v}" for k, v in sorted(s["failure_reasons"].items())) + " |")
    lines.append("")
    with open(os.path.join(out_dir, "summary.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data-root", default=os.path.join(os.path.dirname(__file__), "..",
                                                        "data", "sanpo_real", "sanpo-real"))
    ap.add_argument("--sessions", nargs="*", default=None,
                    help="session ids (default: every session under --data-root)")
    ap.add_argument("--camera", default="camera_chest")
    ap.add_argument("--v5-checkpoint", required=True,
                    help="local path to segformer_sanpo_b0_v5.pt (not committed)")
    ap.add_argument("--out", default=os.path.join(os.path.dirname(__file__), "..", "out", "corridor_truth"))
    ap.add_argument("--max-width", type=int, default=640)
    ap.add_argument("--limit-per-session", type=int, default=0,
                    help="debug only: evaluate at most N frames per session (0 = all)")
    ap.add_argument("--band-m", type=float, default=BAND_M,
                    help="uncertain band half-width in ground metres for arms C/D")
    ap.add_argument("--modes", nargs="*", default=list(MODES), choices=list(MODES))
    ap.add_argument("--save-failures", action="store_true")
    ap.add_argument("--failures-limit-per-kind", type=int, default=2)
    ap.add_argument("--failures-max", type=int, default=8)
    ap.add_argument("--device", default=None)
    ap.add_argument("--seed", type=int, default=0)
    return ap.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    np.random.seed(args.seed)
    data_root = os.path.abspath(args.data_root)
    if not os.path.isdir(data_root):
        print(f"BLOCKED_MISSING_DATA: {data_root} does not exist", file=sys.stderr)
        return 2
    sessions = args.sessions or sorted(d for d in os.listdir(data_root)
                                       if os.path.isdir(os.path.join(data_root, d)))
    if not sessions:
        print(f"BLOCKED_MISSING_DATA: no sessions under {data_root}", file=sys.stderr)
        return 2
    cfg = Config()
    failure_manifest: List[dict] = []
    model, device = load_v5(args.v5_checkpoint, args.device)
    from blindnav_pc.depth import DepthEstimator
    depth_estimator = DepthEstimator(device=device)

    all_rows: List[dict] = []
    frame_counts: Dict[str, int] = {}
    t_start = time.time()
    for sid in sessions:
        session = SanpoSession(os.path.join(data_root, sid), args.camera,
                               max_width=args.max_width)
        cfg.ground.camera_height_m = float(session.camera_height_m)
        indices = session.available_frames()
        if args.limit_per_session and args.limit_per_session > 0:
            indices = indices[: args.limit_per_session]
        if len(indices) < 2:
            print(f"skip {sid}: fewer than 2 usable frames", file=sys.stderr)
            continue
        pos, headings = route_frame(session, indices)
        tangents = route_tangents(pos)
        dt = frame_dt_s(indices, session.fps)
        rotation = rotation_only_flags(headings, tangents, dt)
        calibrators = {arm: GroundCalibrator(cfg.ground) for arm in ARMS}
        print(f"[{sid}] frames={len(indices)} path={np.linalg.norm(np.diff(pos, axis=0), axis=1).sum():.1f} m "
              f"rot_only_frames={int(rotation.sum())}", flush=True)
        for k, i in enumerate(indices):
            rows = eval_frame(session, model, device, depth_estimator, cfg, calibrators, i,
                              headings[k], tangents[k], bool(rotation[k]), args.band_m, args.modes)
            all_rows.extend(rows)
            if (k + 1) % 25 == 0 or k + 1 == len(indices):
                print(f"  {k + 1}/{len(indices)} frames, {time.time() - t_start:.0f}s", flush=True)
        frame_counts[sid] = len(indices)
        if args.save_failures:
            manifest = save_failure_frames(
                session, model, device, cfg,
                [r for r in all_rows if r["session"] == sid],
                os.path.join(args.out, "failures", sid),
                limit_per_kind=args.failures_limit_per_kind,
                max_total=args.failures_max,
                band_m=args.band_m)
            failure_manifest.extend(manifest)
            print(f"  saved {len(manifest)} failure frames under {args.out}/failures/{sid}", flush=True)

    summary = summarize(all_rows, cfg)
    per_session = per_session_summary(all_rows)
    os.makedirs(args.out, exist_ok=True)
    meta = {
        "tool": "pc/tools/corridor_truth_eval.py",
        "data_root": data_root, "sessions": frame_counts,
        "frames": sum(frame_counts.values()),
        "v5_checkpoint": os.path.abspath(args.v5_checkpoint),
        "modes": list(args.modes), "band_m": args.band_m,
        "corridor": "recorded VIO route, width 2.5 m",
        "band_construction": (f"physical {args.band_m:g} m ground band around the "
                              "walkable-region boundary"),
        "rotation_only_threshold_deg": ROTATION_DEG,
        "rotation_window_s": ROTATION_WINDOW_S,
        "rot_tangent_deg": ROTATION_TANGENT_DEG,
        "arm_description": ARM_DESCRIPTION,
        "evidence_level": "DESKTOP_REAL_DATA",
    }
    write_report(args.out, summary, per_session, all_rows, meta)
    if args.save_failures:
        os.makedirs(os.path.join(args.out, "failures"), exist_ok=True)
        with open(os.path.join(args.out, "failures", "failures.json"), "w", encoding="utf-8") as fh:
            json.dump(failure_manifest, fh, indent=2, ensure_ascii=False)
        print(f"wrote {args.out}/failures/failures.json ({len(failure_manifest)} frames)")
    print(f"\nwrote {args.out}/summary.json, metrics.csv, per_frame.csv, summary.md")
    print(f"elapsed {time.time() - t_start:.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
