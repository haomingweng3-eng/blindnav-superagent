#!/usr/bin/env python3
"""Attribute the remaining dangerous passes of the Issue #18 arms to a pipeline layer.

When the production input chain still emits forward guidance while GT curb/road/3D occupancy lies
in the body swept path, the decision layer cannot recover evidence that perception never produced.
This tool re-runs the production input chain on exactly those frames and classifies the missed GT
pixels into three layers (Issue #18: "attribute the failure to a specific layer"):

``segmentation``  the v5 5-class mask calls the GT danger pixel SAFE (walkable) -- the
                  segmentation layer lost it (Road-as-Sidewalk / curb-as-sidewalk / object as
                  walkable), so the decision layer never sees a non-walkable role.
``geometry``      the v5 mask does call the GT danger pixel non-walkable, but the metric depth /
                  ground calibration does not place it at the expected body-swept position
                  (height band / distance / per-point metric depth) -- the spatial-geometry layer
                  lost it.
``decision``      the evidence exists (v5 non-walkable role AND a body-swept obstacle-height
                  point at the GT pixel) yet the arm still emitted forward guidance -- the
                  decision layer lost it.  This is the only layer the algorithm change owns.

Every per-frame row is written to ``attribution_per_frame.csv``; the aggregate table is in
``attribution.json`` and ``attribution.md``.  Measurement only: no model, threshold or dataset is
changed by this tool.  Evidence level DESKTOP_REAL_DATA.

Usage (from ``pc/`` with the offline venv)::

    HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 .venv/bin/python \
        tools/responsibility_split_attribution.py \
        --per-frame out/responsibility_split/per_frame.csv \
        --v5-checkpoint /Users/mima0000/blindnav-real-eval/models/segformer_sanpo_b0_v5.pt \
        --out out/responsibility_split/attribution
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
from blindnav_pc.shadowwalk import Pose2D

from corridor_evidence_eval import (MIN_EVENT_POINTS, OBSTACLE_MAX_HEIGHT_M, SW_CFG,
                                    oracle_point_table, swept_body_mask,
                                    walkable_mask_from_classes)
from corridor_truth_eval import (gt_remap, load_v5, oracle_plane, predict_v5,
                                 route_frame, route_tangents)
from eval.sanpo import SanpoSession

ARMS = ("A", "B", "C")
DANGER_OBSTACLE_IDS = (4, 7, 8, 9, 10, 11, 12, 13, 14, 15, 20, 21, 22, 23, 24, 25, 26, 28)
ROAD_ID, CURB_ID = 1, 2
CLASS_NAMES = {0: "ignore", 1: "safe", 2: "boundary", 3: "conditional", 4: "non"}


def _flag(value) -> bool:
    return str(value).strip().lower() in ("true", "1", "1.0")


def read_miss_frames(per_frame_path: str, arm: str,
                     condition: str = "production") -> Dict[Tuple[str, int], Dict[str, object]]:
    """Frames where the given arm emitted forward guidance over GT road/curb/occupancy danger."""
    out: Dict[Tuple[str, int], Dict[str, object]] = {}
    with open(per_frame_path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            if row["condition"] != condition or row["arm"] != arm:
                continue
            if not _flag(row["forward_guidance"]):
                continue
            kinds = []
            if _flag(row["gt_road_in_path"]):
                kinds.append("road")
            if _flag(row["gt_curb_in_path"]):
                kinds.append("curb")
            if _flag(row["gt_occupancy_in_path"]):
                kinds.append("occupancy")
            if not kinds:
                continue
            key = (row["session"], int(row["frame"]))
            out.setdefault(key, {"session": key[0], "frame": key[1],
                                 "kinds": set(), "effective_command": row["effective_command"],
                                 "safety_state": row["safety_state"],
                                 "reason": row["reason"]})
            out[key]["kinds"].update(kinds)
    return out


def _pixel_lookup(vs: np.ndarray, us: np.ndarray,
                  fwd: np.ndarray, lat: np.ndarray,
                  height: np.ndarray) -> Dict[Tuple[int, int], Tuple[float, float, float]]:
    return {(int(v), int(u)): (float(f), float(la), float(h))
            for v, u, f, la, h in zip(vs, us, fwd, lat, height)}


def attribute_frame(session: SanpoSession, i: int, model, device: str,
                    depth_estimator, cfg: Config,
                    kinds: Sequence[str]) -> Dict[str, object]:
    """Classify the missed GT danger pixels of one frame into segmentation/geometry/decision."""
    inp = session.inputs(i)
    gt = session.gt(i)
    gt_cls = gt_remap(gt.seg)
    plane = oracle_plane(session, i, gt)
    if plane is None:
        return {"skipped": "no_gt_plane"}
    up_gt, h_gt = plane

    # Production input chain (identical to the A/B/C harness).
    v5_cls = predict_v5(model, device, inp.bgr, gt.seg.shape)
    q = depth_estimator.predict(inp.bgr)
    up_pose = up_from_gravity(inp.gravity_cam)
    fit = GroundCalibrator(cfg.ground).update(q, walkable_mask_from_classes(v5_cls),
                                              session.K, up_pose, up_source="attitude")
    if not fit.ok:
        return {"skipped": f"production_calibration_failed:{fit.reason}"}
    up_prod = fit.up if fit.up is not None else up_pose
    depth_prod = metric_depth(q, fit.a, fit.b, cfg.ground.max_depth_m)

    out: Dict[str, object] = {"attribution": Counter(), "class_at_gt": Counter(),
                              "n_gt_pixels": Counter(), "frame": int(i),
                              "session": os.path.basename(session.dir.rstrip("/"))}

    # GT body-swept pixels and their GT geometry (oracle table, same stride as production).
    indices = session.available_frames()
    pos, headings = route_frame(session, indices)
    k = indices.index(i)
    tangents = route_tangents(pos)
    tang_yaw = math.atan2(float(tangents[k][0]), float(tangents[k][1]))
    pose = Pose2D(0.0, 0.0, float(wrap_pi(headings[k] - tang_yaw)))

    gt_table = oracle_point_table(gt.depth_m, session.K, up_gt, h_gt, pose, SW_CFG)
    raw = np.asarray(gt.seg)[gt_table["vs"], gt_table["us"]]
    ground = np.abs(gt_table["height"]) <= SW_CFG.max_height_m
    swept = swept_body_mask(gt_table["fwd"], gt_table["lat"], SW_CFG)
    sw_half = SW_CFG.swept_half_width_m
    near, far = SW_CFG.near_m, SW_CFG.far_m

    # Production point table, keyed by source pixel.
    x_lat, z_fwd, height, (pvs, pus) = depth_to_level_points(
        depth_prod, session.K, up_prod, cfg.ground.camera_height_m,
        stride=SW_CFG.pixel_stride)
    depth_at_pixels = depth_prod[pvs, pus]
    valid = np.isfinite(depth_at_pixels) & (depth_at_pixels > 0)
    prod = _pixel_lookup(pvs[valid], pus[valid], z_fwd[valid], x_lat[valid], height[valid])

    def classify_pixels(mask_gt: np.ndarray, kind: str) -> None:
        vs = gt_table["vs"][mask_gt]
        us = gt_table["us"][mask_gt]
        out["n_gt_pixels"][kind] += int(len(vs))
        for v, u in zip(vs, us):
            cls = int(v5_cls[int(v), int(u)])
            out["class_at_gt"][f"{kind}:{CLASS_NAMES.get(cls, str(cls))}"] += 1
            if cls == 1:  # v5 says SAFE -> segmentation lost it
                out["attribution"][f"{kind}:segmentation"] += 1
                continue
            point = prod.get((int(v), int(u)))
            if point is None:
                out["attribution"][f"{kind}:geometry"] += 1
                continue
            f, la, h = point
            if not (near <= f <= far and abs(la) <= sw_half):
                out["attribution"][f"{kind}:geometry"] += 1
            elif kind == "occupancy" and not (SW_CFG.max_height_m < h <= OBSTACLE_MAX_HEIGHT_M):
                out["attribution"][f"{kind}:geometry"] += 1
            else:
                out["attribution"][f"{kind}:decision"] += 1

    if "occupancy" in kinds:
        occ_gt = (swept & (gt_table["height"] > SW_CFG.max_height_m)
                  & (gt_table["height"] <= OBSTACLE_MAX_HEIGHT_M)
                  & np.isin(raw, DANGER_OBSTACLE_IDS))
        if int(np.count_nonzero(occ_gt)) >= MIN_EVENT_POINTS:
            classify_pixels(occ_gt, "occupancy")
    if "road" in kinds:
        road_gt = (swept & ground & (raw == ROAD_ID)
                   & (gt_table["fwd"] >= near) & (gt_table["fwd"] <= far))
        if int(np.count_nonzero(road_gt)) >= MIN_EVENT_POINTS:
            classify_pixels(road_gt, "road")
    if "curb" in kinds:
        curb_gt = (swept & ground & (raw == CURB_ID) & (gt_table["fwd"] >= 2.5)
                   & (gt_table["fwd"] <= far))
        if int(np.count_nonzero(curb_gt)) >= MIN_EVENT_POINTS:
            classify_pixels(curb_gt, "curb")
    return out


def aggregate(per_frame: Sequence[Dict[str, object]],
              miss_keys: Sequence[Tuple[str, int]]) -> Dict[str, object]:
    total = Counter()
    frames_with = Counter()
    classes = Counter()
    n_pixels = Counter()
    for row in per_frame:
        if "skipped" in row:
            continue
        total.update(row["attribution"])
        classes.update(row["class_at_gt"])
        n_pixels.update(row["n_gt_pixels"])
        for kind in ("occupancy", "road", "curb"):
            if row["n_gt_pixels"].get(kind, 0):
                frames_with[kind] += 1
    agg: Dict[str, object] = {"frames_requested": len(miss_keys),
                              "frames_attributed": len([r for r in per_frame
                                                        if "skipped" not in r]),
                              "frames_skipped": len([r for r in per_frame
                                                     if "skipped" in r]),
                              "pixel_attribution": dict(total),
                              "class_at_gt_pixel": dict(classes),
                              "gt_pixels": dict(n_pixels),
                              "frames_with_kind": dict(frames_with),
                              "skipped_examples": [r["skipped"] for r in per_frame
                                                   if "skipped" in r][:10]}
    for kind in ("occupancy", "road", "curb"):
        n = int(n_pixels.get(kind, 0))
        if n:
            agg[f"{kind}_layer_share"] = {
                layer: round(total.get(f"{kind}:{layer}", 0) / n, 4)
                for layer in ("segmentation", "geometry", "decision")}
    return agg


def render_md(agg: Dict[str, object], arm: str) -> str:
    lines = [f"# Dangerous-pass layer attribution (production condition, arm {arm})\n"]
    lines.append("Frames where the arm emitted forward guidance while GT curb/road/3D occupancy "
                 "lay in the body swept path. Each missed GT pixel is classified by the production "
                 "input chain: `segmentation` = v5 mask says SAFE; `geometry` = v5 non-walkable "
                 "but metric depth/ground calibration does not place it in the body swept band; "
                 "`decision` = evidence exists and the decision layer still passed.")
    lines.append("")
    lines.append(f"Frames requested: {agg['frames_requested']}; attributed: "
                 f"{agg['frames_attributed']}; skipped: {agg['frames_skipped']}.")
    lines.append("")
    lines.append("| danger kind | GT pixels | segmentation share | geometry share | decision share |")
    lines.append("|---|---:|---:|---:|---:|")
    for kind in ("occupancy", "road", "curb"):
        n = int(agg["gt_pixels"].get(kind, 0))
        if not n:
            lines.append(f"| {kind} | 0 | NOT_EVALUABLE | NOT_EVALUABLE | NOT_EVALUABLE |")
            continue
        shares = agg[f"{kind}_layer_share"]
        lines.append(f"| {kind} | {n} | {100 * shares['segmentation']:.1f} % | "
                     f"{100 * shares['geometry']:.1f} % | {100 * shares['decision']:.1f} % |")
    lines.append("")
    if agg["skipped_examples"]:
        lines.append(f"Skipped frames (calibration/plane unavailable): "
                     f"{', '.join(str(s) for s in agg['skipped_examples'])}")
        lines.append("")
    lines.append("Class distribution at the missed GT pixels: " + ", ".join(
        f"`{k}`={v}" for k, v in sorted(agg["class_at_gt_pixel"].items())))
    lines.append("")
    return "\n".join(lines) + "\n"


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--per-frame", required=True)
    ap.add_argument("--data-root", default=os.path.join(_PC_DIR, "data", "sanpo_real",
                                                        "sanpo-real"))
    ap.add_argument("--v5-checkpoint", required=True)
    ap.add_argument("--out", default=os.path.join(_PC_DIR, "out", "responsibility_split",
                                                  "attribution"))
    ap.add_argument("--condition", default="production")
    ap.add_argument("--arm", default="C", choices=list(ARMS))
    ap.add_argument("--camera", default="camera_chest")
    ap.add_argument("--max-width", type=int, default=640)
    ap.add_argument("--device", default=None)
    return ap.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    if not os.path.isfile(args.per_frame):
        print(f"BLOCKED_MISSING_DATA: {args.per_frame}", file=sys.stderr)
        return 2
    miss = read_miss_frames(args.per_frame, args.arm, args.condition)
    if not miss:
        print("BLOCKED_NO_MISS_FRAMES: the arm has no dangerous passes to attribute",
              file=sys.stderr)
        return 2
    cfg = Config()
    model, device = load_v5(args.v5_checkpoint, args.device)
    from blindnav_pc.depth import DepthEstimator
    depth_estimator = DepthEstimator(device=device)

    per_frame: List[Dict[str, object]] = []
    sessions: Dict[str, SanpoSession] = {}
    t0 = time.time()
    for n, (key, info) in enumerate(sorted(miss.items())):
        sid, frame = key
        if sid not in sessions:
            session = SanpoSession(os.path.join(args.data_root, sid), args.camera,
                                   max_width=args.max_width)
            sessions[sid] = session
            cfg.ground.camera_height_m = float(session.camera_height_m)
        session = sessions[sid]
        row = attribute_frame(session, frame, model, device, depth_estimator, cfg,
                              sorted(info["kinds"]))
        row["kinds"] = sorted(info["kinds"])
        row["effective_command"] = info["effective_command"]
        row["reason"] = info["reason"]
        per_frame.append(row)
        if (n + 1) % 20 == 0 or n + 1 == len(miss):
            print(f"  {n + 1}/{len(miss)} frames, {time.time() - t0:.0f}s", flush=True)

    agg = aggregate(per_frame, list(miss.keys()))
    os.makedirs(args.out, exist_ok=True)
    with open(os.path.join(args.out, "attribution.json"), "w", encoding="utf-8") as fh:
        json.dump({"meta": {"tool": "pc/tools/responsibility_split_attribution.py",
                            "condition": args.condition, "arm": args.arm,
                            "per_frame": os.path.abspath(args.per_frame),
                            "v5_checkpoint": os.path.abspath(args.v5_checkpoint),
                            "evidence_level": "DESKTOP_REAL_DATA"},
                   "aggregate": agg}, fh, indent=1)
    with open(os.path.join(args.out, "attribution.md"), "w", encoding="utf-8") as fh:
        fh.write(render_md(agg, args.arm))
    # Per-frame CSV (counter dicts flattened).
    flat: List[Dict[str, object]] = []
    for row in per_frame:
        rec: Dict[str, object] = {k: v for k, v in row.items()
                                  if k not in ("attribution", "class_at_gt", "n_gt_pixels")}
        rec["kinds"] = "+".join(row.get("kinds", []))
        for kind in ("occupancy", "road", "curb"):
            rec[f"gt_pixels_{kind}"] = int(row.get("n_gt_pixels", {}).get(kind, 0))
            for layer in ("segmentation", "geometry", "decision"):
                rec[f"attr_{kind}_{layer}"] = int(
                    row.get("attribution", {}).get(f"{kind}:{layer}", 0))
        flat.append(rec)
    if flat:
        fields: List[str] = []
        for rec in flat:
            for key in rec:
                if key not in fields:
                    fields.append(key)
        with open(os.path.join(args.out, "attribution_per_frame.csv"), "w", newline="",
                  encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=fields)
            writer.writeheader()
            for rec in flat:
                writer.writerow(rec)
    print(f"\nwrote {args.out}/attribution.json/.md (+per-frame csv)")
    for kind in ("occupancy", "road", "curb"):
        if f"{kind}_layer_share" in agg:
            print(f"ARM {args.arm} {kind}: pixels={agg['gt_pixels'][kind]} "
                  f"shares={agg[f'{kind}_layer_share']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
