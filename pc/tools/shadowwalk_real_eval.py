#!/usr/bin/env python3
"""Offline ShadowWalk corridor eval on real segmentation masks (Blind-road smoke).

Each frame's real walkable mask (GT label map from ``pc/data/eval/blind_road_crosswalk``) is fed
through the same :class:`blindnav_pc.segmentation.Segmenter` interface used by the PC pipeline.
Because this smoke run has no depth network, the metric depth is the flat-ground plane implied by
the camera height and gravity direction; the external pose is synthetic unless ``--pose-csv`` is
given.  The tool prints one corridor hint per frame and never emits a "go" instruction.

    python tools/shadowwalk_real_eval.py --limit 6
    python tools/shadowwalk_real_eval.py --corridor left_arc80 --offsets 0,0.5,-0.5,1.2
    python tools/shadowwalk_real_eval.py --pose-csv poses.csv

``poses.csv`` columns: ``x,z,yaw_rad`` (one row per frame, metres/radians), optional first column
``frame`` to index explicitly.
"""
from __future__ import annotations

import argparse
import csv
import math
import os
import sys

import cv2
import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from blindnav_pc.ground import flat_ground_depth_map
from blindnav_pc.segmentation import PrecomputedMaskSegmenter, label_map_to_walkable
from blindnav_pc.shadowwalk import (PRESETS, Pose2D, ShadowWalkConfig, ShadowWalkTracker,
                                    get_corridor, load_corridor, mask_to_map_points)

DEFAULT_DATASET = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "data", "eval", "blind_road_crosswalk", "extracted",
    "Blind road and crosswalk dataset"))


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


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dataset", default=DEFAULT_DATASET, help="Blind-road dataset root")
    ap.add_argument("--split", default="test", choices=["train", "val", "test"])
    ap.add_argument("--limit", type=int, default=6)
    ap.add_argument("--offset", type=int, default=0, help="skip this many frames")
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
    args = ap.parse_args(argv)

    corridor = load_corridor(args.corridor_json) if args.corridor_json else get_corridor(args.corridor)
    cfg = ShadowWalkConfig()
    tracker = ShadowWalkTracker(corridor, cfg)
    offsets = [float(v) for v in args.offsets.split(",") if v.strip()]
    poses = _load_poses(args.pose_csv) if args.pose_csv else None
    pitch = math.radians(args.pitch_deg)
    up = np.array([0.0, -math.cos(pitch), -math.sin(pitch)])

    print(f"corridor={corridor.name} length={corridor.length_m:.1f}m width={corridor.width_m:.1f}m "
          f"source=real-label-mask depth=flat-ground pose={'csv' if poses else 'synthetic'}")
    print(f"{'frame':>10} {'s(m)':>5} {'off(m)':>7} {'yaw(deg)':>9} {'cover':>5} {'pts':>6}  "
          f"{'hint':<9} reason")
    counts: dict[str, int] = {}
    shown = 0
    for i, (stem, img_path, lbl_path) in enumerate(_frame_pairs(args.dataset, args.split)):
        if i < args.offset:
            continue
        if shown >= args.limit:
            break
        img = cv2.imread(img_path, cv2.IMREAD_COLOR)
        label = cv2.imread(lbl_path, cv2.IMREAD_COLOR)
        if img is None or label is None:
            print(f"{stem:>10}  skipped (unreadable image/label)")
            continue
        h, w = img.shape[:2]
        K = _camera_matrix(w, h, args.fov_deg, args.fx)
        mask = PrecomputedMaskSegmenter(label_map_to_walkable(label)).predict(img)
        depth = flat_ground_depth_map(K, w, h, up, args.height)
        if poses is not None:
            pose = poses.get(shown)
            if pose is None:
                print(f"{stem:>10}  skipped (no pose for frame {shown})")
                continue
        else:
            off = offsets[shown % len(offsets)] if offsets else 0.0
            pose = corridor.pose_at(args.start_s + shown * args.step_m, offset_m=off,
                                    yaw_error_rad=math.radians(args.yaw_error_deg))
        points = mask_to_map_points(mask, depth, K, up, args.height, pose, cfg)
        hint = tracker.update(points, pose)
        counts[hint.hint] = counts.get(hint.hint, 0) + 1
        print(f"{stem:>10} {hint.progress_m:>5.1f} {hint.offset_m:>7.2f} "
              f"{math.degrees(hint.yaw_error_rad):>9.1f} {hint.coverage:>5.2f} {hint.n_points:>6}  "
              f"{hint.hint:<9} {hint.reason}")
        shown += 1
    print(f"frames={shown} hints={counts}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
