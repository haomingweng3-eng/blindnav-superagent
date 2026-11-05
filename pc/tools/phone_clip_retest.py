#!/usr/bin/env python3
"""Independent re-test of the Issue #18 responsibility split on the local annotated phone clips.

This is the *independent* re-test the ticket requires after the SANPO-Real A/B/C experiment: the
35 clips in ``~/Desktop/盲行导航/视频标注/手机视频_原文件`` were not used to design the split, its
constants or its thresholds (those are a priori body/layer constants; the design data was
SANPO-Real).  None of these clips is used for model training or parameter choice here.

**Label capability must be stated before any number.**  ``人工标注表_已完成.csv`` is a video-level
table: ``status`` (safe / near_miss / conflict), ``target`` category, an approximate onset time
for near-miss/conflict and a free-text note.  It has no per-frame segmentation/depth/pose, no
object boxes and no per-frame curb/road/clear-path ground truth.  Therefore:

* curb/road danger miss        -> NOT_EVALUABLE (no per-frame surface GT)
* 3D occupancy danger miss     -> NOT_EVALUABLE (no per-frame 3D occupancy GT)
* true false-stop (clear path)-> NOT_EVALUABLE (no confirmed-clear-path GT)
* latency to first hazard     -> NOT_EVALUABLE (only a coarse video-level onset time)
* effective guidance coverage -> PROXY system-output statistic (no GT needed, but the corridor is
                                 an explicitly assumed straight corridor: no IMU/pose/VIO exists
                                 in these clips, so absolute guidance direction is not validated)
* UNKNOWN share               -> PROXY system-output statistic (same assumption)
* decision flips              -> PROXY system-output statistic (same assumption)

The tool also reports, as a clearly-labelled descriptive proxy only, whether any non-forward
warning occurs near the human-noted onset of the four near-miss/conflict clips.  It never turns
that into a safety metric and never claims device or blind-user validation.

Nothing here changes the Android STOP Safety Policy, MotionAuthority or ArrivalAuthority.

Usage (from ``pc/`` with the offline venv)::

    HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 .venv/bin/python tools/phone_clip_retest.py \
        --v5-checkpoint /Users/mima0000/blindnav-real-eval/models/segformer_sanpo_b0_v5.pt \
        --out out/phone_retest
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

from blindnav_pc.config import Config
from blindnav_pc.corridor_evidence import build_split_corridor_evidence
from blindnav_pc.depth import DepthEstimator
from blindnav_pc.ground import GroundCalibrator, metric_depth, up_from_gravity
from blindnav_pc.io import gravity_from_pitch_roll, load_camera
from blindnav_pc.obstacle_guardian import COARSE, SPATIAL, ObstacleGuardian
from blindnav_pc.path_keeper import PathKeeper, PathObservation
from blindnav_pc.safety_coordinator import SafetyCoordinator
from blindnav_pc.safety_states import BLOCKED, CLEAR, OBSERVE, UNKNOWN
from blindnav_pc.shadowwalk import (LEFT, RIGHT, STRAIGHT, STOP, UNKNOWN as HINT_UNKNOWN,
                                    Corridor, Pose2D, ShadowWalkConfig, ShadowWalkTracker)

import responsibility_split_metrics as rsm
from corridor_evidence_eval import role_map_from_classes, walkable_mask_from_classes
from corridor_truth_eval import load_v5, predict_v5

DEFAULT_ANNOTATIONS = os.path.expanduser(
    "~/Desktop/盲行导航/视频标注/人工标注表_已完成.csv")
DEFAULT_VIDEOS = os.path.expanduser(
    "~/Desktop/盲行导航/视频标注/手机视频_原文件")
ARMS = ("A", "B", "C")
ARM_DESCRIPTION = {
    "A": "PR #17 as-is: four-channel negative-evidence fusion, no temporal confirmation",
    "B": "responsibility split: Path Keeper + Obstacle Guardian (coarse) + Safety Coordinator",
    "C": "B + spatial risk fusion (3D occupancy, swept intersection, clearance, continuity)",
}
CORRIDOR = Corridor("assumed-straight", ((0.0, 0.0), (0.0, 60.0)), 2.5)

#: The formal metric set for the independent clips, with its data-support verdict.  This matrix
#: is written verbatim into the report so the reader knows what the labels can and cannot decide.
LABEL_CAPABILITY: Tuple[Dict[str, str], ...] = (
    {"metric": "curb/road danger miss", "status": "NOT_EVALUABLE",
     "reason": "video-level labels only; no per-frame curb/road segmentation or geometry GT"},
    {"metric": "3D occupancy danger miss", "status": "NOT_EVALUABLE",
     "reason": "video-level labels only; no per-frame 3D occupancy / object boxes GT"},
    {"metric": "true false-stop (confirmed clear path)", "status": "NOT_EVALUABLE",
     "reason": "no per-frame clear-path GT; a safe video label does not make each frame clear"},
    {"metric": "latency to first hazard warning", "status": "NOT_EVALUABLE",
     "reason": "only a coarse video-level onset time for 4 clips; no frame-level hazard onset"},
    {"metric": "effective guidance coverage", "status": "PROXY",
     "reason": "GT-free system output share, but corridor geometry is assumed (no IMU/pose/VIO)"},
    {"metric": "UNKNOWN share", "status": "PROXY",
     "reason": "GT-free system output share under the same assumed corridor geometry"},
    {"metric": "decision flips", "status": "PROXY",
     "reason": "GT-free system output statistic under the same assumed corridor geometry"},
)


# ---------------------------------------------------------------------------
# Annotation table
# ---------------------------------------------------------------------------
def read_annotations(path: str) -> List[Dict[str, object]]:
    """Read the local annotation CSV (utf-8-sig: the file may carry a BOM)."""
    rows: List[Dict[str, object]] = []
    with open(path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        for raw in reader:
            video = str(raw.get("video_file", "")).strip()
            if not video:
                continue
            onset = raw.get("approx_time_s", "")
            onset_val: Optional[float] = None
            if onset not in (None, ""):
                try:
                    onset_val = float(onset)
                except ValueError:
                    onset_val = None
            status = str(raw.get("status", "")).strip().lower()
            if status not in ("safe", "near_miss", "conflict"):
                raise ValueError(f"unknown status {status!r} for {video}")
            rows.append({"video_file": video, "status": status,
                         "target": str(raw.get("target", "")).strip(),
                         "approx_time_s": onset_val,
                         "notes": str(raw.get("notes", "")).strip()})
    return rows


def _first_warning_time(rows: Sequence[Dict[str, object]]) -> Optional[float]:
    """First time (s) with a non-forward command in a clip's rows; None if none."""
    times = [float(r["t_s"]) for r in rows
             if str(r["effective_command"]) in ("STOP", "OBSERVE", "UNKNOWN")]
    return min(times) if times else None


