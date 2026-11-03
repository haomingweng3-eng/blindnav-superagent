"""Obstacle detector vs UrbanEgo YOLO11x tracking on real walking video."""

from __future__ import annotations

import csv
import json
import os
import math
import statistics
from pathlib import Path

import numpy as np
import onnxruntime as ort
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / "data" / "urbanego" / "run7_20260714_171427"
ASSET = Path("/Users/mima0000/blindnav-app/app/src/main/assets/yolov8n.onnx")
SIZE = 640
BLOCKING = {0, 1, 2, 3, 5, 7, 9, 10, 11, 13, 16, 24, 28, 56, 57, 58, 59, 60}
NAMES = [
    "人", "自行车", "汽车", "摩托车", "飞机", "公交车", "火车", "卡车", "船", "红绿灯",
    "消防栓", "stop sign", "停车计时器", "长椅", "鸟", "猫", "狗", "马", "羊", "牛",
    "大象", "熊", "斑马", "长颈鹿", "背包", "雨伞", "手提包", "领带", "行李箱", "飞盘",
    "滑雪板", "单板滑雪", "运动球", "风筝", "棒球棒", "棒球手套", "滑板", "冲浪板",
    "网球拍", "瓶子", "酒杯", "杯子", "叉子", "刀", "勺子", "碗", "香蕉", "苹果",
    "三明治", "橙子", "西兰花", "胡萝卜", "热狗", "披萨", "甜甜圈", "蛋糕", "椅子",
    "沙发", "盆栽", "床", "餐桌", "马桶", "电视", "笔记本电脑", "鼠标", "遥控器",
    "键盘", "手机", "微波炉", "烤箱", "烤面包机", "水槽", "冰箱", "书", "时钟",
    "花瓶", "剪刀", "泰迪熊", "吹风机", "牙刷",
]
CONF=float(os.getenv('CONF','0.30'))
NEED=float(os.getenv('NEED','0.25'))
PCONF=float(os.getenv('PCONF','0.65'))
PBOT=float(os.getenv('PBOT','0.72'))
SUFFIX=os.getenv('SUFFIX','current')
GT_MAP = {"person": "人", "bicycle": "自行车", "car": "汽车", "motorcycle": "摩托车",
          "bus": "公交车", "truck": "卡车"}


def letterbox(im):
    w, h = im.size
    scale = min(SIZE / w, SIZE / h)
    nw, nh = int(w * scale), int(h * scale)
    px, py = (SIZE - nw) // 2, (SIZE - nh) // 2
    canvas = Image.new("RGB", (SIZE, SIZE), (114, 114, 114))
    canvas.paste(im.resize((nw, nh), Image.BILINEAR), (px, py))
    x = np.asarray(canvas, dtype=np.float32) / 255.0
    return x.transpose(2, 0, 1)[None], px, py, nw, nh


def iou(a, b):
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    iw = max(0.0, min(ax2, bx2) - max(ax1, bx1))
    ih = max(0.0, min(ay2, by2) - max(ay1, by1))
    inter = iw * ih
    uni = (ax2 - ax1) * (ay2 - ay1) + (bx2 - bx1) * (by2 - by1) - inter
    return inter / uni if uni > 0 else 0.0


def detect(sess, im):
    x, padX, padY, newW, newH = letterbox(im)
    out = sess.run(["output0"], {"images": x})[0][0]
    n = 8400
    cands = []
    for i in range(n):
        cls, score = -1, CONF
        for k in range(80):
            s = float(out[4 + k, i])
            if s > score:
                score, cls = s, k
        if cls < 0 or cls not in BLOCKING:
            continue
        cx, cy, w, h = (float(out[k, i]) for k in range(4))
        x1, x2, bottom = cx - w / 2, cx + w / 2, cy + h / 2
        pathL, pathR = padX + newW * 0.30, padX + newW * 0.70
        corridorW = pathR - pathL
        overlap = max(0.0, min(x2, pathR) - max(x1, pathL))
        overlapRatio = overlap / max(w, 1e-3)
        relBottom = (bottom - padY) / newH
        if relBottom <= 0.58:
            continue
        if cls == 0:
            visibleH = max(0.0, min(bottom, padY + newH) - max(cy - h / 2, padY))
            if score < PCONF or w <= 0 or h <= 0 or relBottom <= PBOT or visibleH < newH * 0.25:
                continue
            if (not (pathL <= cx <= pathR) and overlap / corridorW < 0.75) or \
               overlap / min(w, corridorW) < 0.55:
                continue
        need = 0.12 if relBottom > 0.88 else NEED
        if overlapRatio < need or w * h < newW * newH * 0.004:
            continue
        cands.append([x1, cy - h / 2, x2, bottom, score, cls, i, overlap / corridorW])
    cands.sort(key=lambda c: -c[4])
    kept = []
    for c in cands:
        if all(not (c[5] == k[5] and iou(c[:4], k[:4]) > 0.45) for k in kept):
            kept.append(c)
    sx = newW / im.width
    sy = newH / im.height
    def ox(v): return (v - padX) / sx
    def oy(v): return (v - padY) / sy
    return [{"cls": int(c[5]), "name": NAMES[c[5]], "score": round(c[4], 3),
             "box": [round(ox(c[0]), 1), round(oy(c[1]), 1), round(ox(c[2]), 1), round(oy(c[3]), 1)],
             "corridor_overlap": round(c[7], 3),
             "band": "近" if (c[3] - padY) / newH > 0.82 else ("中" if (c[3] - padY) / newH > 0.66 else "远")}
            for c in kept[:2]]


