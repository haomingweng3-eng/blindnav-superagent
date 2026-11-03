"""Rank door candidates with a lightweight geometry/score classifier."""

import json
import statistics
from pathlib import Path

import numpy as np
from PIL import Image
from sklearn.ensemble import RandomForestClassifier
from ultralytics import YOLO

ROOT = Path(__file__).resolve().parents[1]
MODEL = ROOT / "runs/detect/runs/door/multi2/weights/best.pt"
CONF = .05


def iou(a, b):
    iw = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    ih = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = iw * ih
    uni = (a[2]-a[0])*(a[3]-a[1]) + (b[2]-b[0])*(b[3]-b[1]) - inter
    return inter/uni if uni > 0 else 0


def feats(box, score, w, h):
    x1, y1, x2, y2 = box
    bw, bh = max(1e-6, x2-x1), max(1e-6, y2-y1)
    return [score, np.log(bw*bh/(w*h)+1e-6), bw/bh, (x2-x1)/w, (y2-y1)/h,
            y2/h, y1/h, abs((x1+x2)/2/w-.5)]


def load_gt_yolo(p):
    w, h = Image.open(p).size
    out = []
    lp = p.parents[2] / "labels" / p.parent.name / f"{p.stem}.txt"
    for line in lp.read_text().splitlines():
        v = line.split()
        if len(v) < 5:
            continue
        cx, cy, bw, bh = map(float, v[1:5])
        out.append(((cx-bw/2)*w, (cy-bh/2)*h, (cx+bw/2)*w, (cy+bh/2)*h))
    return out


def load_gt_oi(p, boxes):
    w, h = Image.open(p).size
    return [(float(r["XMin"])*w, float(r["YMin"])*h, float(r["XMax"])*w, float(r["YMax"])*h) for r in boxes[p.stem]]


def gather(model, images, gt_fn):
    X, y = [], []
    for p in images:
        gt = gt_fn(p)
        res = model.predict(str(p), imgsz=640, conf=CONF, verbose=False)
        if not res:
            continue
        r = res[0]
        for b, s in zip(r.boxes.xyxy.tolist(), r.boxes.conf.tolist()):
            X.append(feats(b, float(s), *Image.open(p).size))
            y.append(int(max([iou(b, g) for g in gt], default=0) >= .3))
    return np.array(X), np.array(y)


def main():
    model = YOLO(str(MODEL))
    oi = json.load(open(ROOT/"data/openimages/door_boxes.json"))
    oi_val = [ROOT/"data/openimages/images"/f"{i}.jpg" for i in oi
              if (ROOT/"data/openimages/images"/f"{i}.jpg").exists()]
    oi_train_dir = ROOT/"data/door_oi/images/train"
    oi_train = sorted(oi_train_dir.glob("*.jpg"))[:600]
    # GT for OI train is in door_oi labels
    X, y = gather(model, oi_train, lambda p: load_gt_yolo(p))
    print("train candidates", len(y), "pos", int(y.sum()), flush=True)
    clf = RandomForestClassifier(n_estimators=120, max_depth=5, class_weight="balanced", random_state=0)
    clf.fit(X, y)

    def eval_set(name, images, gt_fn):
        ranks = {k: [] for k in (1, 2, 3, 5)}
        counts = []
        for p in images:
            gt = gt_fn(p)
            res = model.predict(str(p), imgsz=640, conf=CONF, verbose=False)
            if not res:
                for k in ranks:
                    for g in gt:
                        ranks[k].append(False)
                continue
            r = res[0]
            boxes = r.boxes.xyxy.tolist(); scores = r.boxes.conf.tolist()
            counts.append(len(boxes))
            if boxes:
                Xe = np.array([feats(b, float(s), *Image.open(p).size) for b, s in zip(boxes, scores)])
                order = np.argsort(-clf.predict_proba(Xe)[:, 1])
            else:
                order = []
            for k in ranks:
                top = [boxes[i] for i in order[:k]]
                for g in gt:
                    ranks[k].append(max([iou(g, b) for b in top], default=0) >= .3)
        return {"images": len(images), "gt_boxes": sum(len(gt_fn(p)) for p in images),
                "mean_candidates": round(statistics.mean(counts), 2),
                "top1_recall": round(float(np.mean(ranks[1])), 4),
                "top2_recall": round(float(np.mean(ranks[2])), 4),
                "top3_recall": round(float(np.mean(ranks[3])), 4),
                "top5_recall": round(float(np.mean(ranks[5])), 4)}

    rf_test = sorted((ROOT/"data/door_yolo/images/test").glob("*.jpg"))
    out = [{"name": "openimages_val", **eval_set("oi", oi_val, lambda p: load_gt_oi(p, oi))},
           {"name": "roboflow_test", **eval_set("rf", rf_test, load_gt_yolo)}]
    (ROOT/"results/door_rank.json").write_text(json.dumps(out, indent=1))
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