# ---------------------------------------------------------------------------
# Per-clip pipeline
# ---------------------------------------------------------------------------
def process_clip(video_path: str, annotation: Dict[str, object], model, device: str,
                 depth_estimator: DepthEstimator, cfg: Config, sample_fps: float,
                 max_frames: int, max_width: int) -> List[Dict[str, object]]:
    """Run the A/B/C arms on one phone clip (fixed pose; assumed straight corridor)."""
    import cv2
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise RuntimeError(f"cannot open {video_path}")
    fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
    if not math.isfinite(fps) or fps <= 0:
        cap.release()
        raise RuntimeError(f"invalid fps for {video_path}")
    stride = max(1, int(round(fps / max(sample_fps, 1e-6))))
    K = None
    up_pose = up_from_gravity(gravity_from_pitch_roll(cfg.camera.pitch_deg,
                                                      cfg.camera.roll_deg))
    # One stateful object per arm for this clip (temporal evidence is per clip).
    trackers = {"A": ShadowWalkTracker(CORRIDOR, ShadowWalkConfig(temporal_confirm_frames=0))}
    keeper = PathKeeper(CORRIDOR, ShadowWalkConfig())
    guardians = {"B": ObstacleGuardian(COARSE), "C": ObstacleGuardian(SPATIAL)}
    coordinator = SafetyCoordinator()
    calibrator = GroundCalibrator(cfg.ground)

    rows: List[Dict[str, object]] = []
    frame_idx = 0
    used = 0
    while used < max_frames:
        ok, bgr = cap.read()
        if not ok:
            break
        if frame_idx % stride:
            frame_idx += 1
            continue
        frame_idx += 1
        if bgr.shape[1] > max_width:
            scale = max_width / float(bgr.shape[1])
            bgr = cv2.resize(bgr, (max_width, max(1, int(round(bgr.shape[0] * scale)))),
                             interpolation=cv2.INTER_AREA)
        H, W = bgr.shape[:2]
        if K is None:
            K = load_camera(None, W, H, cfg.camera.fov_deg)
        t_s = (frame_idx - 1) / fps
        v5_cls = predict_v5(model, device, bgr, (H, W))
        q = depth_estimator.predict(bgr)
        walk = walkable_mask_from_classes(v5_cls)
        fit = calibrator.update(q, walk, K, up_pose, up_source="fixed")
        depth_m = metric_depth(q, fit.a, fit.b, cfg.ground.max_depth_m) if fit.ok else None
        calib_ok = bool(fit.ok)
        depth_conf = float(fit.confidence) if fit.ok else None
        if depth_m is None:
            for arm in ARMS:
                rows.append(_row(annotation, t_s, frame_idx - 1, arm, "UNKNOWN", "UNKNOWN",
                                 None, "no_metric_depth", calib_ok, depth_conf, {}))
            used += 1
            continue
        split = build_split_corridor_evidence(
            role_map_from_classes(v5_cls), depth_m, K, up_pose,
            cfg.ground.camera_height_m, Pose2D(0.0, 0.0, 0.0),
            pixel_stride=SW_PIXEL_STRIDE)
        observation = PathObservation.from_split(split)
        path_state = keeper.update(observation, Pose2D(0.0, 0.0, 0.0))
        hint_a = trackers["A"].update_evidence(split.evidence, Pose2D(0.0, 0.0, 0.0))
        a_state = (BLOCKED if hint_a.hint == STOP else
                   UNKNOWN if hint_a.hint == HINT_UNKNOWN else CLEAR)
        rows.append(_row(annotation, t_s, frame_idx - 1, "A", hint_a.hint, a_state,
                         hint_a.hint if hint_a.hint in (STRAIGHT, LEFT, RIGHT) else None,
                         hint_a.reason, calib_ok, depth_conf,
                         {"coverage": round(float(hint_a.coverage), 3),
                          "n_points": int(hint_a.n_points)}))
        for arm in ("B", "C"):
            risk = guardians[arm].update(split.obstacle, depth_confidence=depth_conf,
                                         calib_ok=calib_ok)
            decision = coordinator.decide(path_state, risk)
            rows.append(_row(annotation, t_s, frame_idx - 1, arm,
                             decision.effective_command(), decision.state,
                             decision.guidance, decision.reason, calib_ok, depth_conf,
                             {"coverage": round(float(path_state.coverage), 3),
                              "n_points": int(path_state.n_points),
                              "obstacle_state": risk.state,
                              "obstacle_clearance_m": (round(float(risk.clearance_m), 3)
                                                       if risk.clearance_m is not None else None),
                              "obstacle_in_path": int(risk.n_in_path)}))
        used += 1
    cap.release()
    return rows


