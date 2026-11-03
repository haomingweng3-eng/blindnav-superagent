"""Door candidate evaluation: Recall@IoU + candidate count on held-out and external sets."""

import json
import os
import statistics
from pathlib import Path

from PIL import Image
from ultralytics import YOLO

ROOT = Path(__file__).resolve().parents[1]
MODEL = os.getenv("MODEL", "runs/detect/runs/door/cpu1/weights/best.pt")
CONFS = [0.01, 0.02, 0.03, 0.05, 0.10, 0.15, 0.25, 0.40]


def iou(a, b):
    iw = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    ih = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = iw * ih
    uni = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / uni if uni > 0 else 0


def eval_set(name, images, gt_fn, model):
    out = {}
    preds_by_conf = {c: [] for c in CONFS}
    for p in images:
        im = Image.open(p)
        w, h = im.size
        gt = gt_fn(p, w, h)
        r = model.predict(str(p), imgsz=640, conf=min(CONFS), verbose=False)[0]
        boxes = [(float(x1), float(y1), float(x2), float(y2), float(s))
                 for (x1, y1, x2, y2), s in zip(r.boxes.xyxy.tolist(), r.boxes.conf.tolist())]
        for c in CONFS:
            kept = [b for b in boxes if b[4] >= c]
            preds_by_conf[c].append((gt, kept))
    for c in CONFS:
        tp03 = tp05 = ngt = 0
        counts = []
        for gt, kept in preds_by_conf[c]:
            ngt += len(gt)
            counts.append(len(kept))
            for g in gt:
                best = max([iou(g, b[:4]) for b in kept], default=0)
                tp03 += best >= .3
                tp05 += best >= .5
        out[str(c)] = {
            "images": len(images), "gt_boxes": ngt,
            "recall_iou03": round(tp03 / max(1, ngt), 4),
            "recall_iou05": round(tp05 / max(1, ngt), 4),
            "mean_candidates": round(statistics.mean(counts), 2),
            "median_candidates": statistics.median(counts),
            "candidate_count_std": round(statistics.pstdev(counts), 2),
        }
    return {"name": name, **out}


def main():
    model = YOLO(MODEL)
    test_dir = ROOT / "data/door_yolo/images/test"
    test_imgs = sorted(test_dir.glob("*.jpg"))

    def test_gt(p, w, h):
        lp = ROOT / "data/door_yolo/labels/test" / f"{p.stem}.txt"
        out = []
        for line in lp.read_text().splitlines():
            v = line.split()
            if len(v) < 5:
                continue
            cx, cy, bw, bh = map(float, v[1:5])
            out.append(((cx - bw / 2) * w, (cy - bh / 2) * h, (cx + bw / 2) * w, (cy + bh / 2) * h))
        return out

    oi = json.load(open(ROOT / "data/openimages/door_boxes.json"))
    oi_ids = [i for i in oi if (ROOT / "data/openimages/images" / f"{i}.jpg").exists()]

    def oi_gt(p, w, h):
        i = p.stem
        return [(float(r["XMin"]) * w, float(r["YMin"]) * h, float(r["XMax"]) * w, float(r["YMax"]) * h)
                for r in oi[i]]

    res = [eval_set("roboflow_door_test", test_imgs, test_gt, model),
           eval_set("openimages_door", [ROOT / "data/openimages/images" / f"{i}.jpg" for i in oi_ids], oi_gt, model)]
    out = ROOT / "results" / "door_detector_eval.json"
    out.write_text(json.dumps(res, indent=1))
    print(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
