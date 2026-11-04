#!/usr/bin/env python3
"""Last-10 m door proposal smoke demo against Entrances labels.

Parses each YOLO label file, estimates door range from the box bottom on a flat-ground plane,
and prints the proposal (at most three doors) or the refusal reason.  No model, no control.

    python tools/last10m_demo.py --limit 15
    python tools/last10m_demo.py --split test --range 10 --min-confidence 0.5
"""
from __future__ import annotations

import argparse
import math
import os
import sys
from collections import Counter

import numpy as np
from PIL import Image

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from blindnav_pc.last10m import (DEFAULT_RANGE_M, DoorCandidate, estimate_distance_m,
                                 load_entrances_labels, propose_doors)

DEFAULT_DATASET = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "data", "eval", "entrances", "extracted", "final_dataset"))


def _pairs(root: str, split: str):
    img_dir = os.path.join(root, split, "images")
    lbl_dir = os.path.join(root, split, "labels")
    if not os.path.isdir(img_dir) or not os.path.isdir(lbl_dir):
        raise SystemExit(f"missing split directories under {root!r}")
    labels = {os.path.splitext(n)[0]: os.path.join(lbl_dir, n) for n in os.listdir(lbl_dir)}
    for name in sorted(os.listdir(img_dir)):
        stem = os.path.splitext(name)[0]
        if stem in labels:
            yield stem, os.path.join(img_dir, name), labels[stem]


def _camera(width: int, height: int, fov_deg: float, fx: float | None = None) -> np.ndarray:
    f = float(fx) if fx else 0.5 * width / math.tan(math.radians(fov_deg) / 2.0)
    return np.array([[f, 0.0, width / 2.0], [0.0, f, height / 2.0], [0.0, 0.0, 1.0]])


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dataset", default=DEFAULT_DATASET)
    ap.add_argument("--split", default="val", choices=["train", "val", "test"])
    ap.add_argument("--limit", type=int, default=15)
    ap.add_argument("--range", type=float, default=DEFAULT_RANGE_M, dest="max_range_m")
    ap.add_argument("--min-confidence", type=float, default=0.5, dest="min_confidence")
    ap.add_argument("--height", type=float, default=1.2, help="camera height above the ground (m)")
    ap.add_argument("--pitch-deg", type=float, default=15.0, dest="pitch_deg")
    ap.add_argument("--fov-deg", type=float, default=70.0, dest="fov_deg")
    ap.add_argument("--fx", type=float)
    ap.add_argument("--require-distance", action="store_true", dest="require_distance",
                    help="refuse instead of keeping candidates whose ground range is unknown")
    args = ap.parse_args(argv)

    pitch = math.radians(args.pitch_deg)
    up = np.array([0.0, -math.cos(pitch), -math.sin(pitch)])
    print(f"dataset={args.dataset} split={args.split} range={args.max_range_m:.1f}m "
          f"min_conf={args.min_confidence}")
    print(f"{'frame':<38} {'parsed':>6} {'in10m':>5} {'pick':>4}  result")
    reasons: Counter = Counter()
    picked_counts: Counter = Counter()
    shown = 0
    for stem, img_path, lbl_path in _pairs(args.dataset, args.split):
        if shown >= args.limit:
            break
        width, height = Image.open(img_path).size
        K = _camera(width, height, args.fov_deg, args.fx)
        doors: list[DoorCandidate] = []
        for door in load_entrances_labels(lbl_path):
            doors.append(door.with_distance(
                estimate_distance_m(door, K, (width, height), up, args.height)))
        proposal = propose_doors(doors, max_distance_m=args.max_range_m,
                                 min_confidence=args.min_confidence,
                                 require_distance=args.require_distance)
        reasons[proposal.reason] += 1
        picked_counts[len(proposal.candidates)] += 1
        if proposal.refused:
            result = f"refused:{proposal.reason}"
        else:
            dists = ", ".join("?" if c.distance_m is None else f"{c.distance_m:.1f}m"
                              for c in proposal.candidates)
            result = f"pick[{dists}]"
        print(f"{stem:<38} {proposal.total_count:>6} {proposal.candidate_count:>5} "
              f"{len(proposal.candidates):>4}  {result}")
        shown += 1
    print(f"frames={shown} reasons={dict(reasons)} selected={dict(picked_counts)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
