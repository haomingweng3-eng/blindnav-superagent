"""Controlled-perturbation stability for the fine-tuned door detector."""

import json
import os
import statistics
from pathlib import Path

from PIL import Image
from ultralytics import YOLO

ROOT = Path(__file__).resolve().parents[1]
MODEL = os.getenv("MODEL", "runs/detect/runs/door/cpu1/weights/best.pt")
CONF = float(os.getenv("CONF", "0.10"))
IMGS = sorted((ROOT / "data/door_yolo/images/test").glob("*.jpg"))


def boxes(model, im):
    r = model.predict(im, imgsz=640, conf=CONF, verbose=False)[0]
    return [tuple(map(float, b)) for b in r.boxes.xyxy.tolist()]


def iou(a, b):
    iw = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    ih = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = iw * ih
    uni = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / uni if uni > 0 else 0


def perturb(im, dx, dy, scale):
    w, h = im.size
    nw, nh = int(w * scale), int(h * scale)
    canvas = Image.new("RGB", (w, h))
    canvas.paste(im.resize((nw, nh)), (int((w - nw) / 2 + dx * w), int((h - nh) / 2 + dy * h)))
    return canvas


def main():
    model = YOLO(MODEL)
    stabs, counts, nimg, variant_matches = [], [], 0, 0
    for p in IMGS:
        im = Image.open(p).convert("RGB")
        base = boxes(model, im)
        if not base:
            continue
        nimg += 1
        counts.append(len(base))
        for dx, dy, s in [(.02, 0, 1), (0, .02, 1), (0, 0, .98)]:
            bs = boxes(model, perturb(im, dx, dy, s))
            counts.append(len(bs))
            matched = False
            for b in base:
                best = max([iou(b, c) for c in bs], default=0)
                if best > 0:
                    stabs.append(best)
                    matched = True
            variant_matches += matched
    out = {"dataset": "fine-tuned door detector, held-out test, controlled perturbation",
           "images_with_candidates": nimg, "conf": CONF,
           "matched_candidate_iou_mean": round(statistics.mean(stabs), 4) if stabs else None,
           "match_rate": round(variant_matches / max(1, nimg * 3), 4),
           "candidate_count_std_over_variants": round(statistics.pstdev(counts), 3) if len(counts) > 1 else None,
           "note": "controlled perturbation, not temporal video stability"}
    (ROOT / "results" / "door_stability_ft.json").write_text(json.dumps(out, indent=1))
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