def load_gt():
    frames = {}
    for line in (RUN / "yolo" / "frames.jsonl").open():
        r = json.loads(line)
        frames[round(float(r["t"]), 2)] = r
    dets = {}
    for line in (RUN / "yolo" / "detections.jsonl").open():
        r = json.loads(line)
        t = round(float(r["t"]), 2)
        dets.setdefault(t, []).append(r)
    return frames, dets


def gt_blocking(records, w=1280, h=720):
    out = []
    pathL, pathR = w * 0.30, w * 0.70
    for r in records:
        name = GT_MAP.get(r.get("name"))
        if not name:
            continue
        x1, y1, x2, y2 = [float(v) for v in r["box"]]
        overlap = max(0.0, min(x2, pathR) - max(x1, pathL))
        if overlap / (pathR - pathL) < 0.25 or y2 / h <= 0.58:
            continue
        out.append({"name": name, "box": [x1, y1, x2, y2], "overlap": overlap / (pathR - pathL)})
    return out


def match(model_hits, gt_hits):
    used = set()
    tp = 0
    for m in model_hits:
        best, best_iou = None, 0.0
        for j, g in enumerate(gt_hits):
            if j in used or g["name"] != m["name"]:
                continue
            v = iou(m["box"], g["box"])
            if v > best_iou:
                best, best_iou = j, v
        if best is not None and best_iou >= 0.3:
            used.add(best); tp += 1
    return tp, len(model_hits) - tp, len(gt_hits) - tp


def main():
    sess = ort.InferenceSession(str(ASSET), providers=["CPUExecutionProvider"])
    frames, dets = load_gt()
    rows = []
    for fp in sorted((RUN / "frames_1s").glob("*.jpg")):
        idx = int(fp.stem.split("_")[1])
        t = (idx - 1) * 1.0
        nearest = min(dets.keys(), key=lambda k: abs(k - t)) if dets else None
        recs = dets.get(nearest, []) if nearest is not None else []
        gt = gt_blocking(recs)
        im = Image.open(fp).convert("RGB")
        model = detect(sess, im)
        tp, fp_, fn = match(model, gt)
        rows.append({"frame": fp.name, "t": t, "gt_n": len(gt), "model_n": len(model),
                     "tp": tp, "fp": fp_, "fn": fn,
                     "gt_names": "|".join(g["name"] for g in gt),
                     "model_names": "|".join(m["name"] for m in model)})
    out = ROOT / "results"
    out.mkdir(exist_ok=True)
    with (out / f"urbanego_obstacle_{SUFFIX}.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader(); w.writerows(rows)
    tp = sum(r["tp"] for r in rows); fp_ = sum(r["fp"] for r in rows); fn = sum(r["fn"] for r in rows)
    frames_with_gt = sum(1 for r in rows if r["gt_n"] > 0)
    frames_with_fp = sum(1 for r in rows if r["fp"] > 0)
    frames_with_tp = sum(1 for r in rows if r["tp"] > 0)
    summary = {
        "dataset": f"UrbanEgo run7, 1 s sampling; conf={CONF} need={NEED} person={PCONF}/{PBOT}",
        "frames": len(rows),
        "frames_with_gt_blocking": frames_with_gt,
        "frames_with_model_tp": frames_with_tp,
        "frames_with_model_fp": frames_with_fp,
        "object_tp": tp, "object_fp": fp_, "object_fn": fn,
        "object_precision": round(tp / max(1, tp + fp_), 4),
        "object_recall": round(tp / max(1, tp + fn), 4),
        "frame_false_positive_rate": round(frames_with_fp / max(1, len(rows)), 4),
        "note": "GT = YOLO11x+BoT-SORT detections filtered to the same 30-70% corridor; "
                "not human ground truth. Per-frame, no temporal policy.",
    }
    (out / f"urbanego_obstacle_{SUFFIX}.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