SW_PIXEL_STRIDE = ShadowWalkConfig().pixel_stride


def _row(annotation: Dict[str, object], t_s: float, frame: int, arm: str, command: str,
         state: str, guidance: Optional[str], reason: str, calib_ok: bool,
         depth_conf: Optional[float], extra: Dict[str, object]) -> Dict[str, object]:
    row: Dict[str, object] = {
        "clip": annotation["video_file"], "status": annotation["status"],
        "target": annotation["target"], "approx_time_s": annotation["approx_time_s"],
        "t_s": round(float(t_s), 2), "frame": int(frame), "arm": arm,
        "effective_command": command, "safety_state": state, "guidance": guidance,
        "reason": reason, "calib_ok": bool(calib_ok),
        "depth_confidence": round(float(depth_conf), 4) if depth_conf is not None else None,
        "forward_guidance": command in rsm.GUIDANCE_COMMANDS,
        "warning": command in rsm.WARNING_COMMANDS,
    }
    row.update(extra)
    return row


# ---------------------------------------------------------------------------
# GT-free summary for the independent clips
# ---------------------------------------------------------------------------
def _metric(rows: Sequence[Dict[str, object]], predicate, denom_predicate=None) -> dict:
    denom = [r for r in rows if denom_predicate(r)] if denom_predicate else list(rows)
    num = [r for r in denom if predicate(r)]
    return {"numerator": len(num), "denominator": len(denom),
            "rate": (len(num) / len(denom)) if denom else None,
            "status": "EVALUABLE" if denom else "NOT_EVALUABLE",
            "low_sample": bool(0 < len(denom) < rsm.LOW_SAMPLE_MIN)}


