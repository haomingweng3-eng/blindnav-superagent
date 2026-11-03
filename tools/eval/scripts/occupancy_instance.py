"""Occupancy-first prototype: walkable mask + YOLO instance masks + depth + tracking."""

import bisect
import json
import sys
from pathlib import Path

import numpy as np
import torch
from PIL import Image
from transformers.models.segformer import SegformerForSemanticSegmentation
from ultralytics import YOLO

sys.path.insert(0, str(Path(__file__).resolve().parent))
from eval_segformer_v2 import load_image
from urbanego_obstacle import RUN, gt_blocking, load_gt

ROOT = Path(__file__).resolve().parents[1]
W, H = 512, 288
BLOCK_CLASSES = {0, 1, 2, 3, 5, 7, 13, 16, 24, 26, 28, 56, 57, 58, 59, 60}


def roi_slice():
    return (slice(int(H * .55), int(H * .98)), slice(int(W * .2), int(W * .8)))


def bbox_iou(a, b):
    iw = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    ih = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = iw * ih
    ua = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / ua if ua > 0 else 0.0


def main():
    ck = torch.load(ROOT / "models/segformer_sanpo_b0_v2.pt", map_location="cpu")
    seg = SegformerForSemanticSegmentation.from_pretrained(
        ck["model"], num_labels=5, ignore_mismatched_sizes=True).eval()
    seg.load_state_dict(ck["state_dict"])
    yolo = YOLO(str(ROOT / "yolov8n-seg.pt"))

    _, dets = load_gt()
    rgb = [json.loads(x) for x in (RUN / "rgb_frames.jsonl").open()]
    depth = [json.loads(x) for x in (RUN / "depth_frames.jsonl").open()]
    first = rgb[0]["ts_unix_ns"]
    dts = [d["ts_unix_ns"] for d in depth]

    def nearest_depth(ts):
        i = bisect.bisect_left(dts, ts)
        j = min((k for k in (i - 1, i, i + 1) if 0 <= k < len(depth)),
                key=lambda k: abs(dts[k] - ts))
        return depth[j]

    rs, cs = roi_slice()
    rows = []
    counts = {"det_blocking": 0, "after_corridor": 0, "after_walkable": 0, "depth_valid": 0}
    safe_fracs = []
    for idx, fp in enumerate(sorted((RUN / "clip_375").glob("*.jpg"))):
        t = 375.0 + idx * 0.1
        recs = dets.get(min(dets.keys(), key=lambda k: abs(k - t)), []) if dets else []
        gt = bool(gt_blocking(recs))
        with torch.no_grad():
            logits = seg(pixel_values=load_image(fp, W, H)).logits
            logits = torch.nn.functional.interpolate(logits, size=(H, W), mode="bilinear", align_corners=False)
            safe = logits.argmax(1)[0].numpy() == 1
        safe_roi = safe[rs, cs]
        safe_fracs.append(float(safe_roi.mean()))

        d = nearest_depth(first + int(t * 1e9))
        dp = RUN / "depth" / f"{d['frame']:06d}.png"
        dep = None
        if dp.exists():
            dep = np.array(Image.open(dp).resize((W, H), Image.NEAREST)).astype(np.float32) / 1000.0

        r = yolo.predict(str(fp), imgsz=640, conf=0.25, verbose=False)[0]
        inst = []
        if r.masks is not None:
            masks = r.masks.data.cpu().numpy()
            boxes = r.boxes.xyxy.cpu().numpy()
            classes = r.boxes.cls.cpu().numpy().astype(int)
            for m, b, c in zip(masks, boxes, classes):
                if c not in BLOCK_CLASSES:
                    continue
                counts["det_blocking"] += 1
                m = np.asarray(Image.fromarray((m * 255).astype(np.uint8)).resize((W, H), Image.NEAREST)) > 127
                in_roi = m[rs, cs]
                if in_roi.sum() < max(20, 0.01 * m.sum()):
                    continue
                counts["after_corridor"] += 1
                walk_overlap = float((in_roi & safe_roi).sum())
                if walk_overlap < max(20, 0.01 * m.sum()):
                    continue
                counts["after_walkable"] += 1
                depth_med = None
                valid_ratio = None
                if dep is not None:
                    pix = dep[m]
                    pix = pix[pix > 0]
                    valid_ratio = float(len(pix) / max(1, int(m.sum())))
                    if len(pix) >= 20 and valid_ratio >= .05:
                        depth_med = float(np.median(pix))
                        counts["depth_valid"] += 1
                inst.append({
                    "box": [float(v) for v in b],
                    "cls": int(c),
                    "walk_overlap": walk_overlap,
                    "depth_med": depth_med,
                    "valid_ratio": valid_ratio,
                })
        rows.append({
            "i": idx, "t": t, "gt": gt,
            "inst": inst,
            "occ_inst": bool(inst),
            "occ_inst_depth": any(i["depth_med"] is not None and i["depth_med"] < 2.0 for i in inst),
            "inst_boxes": [i["box"] for i in inst],
        })

    for i, row in enumerate(rows):
        prev = rows[i - 1]["inst_boxes"] if i else []
        row["occ_inst_temporal"] = row["occ_inst"] and any(
            any(bbox_iou(b, pb) > .3 for pb in prev) for b in row["inst_boxes"])
        row["occ_inst_depth_temporal"] = row["occ_inst_depth"] and any(
            any(bbox_iou(b, pb) > .3 for pb in prev) for b in row["inst_boxes"])

    def score(key):
        tp = sum(r["gt"] and r[key] for r in rows)
        fp = sum(not r["gt"] and r[key] for r in rows)
        fn = sum(r["gt"] and not r[key] for r in rows)
        neg = len(rows) - sum(r["gt"] for r in rows)
        return {
            "frames": len(rows), "gt_blocking": sum(r["gt"] for r in rows),
            "recall": round(tp / max(1, tp + fn), 4),
            "precision": round(tp / max(1, tp + fp), 4) if tp + fp else None,
            "false_positive_rate": round(fp / max(1, neg), 4),
        }

    depth_avail = sum(1 for r in rows if any(i["depth_med"] is not None for i in r["inst"]))
    out = {
        "dataset": "UrbanEgo clip 375-400s, 10fps; SegFormer walkable + YOLOv8n-seg instances + depth + tracking",
        "instance_model": "yolov8n-seg (COCO), conf 0.25",
        "frames": len(rows),
        "walkable_roi_mean": round(float(np.mean(safe_fracs)), 4),
        "walkable_corridor_available": round(float(np.mean(np.array(safe_fracs) >= .25)), 4),
        "counts": counts,
        "frames_with_depth_valid_instance": depth_avail,
        "instance_only": score("occ_inst"),
        "instance_plus_depth": score("occ_inst_depth"),
        "instance_plus_temporal": score("occ_inst_temporal"),
        "instance_plus_depth_temporal": score("occ_inst_depth_temporal"),
        "note": "unknown-object recall not measurable; no GT instances for unlabeled objects",
    }
    (ROOT / "results" / "occupancy_instance.json").write_text(json.dumps(out, indent=1))
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
