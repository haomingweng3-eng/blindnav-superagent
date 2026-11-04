#!/usr/bin/env python3
"""Offline ShadowWalk corridor eval on real segmentation masks (Blind-road smoke).

Each frame's real walkable mask (GT label map from ``pc/data/eval/blind_road_crosswalk``) is fed
through the same :class:`blindnav_pc.segmentation.Segmenter` interface used by the PC pipeline.
Because this smoke run has no depth network, the metric depth is the flat-ground plane implied by
the camera height and gravity direction; the external pose is synthetic unless ``--pose-csv`` is
given.  The tool prints one corridor hint per frame and never emits a "go" instruction.

Smoke mode (default)::

    python tools/shadowwalk_real_eval.py --limit 6
    python tools/shadowwalk_real_eval.py --corridor left_arc80 --offsets 0,0.5,-0.5,1.2
    python tools/shadowwalk_real_eval.py --pose-csv poses.csv

Report mode::

    python tools/shadowwalk_real_eval.py --split all --limit 0 \
        --report-dir out/blindroad_shadowwalk/varied_pose

Failure criteria (documented in the report):

* ``stop_on_open_walkable`` -- STOP while the pose is inside the corridor with an acceptable yaw
  and the raw label mask has enough walkable ground in the forward image ROI.
* ``unknown_when_mask_ok`` -- UNKNOWN while the raw label mask has enough walkable ground.
* ``spurious_turn`` -- LEFT/RIGHT while walker offset, visible-band offset and yaw error are all
  inside their deadbands.
* ``left_right_oscillation`` -- adjacent *consecutive-id* frames (``id_gap == 1``) flip between
  LEFT and RIGHT while the pose barely changes (|delta offset| <= 0.2 m, |delta yaw| <= 2 deg).

``mask_ok`` uses a coarse image-space ROI (lower half, central 60% of width) with a walkable-pixel
fraction threshold; it is independent of the tracker's corridor-band point count.

``poses.csv`` columns: ``x,z,yaw_rad`` (one row per frame, metres/radians), optional first column
``frame`` to index explicitly.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import re
import sys
from collections import Counter
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import cv2
import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from blindnav_pc.ground import flat_ground_depth_map
from blindnav_pc.segmentation import PrecomputedMaskSegmenter, label_map_to_walkable
from blindnav_pc.shadowwalk import (LEFT, PRESETS, RIGHT, STRAIGHT, STOP, UNKNOWN, Pose2D,
                                    ShadowWalkConfig, ShadowWalkTracker, get_corridor,
                                    load_corridor, mask_to_map_points)

DEFAULT_DATASET = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "data", "eval", "blind_road_crosswalk", "extracted",
    "Blind road and crosswalk dataset"))
SPLITS = ("train", "val", "test")
COMMANDS = (STRAIGHT, LEFT, RIGHT, STOP, UNKNOWN)
MASK_OK_SWEEP = (0.15, 0.20, 0.25, 0.30, 0.35, 0.40)


def _camera_matrix(width: int, height: int, fov_deg: float, fx: float | None = None) -> np.ndarray:
    f = float(fx) if fx else 0.5 * width / math.tan(math.radians(fov_deg) / 2.0)
    return np.array([[f, 0.0, width / 2.0], [0.0, f, height / 2.0], [0.0, 0.0, 1.0]])


def _frame_pairs(root: str, split: str):
    img_dir = os.path.join(root, split)
    lbl_dir = os.path.join(root, f"{split}_labels")
    if not os.path.isdir(img_dir) or not os.path.isdir(lbl_dir):
        raise SystemExit(f"missing split directories under {root!r}")
    labels = {os.path.splitext(n)[0]: os.path.join(lbl_dir, n) for n in os.listdir(lbl_dir)}
    for name in sorted(os.listdir(img_dir)):
        stem = os.path.splitext(name)[0]
        if stem in labels:
            yield stem, os.path.join(img_dir, name), labels[stem]


def _load_poses(path: str) -> dict[int, Pose2D]:
    poses: dict[int, Pose2D] = {}
    with open(path, "r", encoding="utf-8-sig", newline="") as fh:
        for i, row in enumerate(csv.DictReader(fh)):
            idx = int(row["frame"]) if "frame" in row and row["frame"] not in (None, "") else i
            poses[idx] = Pose2D(float(row["x"]), float(row["z"]), float(row["yaw_rad"]))
    return poses


def _frame_id(stem: str) -> Optional[int]:
    match = re.search(r"(\d+)", stem)
    return int(match.group(1)) if match else None


def _raw_walkable_frac(mask: np.ndarray, roi=(0.5, 1.0, 0.2, 0.8)) -> float:
    """Walkable-pixel fraction in the lower-central image ROI (pose-independent, coarse)."""
    h, w = mask.shape[:2]
    y0, y1, x0, x1 = roi
    patch = mask[int(h * y0):int(h * y1), int(w * x0):int(w * x1)]
    return float(patch.mean()) if patch.size else 0.0


def _command_table(rows: Sequence[dict]) -> Dict[str, dict]:
    counts = Counter(row["hint"] for row in rows)
    total = len(rows)
    return {
        "frames": total,
        "counts": {command: counts.get(command, 0) for command in COMMANDS},
        "percent": {command: (100.0 * counts.get(command, 0) / total if total else None)
                    for command in COMMANDS},
    }


def _evaluate(args) -> Tuple[List[dict], object]:
    corridor = load_corridor(args.corridor_json) if args.corridor_json else get_corridor(args.corridor)
    cfg = ShadowWalkConfig()
    tracker = ShadowWalkTracker(corridor, cfg)
    offsets = [float(v) for v in args.offsets.split(",") if v.strip()]
    poses = _load_poses(args.pose_csv) if args.pose_csv else None
    pitch = math.radians(args.pitch_deg)
    up = np.array([0.0, -math.cos(pitch), -math.sin(pitch)])
    half = corridor.width_m / 2.0
    splits = list(SPLITS) if args.split == "all" else [args.split]

    rows: List[dict] = []
    shown = 0
    for split in splits:
        for index, (stem, img_path, lbl_path) in enumerate(_frame_pairs(args.dataset, split)):
            if index < args.offset:
                continue
            if args.limit > 0 and shown >= args.limit:
                break
            img = cv2.imread(img_path, cv2.IMREAD_COLOR)
            label = cv2.imread(lbl_path, cv2.IMREAD_COLOR)
            if img is None or label is None:
                continue
            h, w = img.shape[:2]
            K = _camera_matrix(w, h, args.fov_deg, args.fx)
            raw_mask = label_map_to_walkable(label)
            raw_frac = _raw_walkable_frac(raw_mask)
            mask = PrecomputedMaskSegmenter(raw_mask).predict(img)
            depth = flat_ground_depth_map(K, w, h, up, args.height)
            if poses is not None:
                pose = poses.get(shown)
                if pose is None:
                    shown += 1
                    continue
            else:
                off = offsets[shown % len(offsets)] if offsets else 0.0
                pose = corridor.pose_at(args.start_s + shown * args.step_m, offset_m=off,
                                        yaw_error_rad=math.radians(args.yaw_error_deg))
            points = mask_to_map_points(mask, depth, K, up, args.height, pose, cfg)
            hint = tracker.update(points, pose)
            _, walker_off = corridor.project(pose.x_m, pose.z_m)
            yaw_deg = math.degrees(hint.yaw_error_rad)
            mask_ok = raw_frac >= args.mask_ok_frac
            pose_valid = (abs(walker_off) <= cfg.stop_offset_frac * half
                          and abs(yaw_deg) <= cfg.max_yaw_deg)
            band = hint.walkable_offset_m
            band_centred = math.isfinite(band) and abs(band) <= cfg.deadband_m
            stop_on_open = hint.hint == STOP and pose_valid and mask_ok
            unknown_mask_ok = hint.hint == UNKNOWN and mask_ok
            spurious_turn = (hint.hint in (LEFT, RIGHT) and abs(walker_off) <= cfg.deadband_m
                             and band_centred and abs(yaw_deg) <= cfg.deadband_yaw_deg)
            rows.append({
                "split": split,
                "frame": stem,
                "frame_id": _frame_id(stem),
                "s": round(hint.progress_m, 4),
                "offset_m": round(walker_off, 4),
                "yaw_deg": round(yaw_deg, 3),
                "coverage": round(hint.coverage, 4),
                "n_points": hint.n_points,
                "walkable_offset_m": (round(band, 4) if math.isfinite(band) else None),
                "raw_walkable_frac": round(raw_frac, 4),
                "hint": hint.hint,
                "reason": hint.reason,
                "mask_ok": mask_ok,
                "pose_valid": pose_valid,
                "stop_on_open_walkable": stop_on_open,
                "unknown_when_mask_ok": unknown_mask_ok,
                "spurious_turn": spurious_turn,
                "intended_off_corridor_stop": hint.hint == STOP and hint.reason == "off_corridor",
                "yaw_stop": hint.hint == STOP and hint.reason == "yaw_too_large",
            })
            shown += 1
        if args.limit > 0 and shown >= args.limit:
            break
    return rows, corridor


def _oscillation(rows: Sequence[dict]) -> Tuple[List[dict], dict]:
    """Adjacent consecutive-id pairs within a split; flag stable-pose LEFT/RIGHT flips."""
    pairs: List[dict] = []
    for split in sorted({row["split"] for row in rows}):
        seq = [row for row in rows if row["split"] == split and row["frame_id"] is not None]
        seq.sort(key=lambda row: row["frame_id"])
        for prev, curr in zip(seq, seq[1:]):
            gap = curr["frame_id"] - prev["frame_id"]
            flip = {prev["hint"], curr["hint"]} == {LEFT, RIGHT}
            pose_stable = (abs(curr["offset_m"] - prev["offset_m"]) <= 0.2
                           and abs(curr["yaw_deg"] - prev["yaw_deg"]) <= 2.0)
            pairs.append({
                "split": split, "prev_frame": prev["frame"], "next_frame": curr["frame"],
                "id_gap": gap, "prev_hint": prev["hint"], "next_hint": curr["hint"],
                "prev_offset_m": prev["offset_m"], "next_offset_m": curr["offset_m"],
                "prev_yaw_deg": prev["yaw_deg"], "next_yaw_deg": curr["yaw_deg"],
                "flip": flip, "pose_stable": pose_stable,
                "oscillation_failure": flip and pose_stable and gap == 1,
            })
    consecutive = [p for p in pairs if p["id_gap"] == 1]
    failures = [p for p in consecutive if p["oscillation_failure"]]
    all_flips = [p for p in pairs if p["flip"]]
    consecutive_flips = [p for p in consecutive if p["flip"]]
    return pairs, {
        "adjacent_pairs": len(pairs),
        "consecutive_id_pairs": len(consecutive),
        "consecutive_id_pairs_by_split": dict(Counter(p["split"] for p in consecutive)),
        "all_left_right_flips": len(all_flips),
        "left_right_flips_on_consecutive_ids": len(consecutive_flips),
        "stable_pose_oscillation_failures": len(failures),
        "stable_pose_oscillation_rate": (len(failures) / len(consecutive) if consecutive else None),
        "note": ("val/test ids are not temporally contiguous (only train has a meaningful share of "
                 "id_gap==1 pairs), so oscillation is reported per split and only gap==1 pairs count"),
    }


def _mask_ok_sensitivity(rows: Sequence[dict]) -> List[dict]:
    out = []
    for threshold in MASK_OK_SWEEP:
        ok = [row["raw_walkable_frac"] >= threshold for row in rows]
        out.append({
            "mask_ok_frac": threshold,
            "mask_ok_frames": sum(ok),
            "unknown_when_mask_ok": sum(1 for row, good in zip(rows, ok)
                                        if good and row["hint"] == UNKNOWN),
            "stop_on_open_walkable": sum(1 for row, good in zip(rows, ok)
                                         if good and row["hint"] == STOP and row["pose_valid"]),
        })
    return out


def _write_csv(path: str, fieldnames: Sequence[str], rows: Iterable[dict]) -> None:
    with open(path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _report(rows: List[dict], corridor, args) -> int:
    report_dir = os.path.abspath(args.report_dir)
    os.makedirs(report_dir, exist_ok=True)
    overall = _command_table(rows)
    by_split = {split: _command_table([row for row in rows if row["split"] == split])
                for split in sorted({row["split"] for row in rows})}
    pairs, oscillation = _oscillation(rows)
    sensitivity = _mask_ok_sensitivity(rows)

    failures = {
        "stop_on_open_walkable": sum(1 for row in rows if row["stop_on_open_walkable"]),
        "unknown_when_mask_ok": sum(1 for row in rows if row["unknown_when_mask_ok"]),
        "spurious_turn": sum(1 for row in rows if row["spurious_turn"]),
        "intended_off_corridor_stop": sum(1 for row in rows if row["intended_off_corridor_stop"]),
        "yaw_stop": sum(1 for row in rows if row["yaw_stop"]),
        "stop_reasons": dict(Counter(row["reason"] for row in rows if row["hint"] == STOP)),
        "unknown_reasons": dict(Counter(row["reason"] for row in rows if row["hint"] == UNKNOWN)),
        "oscillation": oscillation,
        "criteria": {
            "stop_on_open_walkable": "STOP and pose inside corridor (|offset|<=stop threshold, |yaw|<=max) and raw walkable ROI fraction >= mask-ok threshold",
            "unknown_when_mask_ok": "UNKNOWN and raw walkable ROI fraction >= mask-ok threshold",
            "spurious_turn": "LEFT/RIGHT with walker offset, visible band offset and yaw error all inside deadbands",
            "left_right_oscillation": "consecutive-id pair flips LEFT<->RIGHT with |delta offset|<=0.2 m and |delta yaw|<=2 deg",
            "mask_ok": f"walkable-pixel fraction in rows 50-100%, cols 20-80% >= {args.mask_ok_frac:g}",
        },
    }

    summary = {
        "inputs": {
            "dataset": os.path.abspath(args.dataset),
            "splits": sorted({row["split"] for row in rows}),
            "frames": len(rows),
            "frames_by_split": dict(Counter(row["split"] for row in rows)),
            "corridor": corridor.name,
            "corridor_length_m": round(corridor.length_m, 3),
            "corridor_width_m": corridor.width_m,
            "offsets_m": args.offsets,
            "pose_source": "csv" if args.pose_csv else "synthetic",
            "start_s": args.start_s,
            "step_m": args.step_m,
            "yaw_error_deg": args.yaw_error_deg,
            "camera_height_m": args.height,
            "pitch_deg": args.pitch_deg,
            "fov_deg": args.fov_deg,
            "mask_ok_frac": args.mask_ok_frac,
        },
        "commands": {"overall": overall, "by_split": by_split},
        "failures": failures,
        "mask_ok_sensitivity": sensitivity,
        "notes": [
            "Depth is a flat-ground plane, not a depth network; pose is synthetic unless --pose-csv is given",
            "The blind-road images are not a certified continuous trajectory; train ids are mostly contiguous, val/test are not",
            "UNKNOWN is deliberately conservative; a frame can have walkable pixels outside the predefined corridor band",
            "The stub never emits a 'go' instruction",
        ],
    }
    with open(os.path.join(report_dir, "summary.json"), "w", encoding="utf-8") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=2)

    _write_csv(os.path.join(report_dir, "per_frame.csv"),
               ["split", "frame", "frame_id", "s", "offset_m", "yaw_deg", "coverage", "n_points",
                "walkable_offset_m", "raw_walkable_frac", "hint", "reason", "mask_ok", "pose_valid",
                "stop_on_open_walkable", "unknown_when_mask_ok", "spurious_turn",
                "intended_off_corridor_stop", "yaw_stop"], rows)
    _write_csv(os.path.join(report_dir, "command_counts.csv"),
               ["scope", "command", "count", "percent"],
               [{"scope": "overall", "command": command, "count": overall["counts"][command],
                 "percent": overall["percent"][command]} for command in COMMANDS] +
               [{"scope": split, "command": command, "count": counts["counts"][command],
                 "percent": counts["percent"][command]}
                for split, counts in by_split.items() for command in COMMANDS])
    _write_csv(os.path.join(report_dir, "failures.csv"),
               ["split", "frame", "hint", "reason", "offset_m", "yaw_deg", "raw_walkable_frac",
                "mask_ok", "pose_valid", "stop_on_open_walkable", "unknown_when_mask_ok",
                "spurious_turn"],
               [row for row in rows if row["stop_on_open_walkable"] or row["unknown_when_mask_ok"]
                or row["spurious_turn"]])
    _write_csv(os.path.join(report_dir, "mask_ok_sensitivity.csv"),
               ["mask_ok_frac", "mask_ok_frames", "unknown_when_mask_ok", "stop_on_open_walkable"],
               sensitivity)
    _write_csv(os.path.join(report_dir, "oscillation.csv"),
               ["split", "prev_frame", "next_frame", "id_gap", "prev_hint", "next_hint",
                "prev_offset_m", "next_offset_m", "prev_yaw_deg", "next_yaw_deg", "flip",
                "pose_stable", "oscillation_failure"], pairs)

    def pct(value):
        return "n/a" if value is None else f"{value:.2f}%"

    failure_rows = [row for row in rows if row["stop_on_open_walkable"]
                    or row["unknown_when_mask_ok"] or row["spurious_turn"]]
    by_split_pairs = ", ".join(
        f"{key}={value}"
        for key, value in sorted(oscillation["consecutive_id_pairs_by_split"].items()))

    lines = [
        "# Blind-road ShadowWalk offline eval",
        "",
        "Real GT walkable masks are back-projected with a flat-ground depth plane and the corridor",
        "pose. There is no depth network and no real pose here; the pose is synthetic unless a",
        "pose CSV is supplied. The stub emits guidance only, never a crossing/go command.",
        "",
        "## Inputs",
        "",
        f"- Dataset: `{os.path.abspath(args.dataset)}`",
        f"- Splits: {', '.join(sorted({row['split'] for row in rows}))}",
        f"- Frames: {len(rows)} "
        f"({', '.join(f'{k}={v}' for k, v in sorted(Counter(row['split'] for row in rows).items()))})",
        f"- Corridor: `{corridor.name}` (length {corridor.length_m:.1f} m, width {corridor.width_m:.1f} m)",
        f"- Synthetic offsets: `{args.offsets}` m, step {args.step_m:g} m; "
        f"pose source: {'CSV' if args.pose_csv else 'synthetic'}",
        "",
        "## Command distribution",
        "",
        "| command | count | percent |",
        "|---|---:|---:|",
    ]
    for command in COMMANDS:
        lines.append(f"| {command} | {overall['counts'][command]} | "
                     f"{pct(overall['percent'][command])} |")
    lines += ["", "Per split:", "", "| split | frames | STRAIGHT | LEFT | RIGHT | STOP | UNKNOWN |",
              "|---|---:|---:|---:|---:|---:|---:|"]
    for split, table in by_split.items():
        lines.append(f"| {split} | {table['frames']} | " +
                     " | ".join(str(table["counts"][command]) for command in COMMANDS) + " |")
    lines += [
        "",
        "## Clearly-failed frames",
        "",
        "Criteria are stated in `summary.json` (`failures.criteria`). `intended_off_corridor_stop`",
        "is **not** a failure: the synthetic schedule deliberately places the walker at 1.2 m,",
        "outside the 2.5 m-wide corridor, and the stub is supposed to stop.",
        "",
        "| criterion | count |",
        "|---|---:|",
        f"| STOP on open walkable (`stop_on_open_walkable`) | **{failures['stop_on_open_walkable']}** |",
        f"| UNKNOWN when mask ok (`unknown_when_mask_ok`) | **{failures['unknown_when_mask_ok']}** |",
        f"| spurious turn while centred (`spurious_turn`) | {failures['spurious_turn']} |",
        f"| LEFT/RIGHT oscillation on consecutive ids | **{oscillation['stable_pose_oscillation_failures']}** |",
        f"| intended off-corridor STOP (not a failure) | {failures['intended_off_corridor_stop']} |",
        f"| yaw-too-large STOP | {failures['yaw_stop']} |",
        "",
        "STOP reasons: `" + str(failures["stop_reasons"]) + "`; "
        "UNKNOWN reasons: `" + str(failures["unknown_reasons"]) + "`.",
        "",
        "Failure frames:",
        "",
        "| split | frame | hint | reason | offset (m) | yaw (deg) | raw walkable frac |",
        "|---|---|---|---|---:|---:|---:|",
    ]
    for row in failure_rows:
        lines.append(f"| {row['split']} | {row['frame']} | {row['hint']} | {row['reason']} | "
                     f"{row['offset_m']:.2f} | {row['yaw_deg']:.1f} | {row['raw_walkable_frac']:.3f} |")
    if not failure_rows:
        lines.append("| - | - | - | - | - | - | - |")
    lines += [
        "",
        f"Oscillation base: {oscillation['consecutive_id_pairs']} consecutive-id pairs "
        f"({by_split_pairs}); {oscillation['left_right_flips_on_consecutive_ids']} LEFT/RIGHT "
        f"flips on those pairs; {oscillation['stable_pose_oscillation_failures']} with a stable pose.",
        "",
        "## Mask-ok threshold sensitivity",
        "",
        "| mask_ok fraction | mask-ok frames | UNKNOWN when mask ok | STOP on open walkable |",
        "|---:|---:|---:|---:|",
    ]
    for item in sensitivity:
        lines.append(f"| {item['mask_ok_frac']:g} | {item['mask_ok_frames']} | "
                     f"{item['unknown_when_mask_ok']} | {item['stop_on_open_walkable']} |")
    lines += [
        "",
        "## Caveats / blockers",
        "",
        "- No depth model: metric depth is a flat-ground plane. Absolute ranges and the 1.5-6 m",
        "  corridor band are therefore geometric assumptions, not measurements.",
        "- No real ego-motion: poses are synthetic. Consecutive frames are paired with a synthetic",
        "  step, so the command stream is not a real trajectory.",
        "- The dataset images are not certified as video: train ids are mostly contiguous, val/test",
        "  are random samples. Oscillation is therefore only counted on `id_gap == 1` pairs.",
        "- `mask_ok` is a coarse image-space ROI; walkable pixels outside the predefined corridor",
        "  make UNKNOWN defensible, so the failure count is a lower bound.",
        "- LEFT vs RIGHT skew reflects the visible walkable-band offset in the dataset, not a",
        "  heading controller.",
        "",
    ]
    with open(os.path.join(report_dir, "summary.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))

    print(f"report: {report_dir}")
    print(f"frames={len(rows)} commands={overall['counts']} "
          f"stop_on_open={failures['stop_on_open_walkable']} "
          f"unknown_mask_ok={failures['unknown_when_mask_ok']} "
          f"oscillation={oscillation['stable_pose_oscillation_failures']}")
    return 0


def _smoke(rows: Sequence[dict], corridor, args) -> int:
    print(f"corridor={corridor.name} length={corridor.length_m:.1f}m width={corridor.width_m:.1f}m "
          f"source=real-label-mask depth=flat-ground pose={'csv' if args.pose_csv else 'synthetic'}")
    print(f"{'frame':>10} {'s(m)':>5} {'off(m)':>7} {'yaw(deg)':>9} {'cover':>5} {'pts':>6}  "
          f"{'hint':<9} reason")
    for row in rows:
        print(f"{row['frame']:>10} {row['s']:>5.1f} {row['offset_m']:>7.2f} "
              f"{row['yaw_deg']:>9.1f} {row['coverage']:>5.2f} {row['n_points']:>6}  "
              f"{row['hint']:<9} {row['reason']}")
    counts = Counter(row["hint"] for row in rows)
    print(f"frames={len(rows)} hints={dict(counts)}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dataset", default=DEFAULT_DATASET, help="Blind-road dataset root")
    ap.add_argument("--split", default="test", help="train|val|test|all (default: test)")
    ap.add_argument("--limit", type=int, default=6, help="0 = all frames")
    ap.add_argument("--offset", type=int, default=0, help="skip this many frames per split")
    ap.add_argument("--corridor", default="straight60", help="preset: " + ", ".join(sorted(PRESETS)))
    ap.add_argument("--corridor-json")
    ap.add_argument("--offsets", default="0,0.4,-0.4,1.2",
                    help="synthetic lateral offsets (m), cycled per frame; ignored with --pose-csv")
    ap.add_argument("--pose-csv", help="external pose per frame (x,z,yaw_rad)")
    ap.add_argument("--start-s", type=float, default=2.0, dest="start_s")
    ap.add_argument("--step-m", type=float, default=0.5, dest="step_m")
    ap.add_argument("--yaw-error-deg", type=float, default=0.0, dest="yaw_error_deg")
    ap.add_argument("--height", type=float, default=1.2, help="camera height above the ground (m)")
    ap.add_argument("--pitch-deg", type=float, default=15.0, dest="pitch_deg")
    ap.add_argument("--fov-deg", type=float, default=70.0, dest="fov_deg")
    ap.add_argument("--fx", type=float, help="override focal length (px)")
    ap.add_argument("--mask-ok-frac", type=float, default=0.30, dest="mask_ok_frac",
                    help="walkable-pixel fraction in the forward ROI that counts as an open mask")
    ap.add_argument("--report-dir", default=None,
                    help="write CSV/JSON/Markdown eval outputs here instead of the smoke table")
    args = ap.parse_args(argv)

    if args.split not in SPLITS and args.split != "all":
        raise SystemExit(f"unknown split {args.split!r}; choose from {SPLITS} or 'all'")
    rows, corridor = _evaluate(args)
    if not rows:
        print("no frames selected", file=sys.stderr)
        return 2
    if args.report_dir:
        return _report(rows, corridor, args)
    return _smoke(rows, corridor, args)


if __name__ == "__main__":
    raise SystemExit(main())