def _flips(rows: Sequence[Dict[str, object]], dt_s: float) -> dict:
    by_clip: Dict[str, List[Dict[str, object]]] = {}
    for row in rows:
        by_clip.setdefault(str(row["clip"]), []).append(row)
    flips = transitions = 0
    for clip_rows in by_clip.values():
        clip_rows.sort(key=lambda r: int(r["frame"]))
        for prev, cur in zip(clip_rows[:-1], clip_rows[1:]):
            transitions += 1
            flips += int(prev["effective_command"] != cur["effective_command"])
    duration = transitions * float(dt_s)
    return {"flips": {"numerator": flips, "denominator": transitions,
                      "rate": (flips / transitions) if transitions else None,
                      "status": "EVALUABLE" if transitions else "NOT_EVALUABLE",
                      "low_sample": False},
            "flips_per_minute": {"value": (flips / duration * 60.0) if duration > 0 else None,
                                 "denominator": transitions,
                                 "status": "EVALUABLE" if duration > 0 else "NOT_EVALUABLE",
                                 "low_sample": False}}


def summarize_clips(rows: Sequence[Dict[str, object]], arms: Sequence[str],
                    dt_s: float) -> Dict[str, object]:
    """Per-arm GT-free summary plus per-status groups (safe / near_miss / conflict)."""
    out: Dict[str, object] = {}
    for arm in arms:
        arm_rows = [r for r in rows if r["arm"] == arm]
        if not arm_rows:
            continue
        block: Dict[str, object] = {
            "frames": len(arm_rows),
            "clips": len({r["clip"] for r in arm_rows}),
            "calib_ok_share": _metric(arm_rows, lambda r: bool(r["calib_ok"])),
            "guidance_coverage": _metric(arm_rows, lambda r: bool(r["forward_guidance"])),
            "warning_share": _metric(arm_rows, lambda r: bool(r["warning"])),
            "unknown_share": _metric(arm_rows, lambda r: r["safety_state"] == UNKNOWN),
            "observe_share": _metric(arm_rows, lambda r: r["safety_state"] == OBSERVE),
            "blocked_share": _metric(arm_rows, lambda r: r["safety_state"] == BLOCKED),
            "clear_share": _metric(arm_rows, lambda r: r["safety_state"] == CLEAR),
        }
        block.update(_flips(arm_rows, dt_s))
        block["per_status"] = {}
        for status in ("safe", "near_miss", "conflict"):
            status_rows = [r for r in arm_rows if r["status"] == status]
            if status_rows:
                block["per_status"][status] = {
                    "frames": len(status_rows),
                    "clips": len({r["clip"] for r in status_rows}),
                    "guidance_coverage": _metric(status_rows,
                                                 lambda r: bool(r["forward_guidance"])),
                    "warning_share": _metric(status_rows, lambda r: bool(r["warning"])),
                }
        block["per_clip"] = {}
        for clip in sorted({str(r["clip"]) for r in arm_rows}):
            clip_rows = [r for r in arm_rows if r["clip"] == clip]
            warning_t = _first_warning_time(clip_rows)
            block["per_clip"][clip] = {
                "frames": len(clip_rows),
                "status": clip_rows[0]["status"],
                "guidance_coverage": round(
                    sum(1 for r in clip_rows if r["forward_guidance"]) / len(clip_rows), 3),
                "warning_share": round(
                    sum(1 for r in clip_rows if r["warning"]) / len(clip_rows), 3),
                "first_warning_t_s": (None if warning_t is None else round(warning_t, 2)),
                "approx_time_s": clip_rows[0]["approx_time_s"],
            }
        out[arm] = block
    return out


