#!/usr/bin/env python3
"""Run target-specific entrance grounding on real storefront frames.

Inputs are real: YOLO door labels from Entrances and OCR output from Apple Vision.  The OCR
JSON must be a list of ``{"file": path, "text": [[string, x, y, w, h, confidence], ...]}``.

Without an independent "which door is the target's" annotation this tool reports coverage and
stability, and refuses to print an entrance-accuracy figure.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from blindnav_pc.storefront import Door, Sign, ground, normalize  # noqa: E402


def _doors_from_labels(path: str) -> list[Door]:
    out = []
    for line in open(path, encoding="utf-8-sig"):
        p = line.split()
        if len(p) < 5:
            continue
        cx, cy, w, h = (float(v) for v in p[1:5])
        out.append(Door((cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2)))
    return out


def _signs(entry: dict) -> list[Sign]:
    signs = []
    for text, x, y, w, h, conf in entry.get("text", []):
        signs.append(Sign(text, (x, y, x + w, y + h), conf))
    return signs


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", default=os.path.expanduser(
        "~/blindnav-app/pc/data/eval/entrances/extracted/final_dataset"))
    ap.add_argument("--ocr-dir", default="/tmp/blindnav_ocr/ent_all")
    ap.add_argument("--split", default="test")
    ap.add_argument("--target", required=True)
    ap.add_argument("--alias", action="append", default=[])
    ap.add_argument("--json-out", default="")
    args = ap.parse_args()

    ocr_path = os.path.join(args.ocr_dir, f"{args.split}.json")
    entries = {os.path.basename(e["file"]): e for e in json.load(open(ocr_path))}
    label_dir = os.path.join(args.dataset, args.split, "labels")

    states = Counter()
    rows = []
    for name in sorted(os.listdir(label_dir)):
        if not name.endswith(".txt"):
            continue
        stem = os.path.splitext(name)[0]
        doors = _doors_from_labels(os.path.join(label_dir, name))
        entry = entries.get(stem + ".jpg", {"file": stem, "text": []})
        result = ground(doors, _signs(entry), args.target, args.alias)
        states[result.state] += 1
        if result.state == "confirmed":
            c = result.candidates[0]
            rows.append({"frame": stem, "sign": c.sign_text, "name": round(c.name_score, 3),
                         "spatial": round(c.spatial_score, 3), "total": round(c.total, 3),
                         "door_cx": round(c.door.center_x, 4)})

    print(f"split={args.split} frames={sum(states.values())} target={args.target!r}")
    print("states:", dict(states))
    print("confirmed frames:", len(rows))
    print("entrance accuracy: NOT_EVALUABLE (no independent target-door annotation)")
    if rows:
        print("sample:")
        for r in rows[:10]:
            print(" ", r)
    if args.json_out:
        json.dump({"stats": dict(states), "rows": rows}, open(args.json_out, "w"), indent=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
