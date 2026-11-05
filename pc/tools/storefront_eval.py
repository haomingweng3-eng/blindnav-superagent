#!/usr/bin/env python3
"""Compare target-aware entrance grounding against the old area/confidence ranking.

What this measures, on real Entrances frames with real Vision OCR text:

* coverage      -- frames where a readable sign exists and a door sits under it
* disagreement  -- how often target-aware top-1 differs from the old "biggest door" top-1
* abstention    -- frames where the new path refuses instead of guessing

What it deliberately does NOT measure: entrance accuracy.  Entrances has no "which door
belongs to the store the user named" annotation, so any accuracy number here would be
self-graded.  It is reported as NOT_EVALUABLE.
"""
from __future__ import annotations

import json
import os
import sys
from collections import Counter

from PIL import Image, ImageDraw

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from blindnav_pc.storefront import Door, Sign, ground, spatial_score  # noqa: E402

DATASET = os.path.expanduser(
    "~/blindnav-app/pc/data/eval/entrances/extracted/final_dataset")
OCR_DIR = "/tmp/blindnav_ocr/ent_all"
OUT = os.path.expanduser("~/blindnav-app/pc/out/storefront_eval")


def _doors(label_path: str) -> list[Door]:
    out = []
    for line in open(label_path, encoding="utf-8-sig"):
        p = line.split()
        if len(p) < 5:
            continue
        cx, cy, w, h = (float(v) for v in p[1:5])
        out.append(Door((cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2)))
    return out


def _signs(entry: dict) -> list[Sign]:
    return [Sign(t, (x, y, x + w, y + h), c)
            for t, x, y, w, h, c in entry.get("text", [])]


_NOT_SIGN = {"stop", "oneway", "push", "pull", "open", "closed", "exit", "enter", "parking"}


def _bucket(sign: Sign, doors: list[Door]) -> str:
    """Why a piece of OCR text did or did not ground to a door, one label per sign."""
    text = sign.text.strip()
    letters = [ch for ch in text if ch.isalpha()]
    if len(text) < 3:
        return "too_short"
    if not letters or "".join(letters).lower() in _NOT_SIGN:
        return "not_a_shop_sign"
    scores = [spatial_score(sign, d) for d in doors]
    best = max(scores) if scores else 0.0
    if best <= 0.0:
        return "no_door_in_sign_column"
    if best < 0.5:
        return "door_far_from_sign"
    return "grounded"


def _biggest(door: Door) -> float:
    b = door.box
    return (b[2] - b[0]) * (b[3] - b[1])


def _draw(img_path: str, out_path: str, doors, signs, chosen, sign_used):
    im = Image.open(img_path).convert("RGB")
    W, H = im.size
    d = ImageDraw.Draw(im)

    def rect(box, color, width=3):
        x1, y1, x2, y2 = box
        d.rectangle([x1 * W, (1 - y2) * H, x2 * W, (1 - y1) * H], outline=color, width=width)

    for s in signs:
        rect(s.box, (255, 215, 0), 2)
    for door in doors:
        rect(door.box, (80, 160, 255), 2)
    if sign_used is not None:
        rect(sign_used.box, (0, 200, 0), 4)
    if chosen is not None:
        rect(chosen.box, (255, 40, 40), 5)
    im.save(out_path, quality=90)


def main() -> int:
    os.makedirs(OUT, exist_ok=True)
    stats = Counter()
    disagreements = []
    examples = []

    for split in ("train", "val", "test"):
        entries = {os.path.basename(e["file"]): e
                   for e in json.load(open(os.path.join(OCR_DIR, f"{split}.json")))}
        label_dir = os.path.join(DATASET, split, "labels")
        img_dir = os.path.join(DATASET, split, "images")
        for name in sorted(os.listdir(label_dir)):
            if not name.endswith(".txt"):
                continue
            stem = os.path.splitext(name)[0]
            doors = _doors(os.path.join(label_dir, name))
            if not doors:
                continue
            signs = _signs(entries.get(stem + ".jpg", {}))
            if not signs:
                stats["no_ocr_text"] += 1
                continue
            stats["has_text"] += 1
            for sign in signs:
                stats[f"bucket_{_bucket(sign, doors)}"] += 1
            targets = [s.text for s in signs if len(s.text.strip()) >= 3]
            stats["signs_tried"] += len(targets)
            result = None
            for target in targets:
                trial = ground(doors, signs, target)
                if trial.state == "confirmed":
                    stats["signs_grounded"] += 1
                    if result is None:
                        result = trial
            if result is None:
                stats["frames_nothing_grounded"] += 1
                continue
            stats["confirmed"] += 1
            picked = result.candidates[0].door
            old = max(doors, key=_biggest)
            if len(doors) >= 2:
                stats["multi_door_confirmed"] += 1
                if picked is not old:
                    stats["disagree_with_area_ranking"] += 1
                    disagreements.append({"split": split, "frame": stem,
                                          "sign": result.candidates[0].sign_text})
            if len(examples) < 6:
                path = os.path.join(OUT, f"{split}_{stem}.jpg")
                _draw(os.path.join(img_dir, stem + ".jpg"), path, doors, signs,
                      picked, result.best_sign)
                examples.append(path)

    frames = stats["has_text"] + stats["no_ocr_text"]
    summary = {
        "frames_with_doors": frames,
        "frames_with_ocr_text": stats["has_text"],
        "signs_tried": stats["signs_tried"],
        "signs_grounded_to_a_door": stats["signs_grounded"],
        "sign_grounding_rate": round(
            stats["signs_grounded"] / max(1, stats["signs_tried"]), 4),
        "frames_with_at_least_one_grounded_sign": stats["confirmed"],
        "frames_nothing_grounded": stats["frames_nothing_grounded"],
        "multi_door_frames_confirmed": stats["multi_door_confirmed"],
        "disagreement_with_area_ranking": stats["disagree_with_area_ranking"],
        "disagreement_rate_on_multi_door": round(
            stats["disagree_with_area_ranking"] / max(1, stats["multi_door_confirmed"]), 4),
        "entrance_accuracy": "NOT_EVALUABLE",
        "accuracy_reason": "Entrances has no target-to-door annotation; "
                           "any accuracy here would be self-graded",
        "sign_failure_buckets": {k[7:]: v for k, v in sorted(stats.items()) if k.startswith("bucket_")},
        "examples": examples,
    }
    json.dump(summary, open(os.path.join(OUT, "summary.json"), "w"), indent=2)
    json.dump(disagreements, open(os.path.join(OUT, "disagreements.json"), "w"), indent=2)
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