def descriptive_onset_proxy(rows: Sequence[Dict[str, object]],
                            arms: Sequence[str]) -> List[Dict[str, object]]:
    """PROXY ONLY: warning time vs the human-noted onset on near-miss/conflict clips.

    This is not a latency metric: the annotation has a single coarse onset time and no
    frame-level hazard labels, so it is reported as descriptive evidence and stays
    NOT_EVALUABLE in the metric matrix.
    """
    out: List[Dict[str, object]] = []
    by_clip: Dict[str, List[Dict[str, object]]] = {}
    for row in rows:
        by_clip.setdefault(str(row["clip"]), []).append(row)
    for clip, clip_rows in sorted(by_clip.items()):
        status = str(clip_rows[0]["status"])
        onset = clip_rows[0].get("approx_time_s")
        if status not in ("near_miss", "conflict") or onset is None:
            continue
        for arm in arms:
            arm_rows = [r for r in clip_rows if r["arm"] == arm]
            first = _first_warning_time(arm_rows)
            out.append({
                "clip": clip, "status": status, "target": clip_rows[0]["target"],
                "approx_time_s": float(onset), "arm": arm,
                "first_warning_t_s": (None if first is None else round(first, 2)),
                "warning_before_or_at_note": (None if first is None
                                              else bool(first <= float(onset) + 0.5)),
                "note": "PROXY only; no frame-level hazard GT (NOT_EVALUABLE as a metric)",
            })
    return out


