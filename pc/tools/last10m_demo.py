#!/usr/bin/env python3
"""Last-10 m door proposal eval against Entrances labels.

Each YOLO label file is treated as GT doors.  Ranges are estimated from the box bottom on a
flat-ground plane (the same estimator the stub uses), the proposal is produced with
:func:`blindnav_pc.last10m.propose_doors`, and the run records recall/refusal behaviour.

Definitions used in the report:

* **frame recall (all GT)** = frames with >=1 GT door that receive >=1 proposal, over all frames
  with >=1 GT door.  This penalises correct refusals of doors that are truly out of range.
* **frame recall (in-range GT)** = same, restricted to frames with >=1 GT door whose estimated
  range is <= ``--range``.  This is the headline recall.
* **box recall (in-range)** = proposed in-range boxes / all in-range GT boxes.

GT range comes from the same flat-ground estimator, because Entrances ships no distance labels;
that circularity is stated in the report.  Label files carry no detector confidence, so every
parsed box has confidence 1.0 and ``low_confidence`` can never fire on this dataset.

Smoke mode (default)::

    python tools/last10m_demo.py --limit 15
    python tools/last10m_demo.py --split test --range 10 --min-confidence 0.5

Report mode::

    python tools/last10m_demo.py --split all --limit 0 --report-dir out/entrances_last10m
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import sys
from collections import Counter
from typing import Dict, Iterable, List, Sequence, Tuple

import numpy as np
from PIL import Image

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from blindnav_pc.last10m import (DEFAULT_RANGE_M, DoorCandidate, estimate_distance_m,
                                 load_entrances_labels, propose_doors)

DEFAULT_DATASET = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "data", "eval", "entrances", "extracted", "final_dataset"))
SPLITS = ("train", "val", "test")
REASONS = ("ok", "no_candidate", "low_confidence", "distance_unknown", "out_of_range", "ambiguous")
RANGE_SWEEP = (5.0, 10.0, 15.0, 20.0)


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


def _percentiles(values: Sequence[float], points=(5, 25, 50, 75, 95)) -> Dict[str, float]:
    if not values:
        return {}
    arr = np.asarray(values, dtype=np.float64)
    return {str(p): round(float(np.percentile(arr, p)), 3) for p in points}


def _evaluate_frame(split: str, stem: str, img_path: str, lbl_path: str,
                    args, up: np.ndarray) -> Tuple[dict, List[dict], List[DoorCandidate]]:
    with Image.open(img_path) as image:
        width, height = image.size
    K = _camera(width, height, args.fov_deg, args.fx)
    doors = [door.with_distance(estimate_distance_m(door, K, (width, height), up, args.height))
             for door in load_entrances_labels(lbl_path)]
    proposal = propose_doors(doors, max_distance_m=args.max_range_m,
                             min_confidence=args.min_confidence,
                             require_distance=not args.allow_unknown_distance)
    picked = {id(candidate) for candidate in proposal.candidates}

    box_rows: List[dict] = []
    known = []
    in_range = 0
    unknown = 0
    for index, door in enumerate(doors):
        distance = door.distance_m
        if distance is None:
            unknown += 1
            is_in_range = None
        else:
            known.append(distance)
            is_in_range = distance <= args.max_range_m
            in_range += int(is_in_range)
        box_rows.append({
            "split": split, "frame": stem, "box_index": index, "class_id": door.class_id,
            "cx": round(door.cx, 6), "cy": round(door.cy, 6), "w": round(door.w, 6),
            "h": round(door.h, 6), "confidence": door.confidence,
            "distance_m": None if distance is None else round(distance, 4),
            "in_range": is_in_range, "eligible": door.confidence >= args.min_confidence,
            "proposed": id(door) in picked,
        })

    frame_row = {
        "split": split,
        "frame": stem,
        "width": width,
        "height": height,
        "gt_boxes": len(doors),
        "gt_in_range": in_range,
        "unknown_range": unknown,
        "parsed_count": proposal.total_count,
        "eligible_count": proposal.candidate_count,
        "picked_count": len(proposal.candidates),
        "proposed_in_range": sum(1 for c in proposal.candidates if c.distance_m is not None
                                 and c.distance_m <= args.max_range_m),
        "refused": proposal.refused,
        "reason": proposal.reason,
        "min_distance_m": round(min(known), 4) if known else None,
        "max_distance_m": round(max(known), 4) if known else None,
    }
    return frame_row, box_rows, doors


def _aggregate(frame_rows: Sequence[dict], box_rows: Sequence[dict], max_range: float) -> dict:
    total_frames = len(frame_rows)
    has_gt = [f for f in frame_rows if f["gt_boxes"] > 0]
    has_in_range = [f for f in frame_rows if f["gt_in_range"] > 0]
    empty = [f for f in frame_rows if f["gt_boxes"] == 0]
    proposed_frames = [f for f in frame_rows if not f["refused"]]

    total_boxes = len(box_rows)
    in_range_boxes = [b for b in box_rows if b["in_range"] is True]
    proposed_boxes = [b for b in box_rows if b["proposed"]]
    proposed_in_range = [b for b in in_range_boxes if b["proposed"]]

    refusal_reasons = Counter(f["reason"] for f in frame_rows if f["refused"])
    all_reasons = Counter(f["reason"] for f in frame_rows)
    proposed_dist = Counter(f["picked_count"] for f in frame_rows)
    eligible_dist = Counter(f["eligible_count"] for f in frame_rows)

    def ratio(num, den):
        return num / den if den else None

    return {
        "frames": total_frames,
        "frames_with_gt": len(has_gt),
        "frames_with_in_range_gt": len(has_in_range),
        "empty_label_frames": len(empty),
        "frames_proposed": len(proposed_frames),
        "frame_recall_all_gt": ratio(sum(1 for f in has_gt if not f["refused"]), len(has_gt)),
        "frame_recall_in_range_gt": ratio(sum(1 for f in has_in_range if not f["refused"]),
                                          len(has_in_range)),
        "frame_recall_all_gt_counts": [sum(1 for f in has_gt if not f["refused"]), len(has_gt)],
        "frame_recall_in_range_gt_counts": [sum(1 for f in has_in_range if not f["refused"]),
                                            len(has_in_range)],
        "boxes": total_boxes,
        "boxes_in_range": len(in_range_boxes),
        "boxes_unknown_range": sum(1 for b in box_rows if b["distance_m"] is None),
        "box_recall_all": ratio(len(proposed_boxes), total_boxes),
        "box_recall_in_range": ratio(len(proposed_in_range), len(in_range_boxes)),
        "box_recall_all_counts": [len(proposed_boxes), total_boxes],
        "box_recall_in_range_counts": [len(proposed_in_range), len(in_range_boxes)],
        "refused_frames": sum(1 for f in frame_rows if f["refused"]),
        "refuse_rate": ratio(sum(1 for f in frame_rows if f["refused"]), total_frames),
        "refuse_rate_with_gt": ratio(sum(1 for f in has_gt if f["refused"]), len(has_gt)),
        "refuse_rate_with_in_range_gt": ratio(sum(1 for f in has_in_range if f["refused"]),
                                              len(has_in_range)),
        "false_proposals_on_empty": sum(1 for f in empty if not f["refused"]),
        "refusal_reasons": {reason: refusal_reasons.get(reason, 0) for reason in REASONS},
        "all_reasons": {reason: all_reasons.get(reason, 0) for reason in REASONS},
        "proposed_count_distribution": {str(k): proposed_dist.get(k, 0)
                                        for k in range(4)},
        "eligible_count_distribution": {str(k): eligible_dist.get(k, 0)
                                        for k in sorted(eligible_dist)},
        "range_percentiles_m": _percentiles([b["distance_m"] for b in box_rows
                                             if b["distance_m"] is not None]),
        "in_range_range_percentiles_m": _percentiles([b["distance_m"] for b in in_range_boxes]),
    }


def _range_sweep(frame_doors: Sequence[Tuple[dict, List[DoorCandidate]]], args) -> List[dict]:
    """Re-run selection at several max ranges using the cached per-frame distances."""
    out = []
    for max_range in RANGE_SWEEP:
        frame_rows = []
        box_rows = []
        for base, doors in frame_doors:
            proposal = propose_doors(doors, max_distance_m=max_range,
                                     min_confidence=args.min_confidence,
                                     require_distance=not args.allow_unknown_distance)
            picked = {id(c) for c in proposal.candidates}
            in_range = 0
            for index, door in enumerate(doors):
                is_in_range = (None if door.distance_m is None
                               else door.distance_m <= max_range)
                in_range += int(bool(is_in_range))
                box_rows.append({"split": base["split"], "frame": base["frame"],
                                 "distance_m": door.distance_m,
                                 "in_range": is_in_range,
                                 "proposed": id(door) in picked})
            row = dict(base)
            row.update({"gt_in_range": in_range, "picked_count": len(proposal.candidates),
                        "refused": proposal.refused, "reason": proposal.reason})
            frame_rows.append(row)
        agg = _aggregate(frame_rows, box_rows, max_range)
        out.append({
            "max_range_m": max_range,
            "frame_recall_in_range_gt": agg["frame_recall_in_range_gt"],
            "frame_recall_in_range_gt_counts": agg["frame_recall_in_range_gt_counts"],
            "box_recall_in_range": agg["box_recall_in_range"],
            "refuse_rate": agg["refuse_rate"],
            "refusal_reasons": agg["refusal_reasons"],
        })
    return out


def _write_csv(path: str, fieldnames: Sequence[str], rows: Iterable[dict]) -> None:
    with open(path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _by_split(frame_rows: Sequence[dict], box_rows: Sequence[dict], max_range: float) -> Dict[str, dict]:
    out = {}
    for split in sorted({f["split"] for f in frame_rows}):
        frames = [f for f in frame_rows if f["split"] == split]
        boxes = [b for b in box_rows if b["split"] == split]
        out[split] = _aggregate(frames, boxes, max_range)
    return out


def _report(frame_rows: List[dict], box_rows: List[dict],
            frame_doors: List[Tuple[dict, List[DoorCandidate]]], args) -> int:
    report_dir = os.path.abspath(args.report_dir)
    os.makedirs(report_dir, exist_ok=True)
    aggregate = _aggregate(frame_rows, box_rows, args.max_range_m)
    sweep = _range_sweep(frame_doors, args)
    by_split = _by_split(frame_rows, box_rows, args.max_range_m)

    summary = {
        "inputs": {
            "dataset": os.path.abspath(args.dataset),
            "splits": sorted({f["split"] for f in frame_rows}),
            "max_range_m": args.max_range_m,
            "min_confidence": args.min_confidence,
            "camera_height_m": args.height,
            "pitch_deg": args.pitch_deg,
            "fov_deg": args.fov_deg,
            "require_distance": not args.allow_unknown_distance,
        },
        "metrics": aggregate,
        "range_sweep": sweep,
        "by_split": by_split,
        "definitions": {
            "frame_recall_all_gt": "frames with >=1 GT door that get >=1 proposal / frames with >=1 GT door",
            "frame_recall_in_range_gt": "same, restricted to frames whose GT door range estimate is <= max_range",
            "box_recall_in_range": "proposed in-range boxes / all in-range GT boxes",
            "gt_range_source": "flat-ground estimate from the box bottom; Entrances has no distance labels",
            "confidence_source": "YOLO labels have no confidence, so all boxes are confidence 1.0",
        },
        "notes": [
            "low_confidence cannot fire because labels carry no detector confidence",
            "distance_unknown cannot fire on this dataset because every parsed box has a finite flat-ground estimate",
        ],
    }
    with open(os.path.join(report_dir, "summary.json"), "w", encoding="utf-8") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=2)

    _write_csv(os.path.join(report_dir, "per_frame.csv"),
               ["split", "frame", "width", "height", "gt_boxes", "gt_in_range", "unknown_range",
                "parsed_count", "eligible_count", "picked_count", "proposed_in_range",
                "refused", "reason", "min_distance_m", "max_distance_m"], frame_rows)
    _write_csv(os.path.join(report_dir, "per_box.csv"),
               ["split", "frame", "box_index", "class_id", "cx", "cy", "w", "h", "confidence",
                "distance_m", "in_range", "eligible", "proposed"], box_rows)
    _write_csv(os.path.join(report_dir, "refusal_reasons.csv"),
               ["reason", "refused_frames", "all_frames"],
               [{"reason": reason,
                 "refused_frames": aggregate["refusal_reasons"][reason],
                 "all_frames": aggregate["all_reasons"][reason]} for reason in REASONS])
    _write_csv(os.path.join(report_dir, "candidate_counts.csv"),
               ["kind", "count", "frames"],
               [{"kind": "proposed", "count": int(k), "frames": v}
                for k, v in aggregate["proposed_count_distribution"].items()] +
               [{"kind": "eligible", "count": int(k), "frames": v}
                for k, v in aggregate["eligible_count_distribution"].items()])
    _write_csv(os.path.join(report_dir, "range_sweep.csv"),
               ["max_range_m", "frame_recall_in_range_gt", "box_recall_in_range", "refuse_rate"],
               [{**item, "frame_recall_in_range_gt": item["frame_recall_in_range_gt"]}
                for item in sweep])

    def pct(value):
        return "n/a" if value is None else f"{100.0 * value:.2f}%"

    m = aggregate
    lines = [
        "# Entrances Last-10 m door proposal eval",
        "",
        "The stub parses GT YOLO door boxes, estimates range from the box bottom on a flat-ground",
        "plane, keeps boxes within the last `max_range`, ranks them and proposes at most three.",
        "It refuses rather than guessing when its reliability rules fail.",
        "",
        "## Inputs",
        "",
        f"- Dataset: `{os.path.abspath(args.dataset)}`",
        f"- Splits: {', '.join(sorted({f['split'] for f in frame_rows}))}",
        f"- Frames: {m['frames']} (with >=1 GT door: {m['frames_with_gt']}; "
        f"with an in-range GT door: {m['frames_with_in_range_gt']}; empty labels: {m['empty_label_frames']})",
        f"- GT boxes: {m['boxes']} (in range: {m['boxes_in_range']}; unknown range: {m['boxes_unknown_range']})",
        f"- Config: max_range={args.max_range_m:g} m, min_confidence={args.min_confidence:g}, "
        f"require_distance={not args.allow_unknown_distance}",
        "",
        "## Recall",
        "",
        "| metric | definition | value | numerator / denominator |",
        "|---|---|---:|---:|",
        f"| frame recall (in-range GT) | frames with an in-range GT door that get >=1 proposal | "
        f"**{pct(m['frame_recall_in_range_gt'])}** | "
        f"{m['frame_recall_in_range_gt_counts'][0]}/{m['frame_recall_in_range_gt_counts'][1]} |",
        f"| frame recall (all GT) | frames with any GT door that get >=1 proposal | "
        f"{pct(m['frame_recall_all_gt'])} | "
        f"{m['frame_recall_all_gt_counts'][0]}/{m['frame_recall_all_gt_counts'][1]} |",
        f"| box recall (in-range) | proposed in-range boxes / in-range GT boxes | "
        f"{pct(m['box_recall_in_range'])} | "
        f"{m['box_recall_in_range_counts'][0]}/{m['box_recall_in_range_counts'][1]} |",
        "",
        "GT range is the same flat-ground estimate the stub uses (Entrances has no distance",
        "labels), so `in-range` is a proxy, not an independent annotation.",
        "",
        "## Refusals",
        "",
        f"- Refused frames: {m['refused_frames']}/{m['frames']} ({pct(m['refuse_rate'])})",
        f"- Refuse rate on frames with GT doors: {pct(m['refuse_rate_with_gt'])}",
        f"- Refuse rate on frames with in-range GT doors: {pct(m['refuse_rate_with_in_range_gt'])}",
        f"- False proposals on empty-label frames: {m['false_proposals_on_empty']}",
        "",
        "| reason | frames | of which refused |",
        "|---|---:|---:|",
    ]
    for reason in REASONS:
        lines.append(f"| {reason} | {m['all_reasons'][reason]} | {m['refusal_reasons'][reason]} |")
    lines += [
        "",
        "`ok` is a non-refusal reason, so its refused count is zero by definition.",
        "",
        "Frames with GT doors that were refused:",
        "",
        "| split | frame | reason | GT boxes | in range | range (m) |",
        "|---|---|---|---:|---:|---|",
    ]
    refused_with_gt = [row for row in frame_rows if row["refused"] and row["gt_boxes"] > 0]
    for row in refused_with_gt:
        lines.append(f"| {row['split']} | {row['frame']} | {row['reason']} | {row['gt_boxes']} | "
                     f"{row['gt_in_range']} | {row['min_distance_m']}-{row['max_distance_m']} |")
    if not refused_with_gt:
        lines.append("| - | - | - | - | - | - |")
    lines += [
        "",
        "## Candidate-count distribution",
        "",
        "| proposed candidates | frames |",
        "|---|---:|",
    ]
    for count, frames in m["proposed_count_distribution"].items():
        lines.append(f"| {count} | {frames} |")
    lines += [
        "",
        "Eligible (in-range, confidence-passing) candidates before the 3-cap:",
        "",
        "| eligible candidates | frames |",
        "|---|---:|",
    ]
    for count, frames in m["eligible_count_distribution"].items():
        lines.append(f"| {count} | {frames} |")
    lines += [
        "",
        "## Estimated range (metres)",
        "",
        "| split of boxes | p5 | p25 | p50 | p75 | p95 |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for label, values in (("all boxes", m["range_percentiles_m"]),
                          ("in-range boxes", m["in_range_range_percentiles_m"])):
        lines.append(f"| {label} | " + " | ".join(str(values.get(p, "n/a"))
                                                  for p in ("5", "25", "50", "75", "95")) + " |")
    lines += [
        "",
        "## Range sensitivity",
        "",
        "| max_range (m) | frame recall (in-range GT) | box recall (in-range) | refuse rate | refusals |",
        "|---:|---:|---:|---:|---|",
    ]
    for item in sweep:
        lines.append(f"| {item['max_range_m']:g} | {pct(item['frame_recall_in_range_gt'])} | "
                     f"{pct(item['box_recall_in_range'])} | {pct(item['refuse_rate'])} | "
                     f"{item['refusal_reasons']} |")
    lines += [
        "",
        "## Per split",
        "",
        "| split | frames | with GT | in-range GT | frame recall (in-range) | refuse rate |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for split, item in by_split.items():
        lines.append(f"| {split} | {item['frames']} | {item['frames_with_gt']} | "
                     f"{item['frames_with_in_range_gt']} | "
                     f"{pct(item['frame_recall_in_range_gt'])} | {pct(item['refuse_rate'])} |")
    lines += [
        "",
        "## Caveats",
        "",
        "- Range is a flat-ground estimate, not a labelled distance.  The `in-range` subsets are",
        "  therefore defined by the same model under test; treat them as a consistency check.",
        "- Entrances labels carry no detector confidence, so the stub sees every box at 1.0.",
        "  `low_confidence` and `distance_unknown` refusals are untestable on this dataset.",
        "- `ambiguous` fires only when >3 in-range boxes have a near-tie at the 3rd/4th cut-off.",
        "",
    ]
    with open(os.path.join(report_dir, "summary.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))

    print(f"report: {report_dir}")
    print(f"frames={m['frames']} with_gt={m['frames_with_gt']} "
          f"frame_recall_in_range={m['frame_recall_in_range_gt']} "
          f"refuse_rate={m['refuse_rate']} reasons={m['refusal_reasons']}")
    return 0


def _smoke(frame_rows: List[dict], args) -> int:
    print(f"dataset={args.dataset} split={args.split} range={args.max_range_m:.1f}m "
          f"min_conf={args.min_confidence}")
    print(f"{'frame':<38} {'parsed':>6} {'in10m':>5} {'pick':>4}  result")
    for row in frame_rows:
        if row["refused"]:
            result = f"refused:{row['reason']}"
        else:
            result = f"pick[{row['picked_count']}]"
        print(f"{row['frame']:<38} {row['parsed_count']:>6} {row['gt_in_range']:>5} "
              f"{row['picked_count']:>4}  {result}")
    reasons = Counter(row["reason"] for row in frame_rows)
    picked = Counter(row["picked_count"] for row in frame_rows)
    print(f"frames={len(frame_rows)} reasons={dict(reasons)} selected={dict(sorted(picked.items()))}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dataset", default=DEFAULT_DATASET)
    ap.add_argument("--split", default="val", help="train|val|test|all (default: val)")
    ap.add_argument("--limit", type=int, default=15, help="0 = all frames")
    ap.add_argument("--range", type=float, default=DEFAULT_RANGE_M, dest="max_range_m")
    ap.add_argument("--min-confidence", type=float, default=0.5, dest="min_confidence")
    ap.add_argument("--height", type=float, default=1.2, help="camera height above the ground (m)")
    ap.add_argument("--pitch-deg", type=float, default=15.0, dest="pitch_deg")
    ap.add_argument("--fov-deg", type=float, default=70.0, dest="fov_deg")
    ap.add_argument("--fx", type=float)
    ap.add_argument("--allow-unknown-distance", action="store_true", dest="allow_unknown_distance",
                    help="keep candidates whose ground range is unknown instead of refusing")
    ap.add_argument("--report-dir", default=None,
                    help="write CSV/JSON/Markdown eval outputs here instead of the smoke table")
    args = ap.parse_args(argv)

    splits = list(SPLITS) if args.split == "all" else [args.split]
    for split in splits:
        if split not in SPLITS:
            raise SystemExit(f"unknown split {split!r}; choose from {SPLITS} or 'all'")

    pitch = math.radians(args.pitch_deg)
    up = np.array([0.0, -math.cos(pitch), -math.sin(pitch)])
    frame_rows: List[dict] = []
    box_rows: List[dict] = []
    frame_doors: List[Tuple[dict, List[DoorCandidate]]] = []
    for split in splits:
        for stem, img_path, lbl_path in _pairs(args.dataset, split):
            if args.limit > 0 and len(frame_rows) >= args.limit:
                break
            row, boxes, doors = _evaluate_frame(split, stem, img_path, lbl_path, args, up)
            frame_rows.append(row)
            box_rows.extend(boxes)
            frame_doors.append((row, doors))
        if args.limit > 0 and len(frame_rows) >= args.limit:
            break

    if not frame_rows:
        print("no frames selected", file=sys.stderr)
        return 2
    if args.report_dir:
        return _report(frame_rows, box_rows, frame_doors, args)
    return _smoke(frame_rows, args)


if __name__ == "__main__":
    raise SystemExit(main())
