#!/usr/bin/env python3
"""Traffic-light stub eval against ImVisible labels (no model, no pixels needed).

Two things are measured:

* **label mapping** -- every ImVisible ``mode`` (0..4) is mapped once to one of the three allowed
  states (green / red / uncertain) and compared with the same three-state rubric used as GT.  This
  is exact by construction; it is reported to make the rubric explicit, not as model accuracy.
* **confirmation filter** -- the stub's actual per-frame output.  Rows are grouped into
  pseudo-sequences by filename series and image index (the CSV does not carry a verified frame
  order), then replayed through :class:`TrafficLightFilter`.  This is where the non-trivial numbers
  live: the filter abstains during warm-up and after any uncertain/low-confidence observation.

Smoke mode (default) prints rows and a confirmation demo::

    python tools/traffic_light_demo.py --limit 12
    python tools/traffic_light_demo.py --split validation --limit 0

Report mode writes ``per_frame.csv``, confusion matrices, a confirmation-window sweep,
``summary.json`` and ``summary.md``::

    python tools/traffic_light_demo.py --split validation,testing --limit 0 \
        --report-dir out/imvisible_traffic_light
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from collections import Counter, defaultdict
from typing import Dict, Iterable, List, Sequence, Tuple

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from blindnav_pc.traffic_light import (IMVISIBLE_MODE_TO_STATE, STATES, TrafficLightFilter,
                                       imvisible_mode_to_state, imvisible_row_to_observation,
                                       read_imvisible_rows)

DEFAULT_LABELS = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "data", "eval", "imvisible", "testing_file.csv"))
DEFAULT_IMAGES = os.path.abspath(os.path.join(
    os.path.dirname(DEFAULT_LABELS), "extracted", "PTL_Dataset_876x657"))
SPLITS = ("training", "validation", "testing")
MODE_STATE_RUBRIC = dict(IMVISIBLE_MODE_TO_STATE)


def _series_key(filename: str) -> Tuple[str, int]:
    """Group label files that look like the same capture series; sort by the first numeric id."""
    stem = os.path.splitext(os.path.basename(str(filename)))[0]
    match = re.match(r"^(.*?)(\d+)", stem)
    if not match:
        return stem, 0
    return match.group(1), int(match.group(2))


def _resolve_label_files(args) -> List[Tuple[str, str]]:
    base_dir = os.path.dirname(os.path.abspath(args.labels))
    if not args.split:
        return [("custom", os.path.abspath(args.labels))]
    names = list(SPLITS) if args.split == "all" else [s.strip() for s in args.split.split(",") if s.strip()]
    out = []
    for name in names:
        if name not in SPLITS:
            raise SystemExit(f"unknown split {name!r}; choose from {SPLITS} or 'all'")
        out.append((name, os.path.join(base_dir, f"{name}_file.csv")))
    return out


def _load_rows(label_files: Sequence[Tuple[str, str]], limit: int) -> List[dict]:
    rows: List[dict] = []
    for split, path in label_files:
        if not os.path.isfile(path):
            raise SystemExit(f"labels not found: {path}")
        for row in read_imvisible_rows(path):
            item = dict(row)
            item["split"] = split
            rows.append(item)
    return rows[:limit] if limit > 0 else rows


def _sequences(rows: Iterable[dict]) -> List[List[dict]]:
    """Pseudo-sequences: same split + filename series, sorted by the first numeric image id."""
    groups: Dict[Tuple[str, str], List[Tuple[int, dict]]] = defaultdict(list)
    for row in rows:
        series, index = _series_key(row.get("file", ""))
        groups[(row["split"], series)].append((index, row))
    sequences = []
    for key in sorted(groups):
        sequences.append([row for _, row in sorted(groups[key], key=lambda item: item[0])])
    return sequences


def _empty_matrix() -> Dict[str, Dict[str, int]]:
    return {gt: {pred: 0 for pred in STATES} for gt in STATES}


def _confusion(pairs: Iterable[Tuple[str, str]]) -> Dict[str, Dict[str, int]]:
    matrix = _empty_matrix()
    for gt, pred in pairs:
        matrix[gt][pred] += 1
    return matrix


def _accuracy(matrix: Dict[str, Dict[str, int]]) -> float:
    total = sum(sum(row.values()) for row in matrix.values())
    if not total:
        return 0.0
    return sum(matrix[state][state] for state in STATES) / total


def _per_class(matrix: Dict[str, Dict[str, int]]) -> Dict[str, dict]:
    out = {}
    for state in STATES:
        tp = matrix[state][state]
        support = sum(matrix[state].values())
        predicted = sum(matrix[gt][state] for gt in STATES)
        precision = tp / predicted if predicted else None
        recall = tp / support if support else None
        f1 = (2 * precision * recall / (precision + recall)
              if precision is not None and recall is not None and (precision + recall) else None)
        out[state] = {"support": support, "predicted": predicted, "true_positive": tp,
                      "precision": precision, "recall": recall, "f1": f1}
    return out


def _simulate(sequences: Sequence[Sequence[dict]], min_confirm: int,
              min_confidence: float) -> List[Tuple[dict, object]]:
    """Replay each pseudo-sequence through a fresh filter; return (row, decision) pairs."""
    out: List[Tuple[dict, object]] = []
    for sequence in sequences:
        filt = TrafficLightFilter(min_confirm_frames=min_confirm,
                                  min_confidence=min_confidence)
        for row in sequence:
            decision = filt.update(imvisible_row_to_observation(row))
            out.append((row, decision))
    return out


def _filtered_metrics(pairs: Sequence[Tuple[dict, object]], min_confirm: int,
                      min_confidence: float) -> dict:
    matrix = _confusion((row["gt_state"], decision.state) for row, decision in pairs)
    total = len(pairs)
    unsafe_green = sum(matrix[gt]["green"] for gt in STATES if gt != "green")
    abstained = sum(matrix[gt]["uncertain"] for gt in STATES if gt != "uncertain")
    stable = sum(1 for _, decision in pairs if decision.stable)
    return {
        "min_confirm": min_confirm,
        "min_confidence": min_confidence,
        "frames": total,
        "accuracy": _accuracy(matrix),
        "confusion": matrix,
        "per_class": _per_class(matrix),
        "unsafe_green": unsafe_green,
        "conservative_abstention": abstained,
        "abstention_rate": abstained / total if total else None,
        "stable_rate": stable / total if total else None,
    }


def _write_csv(path: str, fieldnames: Sequence[str], rows: Iterable[dict]) -> None:
    with open(path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _matrix_rows(matrix: Dict[str, Dict[str, int]], row_label: str) -> List[dict]:
    rows = []
    for gt in STATES:
        for pred in STATES:
            rows.append({row_label: gt, "predicted": pred, "count": matrix[gt][pred]})
    return rows


def _report(rows: List[dict], args) -> int:
    report_dir = os.path.abspath(args.report_dir)
    os.makedirs(report_dir, exist_ok=True)

    for row in rows:
        row["gt_state"] = imvisible_mode_to_state(row.get("mode"))
        row["raw_state"] = imvisible_mode_to_state(row.get("mode"))
    images_root = os.path.abspath(args.images)
    for row in rows:
        row["image_present"] = os.path.isfile(os.path.join(images_root, str(row.get("file", ""))))

    sequences = _sequences(rows)
    filtered = _simulate(sequences, args.min_confirm, args.min_confidence)
    filtered_rows = []
    for row, decision in filtered:
        item = dict(row)
        item["filtered_state"] = decision.state
        item["filter_reason"] = decision.reason
        item["filter_frames"] = decision.frames
        item["stable"] = decision.stable
        item["requires_user_confirmation"] = decision.requires_user_confirmation
        filtered_rows.append(item)

    raw_matrix = _confusion((row["gt_state"], row["raw_state"]) for row in rows)
    raw_accuracy = _accuracy(raw_matrix)
    filtered_metrics = _filtered_metrics(filtered, args.min_confirm, args.min_confidence)
    split_counts = Counter(row["split"] for row in rows)
    per_split_filtered = {}
    for split in sorted(split_counts):
        pairs = [(row, decision) for row, decision in filtered if row["split"] == split]
        per_split_filtered[split] = _filtered_metrics(pairs, args.min_confirm, args.min_confidence)

    mode_matrix = {str(mode): {state: 0 for state in STATES} for mode in MODE_STATE_RUBRIC}
    mode_counts: Counter = Counter()
    for row in rows:
        mode = str(row.get("mode", ""))
        mode_counts[mode] += 1
        if mode in mode_matrix:
            mode_matrix[mode][row["raw_state"]] += 1

    sweep = []
    for k in [int(v) for v in args.sweep.split(",") if v.strip()]:
        pairs = _simulate(sequences, k, args.min_confidence)
        metrics = _filtered_metrics(pairs, k, args.min_confidence)
        sweep.append(metrics)

    image_missing = sum(1 for row in rows if not row["image_present"])
    sequence_lengths = Counter(len(seq) for seq in sequences)

    summary = {
        "inputs": {
            "splits": sorted(split_counts),
            "rows": len(rows),
            "rows_by_split": dict(sorted(split_counts.items())),
            "label_files": [path for _, path in _resolve_label_files(args)],
            "images_root": images_root,
            "images_present": len(rows) - image_missing,
            "images_missing": image_missing,
        },
        "sequences": {
            "count": len(sequences),
            "length_histogram": {str(k): v for k, v in sorted(sequence_lengths.items())},
            "note": ("grouped by split + filename series and sorted by the first numeric image id; "
                     "ImVisible CSVs do not certify contiguous video order, so filter numbers are "
                     "an order-proxy stress test, not deployment accuracy"),
        },
        "label_mapping": {
            "states": list(STATES),
            "mode_to_state": {str(k): v for k, v in MODE_STATE_RUBRIC.items()},
            "mode_counts": dict(sorted(mode_counts.items())),
            "mode_state_confusion": mode_matrix,
            "accuracy": raw_accuracy,
            "confusion": raw_matrix,
        },
        "filtered": filtered_metrics,
        "filtered_by_split": per_split_filtered,
        "confirmation_sweep": sweep,
        "invariants": {"auto_go": False, "green_requires_user_confirmation": True},
    }

    with open(os.path.join(report_dir, "summary.json"), "w", encoding="utf-8") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=2)

    _write_csv(os.path.join(report_dir, "per_frame.csv"),
               ["split", "file", "mode", "block", "gt_state", "raw_state", "filtered_state",
                "filter_reason", "filter_frames", "stable", "image_present", "requires_user_confirmation"],
               filtered_rows)
    _write_csv(os.path.join(report_dir, "confusion_filtered.csv"),
               ["gt_state", "predicted", "count"], _matrix_rows(filtered_metrics["confusion"], "gt_state"))
    _write_csv(os.path.join(report_dir, "confusion_raw.csv"),
               ["gt_state", "predicted", "count"], _matrix_rows(raw_matrix, "gt_state"))
    _write_csv(os.path.join(report_dir, "confusion_mode_to_state.csv"),
               ["mode", "state", "count"],
               [{"mode": mode, "state": state, "count": count}
                for mode, states in mode_matrix.items() for state, count in states.items()])
    _write_csv(os.path.join(report_dir, "per_class_filtered.csv"),
               ["state", "support", "predicted", "true_positive", "precision", "recall", "f1"],
               [{"state": state, **metrics} for state, metrics in filtered_metrics["per_class"].items()])
    _write_csv(os.path.join(report_dir, "confirmation_sweep.csv"),
               ["min_confirm", "frames", "accuracy", "stable_rate", "abstention_rate",
                "unsafe_green", "green_recall", "red_recall", "uncertain_recall"],
               [{"min_confirm": item["min_confirm"], "frames": item["frames"],
                 "accuracy": item["accuracy"], "stable_rate": item["stable_rate"],
                 "abstention_rate": item["abstention_rate"], "unsafe_green": item["unsafe_green"],
                 "green_recall": item["per_class"]["green"]["recall"],
                 "red_recall": item["per_class"]["red"]["recall"],
                 "uncertain_recall": item["per_class"]["uncertain"]["recall"]}
                for item in sweep])

    def pct(value):
        return "n/a" if value is None else f"{100.0 * value:.2f}%"

    lines = [
        "# ImVisible traffic-light three-state eval",
        "",
        "The stub has **no pixel model**: `mode` is mapped to green/red/uncertain, and the only",
        "decision logic is the consecutive-frame confirmation filter. Raw mapping accuracy is",
        "therefore 100% by construction; the filtered numbers measure the stub's operational",
        "per-frame output on a pseudo-temporal replay of the labels.",
        "",
        "## Inputs",
        "",
        f"- Splits: {', '.join(sorted(split_counts))}",
        f"- Rows: {len(rows)} ({', '.join(f'{k}={v}' for k, v in sorted(split_counts.items()))})",
        f"- Images found: {len(rows) - image_missing}/{len(rows)} under `{images_root}`",
        f"- Pseudo-sequences: {len(sequences)} "
        f"(length histogram {dict(sorted(sequence_lengths.items()))})",
        "",
        "## Three-state rubric (GT = same collapse)",
        "",
        "| ImVisible mode | meaning | state | rows |",
        "|---:|---|---|---:|",
        "| 0 | red | red | " + str(mode_counts.get("0", 0)) + " |",
        "| 1 | green | green | " + str(mode_counts.get("1", 0)) + " |",
        "| 2 | green countdown | green | " + str(mode_counts.get("2", 0)) + " |",
        "| 3 | countdown blank | uncertain | " + str(mode_counts.get("3", 0)) + " |",
        "| 4 | no light | uncertain | " + str(mode_counts.get("4", 0)) + " |",
        "",
        f"Raw mapping fidelity: **{raw_accuracy:.4f}** "
        f"(measured; exact by construction).",
        "",
        "Mode-level collapse (rows = GT mode, columns = predicted state):",
        "",
        "| mode | green | red | uncertain |",
        "|---:|---:|---:|---:|",
    ]
    for mode in sorted(mode_matrix, key=int):
        lines.append(f"| {mode} | " + " | ".join(str(mode_matrix[mode][state])
                                                  for state in STATES) + " |")
    lines += [
        "",
        f"## Confirmation filter (min_confirm={args.min_confirm}, "
        f"min_confidence={args.min_confidence})",
        "",
        f"- Frames: {filtered_metrics['frames']}",
        f"- Accuracy: **{filtered_metrics['accuracy']:.4f}**",
        f"- Stable (confirmed) rate: {pct(filtered_metrics['stable_rate'])}",
        f"- Conservative abstention (GT green/red -> uncertain): "
        f"{pct(filtered_metrics['abstention_rate'])}",
        f"- Unsafe green (GT red/uncertain -> green): **{filtered_metrics['unsafe_green']}**",
        "",
        "Confusion matrix (rows = GT, columns = predicted):",
        "",
        "| GT \\ pred | green | red | uncertain |",
        "|---|---:|---:|---:|",
    ]
    for gt in STATES:
        lines.append(f"| {gt} | " + " | ".join(str(filtered_metrics['confusion'][gt][p])
                                                for p in STATES) + " |")
    lines += [
        "",
        "Per-class:",
        "",
        "| state | support | predicted | precision | recall | f1 |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for state, metrics in filtered_metrics["per_class"].items():
        lines.append(f"| {state} | {metrics['support']} | {metrics['predicted']} | "
                     f"{pct(metrics['precision'])} | {pct(metrics['recall'])} | {pct(metrics['f1'])} |")
    lines += [
        "",
        "Per split (same filter settings):",
        "",
        "| split | frames | accuracy | stable rate | abstention | unsafe green |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for split, metrics in per_split_filtered.items():
        lines.append(f"| {split} | {metrics['frames']} | {metrics['accuracy']:.4f} | "
                     f"{pct(metrics['stable_rate'])} | {pct(metrics['abstention_rate'])} | "
                     f"{metrics['unsafe_green']} |")
    lines += [
        "",
        "## Confirmation-window sweep",
        "",
        "| min_confirm | accuracy | stable rate | abstention | unsafe green | green recall | red recall |",
        "|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for item in sweep:
        lines.append(f"| {item['min_confirm']} | {item['accuracy']:.4f} | "
                     f"{pct(item['stable_rate'])} | {pct(item['abstention_rate'])} | "
                     f"{item['unsafe_green']} | {pct(item['per_class']['green']['recall'])} | "
                     f"{pct(item['per_class']['red']['recall'])} |")
    lines += [
        "",
        "## Caveats",
        "",
        "- The CSV rows are individual labelled frames, not a certified contiguous video. Filter",
        "  numbers are an order-proxy stress test; they are a lower bound on warm-up behaviour.",
        "- GT is the three-state collapse of the dataset's own `mode` column, so the raw mapping",
        "  cannot disagree with GT. There is no learned traffic-light detector in this stub.",
        "- `green` is never a crossing permit: `auto_go=False` and green requires user confirmation.",
        "",
    ]
    with open(os.path.join(report_dir, "summary.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))

    print(f"report: {report_dir}")
    print(f"rows={len(rows)} sequences={len(sequences)} images_missing={image_missing}")
    print(f"raw mapping accuracy={raw_accuracy:.4f} "
          f"filtered accuracy={filtered_metrics['accuracy']:.4f} "
          f"unsafe_green={filtered_metrics['unsafe_green']} "
          f"abstention={filtered_metrics['abstention_rate']:.4f}")
    return 0


def _smoke(rows: List[dict], args) -> int:
    print(f"labels={args.labels} rows={len(rows)} min_confirm={args.min_confirm}")
    print(f"{'file':<28} {'mode':>4} {'block':<11} {'raw_state':<10} source")
    counts: Counter = Counter()
    for row in rows:
        state = imvisible_mode_to_state(row.get("mode"))
        counts[state] += 1
        print(f"{row.get('file', ''):<28} {row.get('mode', ''):>4} {row.get('block', ''):<11} "
              f"{state:<10} imvisible")
    print(f"raw states: {dict(counts)}")

    filt = TrafficLightFilter(min_confirm_frames=args.min_confirm,
                              min_confidence=args.min_confidence)
    print("\nconfirmation demo (same evidence repeated):")
    for label, obs in (("green x3", [imvisible_row_to_observation({"file": "demo", "mode": "1"})] * 3),
                       ("countdown-blank x3", [imvisible_row_to_observation({"file": "demo", "mode": "3"})] * 3)):
        decision = None
        for item in obs:
            decision = filt.update(item)
        print(f"  {label:<20} -> {decision.state:<10} stable={decision.stable} "
              f"auto_go={decision.auto_go} requires_confirmation={decision.requires_user_confirmation} "
              f"reason={decision.reason}")
        filt.reset()
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--labels", default=DEFAULT_LABELS)
    ap.add_argument("--split", default=None,
                    help="training|validation|testing|all, or a comma-separated list; "
                         "if omitted, --labels is used as a single file")
    ap.add_argument("--images", default=None,
                    help=f"extracted ImVisible frames (default: {DEFAULT_IMAGES})")
    ap.add_argument("--limit", type=int, default=12, help="0 = all rows")
    ap.add_argument("--min-confirm", type=int, default=3, dest="min_confirm")
    ap.add_argument("--min-confidence", type=float, default=0.6, dest="min_confidence")
    ap.add_argument("--sweep", default="1,2,3,4,5",
                    help="confirmation-window values to sweep in report mode")
    ap.add_argument("--report-dir", default=None,
                    help="write CSV/JSON/Markdown eval outputs here instead of printing rows")
    args = ap.parse_args(argv)

    label_files = _resolve_label_files(args)
    rows = _load_rows(label_files, args.limit)
    if not rows:
        print("no rows selected", file=sys.stderr)
        return 2
    if args.report_dir:
        if args.images is None:
            args.images = os.path.join(os.path.dirname(os.path.abspath(label_files[0][1])),
                                       "extracted", "PTL_Dataset_876x657")
        return _report(rows, args)
    return _smoke(rows, args)


if __name__ == "__main__":
    raise SystemExit(main())