# ---------------------------------------------------------------------------
# Writers
# ---------------------------------------------------------------------------
def write_csv(path: str, rows: Sequence[Dict[str, object]]) -> None:
    if not rows:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("")
        return
    fields: List[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def render_md(summary: Dict[str, object], capability: Sequence[Dict[str, str]],
              proxy_rows: Sequence[Dict[str, object]], meta: Dict[str, object],
              arms: Sequence[str]) -> str:
    lines: List[str] = ["# Independent re-test on local annotated phone clips\n"]
    lines.append(f"Clips: {meta['clips']} ({meta['frames']} sampled frames at "
                 f"{meta['sample_fps']} fps; {meta['safe_clips']} safe, "
                 f"{meta['near_miss_clips']} near_miss, {meta['conflict_clips']} conflict).")
    lines.append("")
    lines.append("**Label capability is stated before any number.** The annotation table is "
                 "video-level; it has no per-frame segmentation, depth, pose or 3D occupancy "
                 "GT. Metrics that need frame-level GT are NOT_EVALUABLE here and no "
                 "substitute was improvised.")
    lines.append("")
    lines.append("| metric | status | why |")
    lines.append("|---|---|---|")
    for item in capability:
        lines.append(f"| {item['metric']} | **{item['status']}** | {item['reason']} |")
    lines.append("")
    lines.append("Evidence level: **PROTOTYPE** for the proxy statistics (desktop run on phone "
                 "clips with an assumed straight corridor, fixed 15 deg pitch and assumed "
                 "camera height; no IMU, no pose, no VIO). Not DEVICE_VERIFIED, not "
                 "USER_TESTED; no safety claim.")
    lines.append("")
    lines.append("## GT-free output statistics (PROXY)\n")
    lines.append("| arm | frames | guidance coverage | warning share | UNKNOWN share | "
                 "OBSERVE share | BLOCKED share | flips |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|")
    for arm in arms:
        block = summary.get(arm)
        if block is None:
            continue
        def pct(m):
            return "NOT_EVALUABLE" if m["status"] != "EVALUABLE" else f"{100 * m['rate']:.1f} %"
        lines.append(f"| {arm} | {block['frames']} | {pct(block['guidance_coverage'])} | "
                     f"{pct(block['warning_share'])} | {pct(block['unknown_share'])} | "
                     f"{pct(block['observe_share'])} | {pct(block['blocked_share'])} | "
                     f"{block['flips']['numerator']}/{block['flips']['denominator']} |")
    lines.append("")
    lines.append("### Per status group (`safe` / `near_miss` / `conflict`)\n")
    lines.append("| arm | status | frames | guidance coverage | warning share |")
    lines.append("|---|---|---:|---:|---:|")
    for arm in arms:
        block = summary.get(arm) or {}
        for status, group in block.get("per_status", {}).items():
            def pct(m):
                return "NOT_EVALUABLE" if m["status"] != "EVALUABLE" else f"{100 * m['rate']:.1f} %"
            lines.append(f"| {arm} | {status} | {group['frames']} | "
                         f"{pct(group['guidance_coverage'])} | {pct(group['warning_share'])} |")
    lines.append("")
    lines.append("## Descriptive onset proxy (NOT a latency metric)\n")
    lines.append("The annotation gives only one coarse onset time for the four near-miss / "
                 "conflict clips. The table below is descriptive; the formal latency metric "
                 "remains NOT_EVALUABLE.")
    lines.append("")
    lines.append("| clip | status | note time (s) | arm | first warning (s) | warning<=note+0.5s |")
    lines.append("|---|---|---:|---|---:|---|")
    for row in proxy_rows:
        lines.append(f"| {row['clip']} | {row['status']} | {row['approx_time_s']} | "
                     f"{row['arm']} | {row['first_warning_t_s']} | "
                     f"{row['warning_before_or_at_note']} |")
    lines.append("")
    lines.append("Remaining limitations: no IMU/pose/VIO in the clips, so the corridor is a "
                 "straight-line assumption and absolute LEFT/RIGHT guidance is not validated; "
                 "camera height / pitch are assumed, so metric distances can be biased; 2 fps "
                 "sampling can miss short events; none of these clips was used for design, so "
                 "this is an independent but weak (GT-free) re-test.")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        description="Independent GT-free re-test of the Issue #18 arms on local annotated "
                    "phone clips.",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--annotations-csv", default=DEFAULT_ANNOTATIONS)
    ap.add_argument("--videos-dir", default=DEFAULT_VIDEOS)
    ap.add_argument("--v5-checkpoint", default=None)
    ap.add_argument("--out", default=os.path.join(_PC_DIR, "out", "phone_retest"))
    ap.add_argument("--sample-fps", type=float, default=2.0)
    ap.add_argument("--max-frames-per-clip", type=int, default=90)
    ap.add_argument("--max-width", type=int, default=640)
    ap.add_argument("--camera-height", type=float, default=1.30,
                    help="assumed handheld camera height (m); no sensor gives it here")
    ap.add_argument("--pitch-deg", type=float, default=15.0,
                    help="assumed camera pitch down (deg); no IMU gives it here")
    ap.add_argument("--limit-clips", type=int, default=0)
    ap.add_argument("--device", default=None)
    ap.add_argument("--seed", type=int, default=0)
    return ap.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    np.random.seed(int(args.seed))
    if not os.path.isfile(args.annotations_csv):
        print(f"BLOCKED_MISSING_DATA: {args.annotations_csv} does not exist", file=sys.stderr)
        return 2
    if not os.path.isdir(args.videos_dir):
        print(f"BLOCKED_MISSING_DATA: {args.videos_dir} does not exist", file=sys.stderr)
        return 2
    if not args.v5_checkpoint:
        print("BLOCKED_MISSING_CHECKPOINT: --v5-checkpoint is required", file=sys.stderr)
        return 2
    annotations = read_annotations(args.annotations_csv)
    if args.limit_clips:
        annotations = annotations[:args.limit_clips]
    missing = [a["video_file"] for a in annotations
               if not os.path.isfile(os.path.join(args.videos_dir, a["video_file"]))]
    if missing:
        print(f"BLOCKED_MISSING_DATA: {len(missing)} annotated videos not found under "
              f"{args.videos_dir} (first: {missing[0]})", file=sys.stderr)
        return 2

    cfg = Config()
    cfg.camera.pitch_deg = float(args.pitch_deg)
    cfg.ground.camera_height_m = float(args.camera_height)
    model, device = load_v5(args.v5_checkpoint, args.device)
    depth_estimator = DepthEstimator(device=device)

    all_rows: List[Dict[str, object]] = []
    t_start = time.time()
    for n, annotation in enumerate(annotations):
        path = os.path.join(args.videos_dir, annotation["video_file"])
        print(f"[{n + 1}/{len(annotations)}] {annotation['video_file']} "
              f"({annotation['status']})", flush=True)
        all_rows.extend(process_clip(path, annotation, model, device, depth_estimator,
                                     cfg, args.sample_fps, args.max_frames_per_clip,
                                     args.max_width))

    if not all_rows:
        print("BLOCKED_NO_FRAMES: no clip frames were evaluated", file=sys.stderr)
        return 2
    summary = summarize_clips(all_rows, ARMS, 1.0 / max(args.sample_fps, 1e-6))
    proxy_rows = descriptive_onset_proxy(all_rows, ARMS)
    status_counts = {s: sum(1 for a in annotations if a["status"] == s)
                     for s in ("safe", "near_miss", "conflict")}
    meta = {
        "tool": "pc/tools/phone_clip_retest.py",
        "ticket": "Issue #18 P1 Path Keeper: independent re-test on local annotated clips",
        "annotations_csv": os.path.abspath(args.annotations_csv),
        "videos_dir": os.path.abspath(args.videos_dir),
        "v5_checkpoint": os.path.abspath(args.v5_checkpoint),
        "clips": len(annotations),
        "safe_clips": status_counts["safe"],
        "near_miss_clips": status_counts["near_miss"],
        "conflict_clips": status_counts["conflict"],
        "frames": len({(r["clip"], r["frame"]) for r in all_rows}),
        "sampled_rows": len(all_rows),
        "sample_fps": float(args.sample_fps),
        "max_width": int(args.max_width),
        "assumed": {"camera_height_m": float(args.camera_height),
                    "pitch_deg": float(args.pitch_deg),
                    "corridor": "assumed straight 2.5 m corridor; pose = identity",
                    "gravity": "fixed pitch/roll (no IMU)"},
        "label_capability": list(LABEL_CAPABILITY),
        "evidence_level": "PROTOTYPE",
        "device_verified": False,
        "user_tested": False,
        "clips_used_for_design": False,
        "notes": "Video-level labels only. Metrics needing frame-level GT are NOT_EVALUABLE; "
                 "GT-free output statistics are PROXY under assumed corridor/pitch/height.",
    }
    out_dir = os.path.abspath(args.out)
    os.makedirs(out_dir, exist_ok=True)
    write_csv(os.path.join(out_dir, "per_frame.csv"), all_rows)
    write_csv(os.path.join(out_dir, "onset_proxy.csv"), proxy_rows)
    with open(os.path.join(out_dir, "summary.json"), "w", encoding="utf-8") as fh:
        json.dump({"meta": meta, "arms": summary,
                   "label_capability": list(LABEL_CAPABILITY),
                   "onset_proxy": list(proxy_rows)}, fh, indent=1)
    with open(os.path.join(out_dir, "summary.md"), "w", encoding="utf-8") as fh:
        fh.write(render_md(summary, LABEL_CAPABILITY, proxy_rows, meta, ARMS))

    print("")
    for arm in ARMS:
        block = summary.get(arm) or {}
        if not block:
            continue
        cov = block["guidance_coverage"]
        cov_txt = ("NOT_EVALUABLE" if cov["status"] != "EVALUABLE"
                   else f"{100 * cov['rate']:.1f} %")
        print(f"PROXY {arm}: frames={block['frames']} guidance_coverage={cov_txt} "
              f"warning={block['warning_share']['numerator']}/"
              f"{block['warning_share']['denominator']} "
              f"unknown={block['unknown_share']['numerator']}/"
              f"{block['unknown_share']['denominator']} "
              f"flips={block['flips']['numerator']}/{block['flips']['denominator']}")
    print(f"\nwrote {out_dir}/per_frame.csv, onset_proxy.csv, summary.json, summary.md "
          f"({len(all_rows)} rows, {time.time() - t_start:.0f}s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
