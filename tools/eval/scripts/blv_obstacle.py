"""COCO detector class coverage on BLV-Road-Nav-Accessibility real frames."""

from __future__ import annotations

import csv
import json
import re
from collections import defaultdict
from pathlib import Path

import numpy as np
import onnxruntime as ort
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT / "data" / "BLV-Road-Nav-Accessibility"
ASSET = Path("/Users/mima0000/blindnav-app/app/src/main/assets/yolov8n.onnx")
SIZE = 640
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
BLV_TO_COCO = {
    "Bench": "长椅", "Bicycle": "自行车", "Bus": "公交车", "Car": "汽车",
    "Chair": "椅子", "Dog": "狗", "Fire hydrant": "消防栓", "Motorcycle": "摩托车",
    "Person": "人", "Stop Sign": "stop sign", "Table": "餐桌", "Traffic Signals": "红绿灯",
}
NOT_COVERED = [
    "Foldout Sign", "Raised Entryway", "Overhanging Tree Branches", "Sidewalk Pits",
    "Curb", "Sloped Curb", "Stairs", "Uneven Stairs", "Doorway", "Pillar", "Pole",
    "Barrier Post", "Fence", "Railing", "Trash Bins", "Wheelchair", "White Cane",
    "Tactile Paving", "Wet Surface", "Clear Ice", "Puddle", "Snow", "Closed Sidewalk",
]
FRAME_RE = re.compile(r"video-(\d+)-segment-(\d+)-frame-(\d+)")


def letterbox(im):
    w, h = im.size
    scale = min(SIZE / w, SIZE / h)
    nw, nh = int(w * scale), int(h * scale)
    px, py = (SIZE - nw) // 2, (SIZE - nh) // 2
    canvas = Image.new("RGB", (SIZE, SIZE), (114, 114, 114))
    canvas.paste(im.resize((nw, nh), Image.BILINEAR), (px, py))
    x = np.asarray(canvas, dtype=np.float32) / 255.0
    return x.transpose(2, 0, 1)[None]


def detect_names(sess, im, conf=0.30):
    out = sess.run(["output0"], {"images": letterbox(im)})[0][0]
    n = 8400
    found = set()
    for i in range(n):
        best, cls = 0.0, -1
        for k in range(80):
            v = float(out[4 + k, i])
            if v > best:
                best, cls = v, k
        if cls >= 0 and best >= conf:
            found.add(NAMES[cls])
    return found


def load_labels():
    labels = {}
    for csv_path in (REPO / "Ground Truth Annotation").glob("*.csv"):
        m = re.match(r"video-(\d+)-segment-(\d+)", csv_path.stem)
        if not m:
            continue
        v, s = int(m.group(1)), int(m.group(2))
        with csv_path.open(encoding="utf-8", errors="replace") as f:
            rows = list(csv.reader(f))
        header = rows[0]
        for row in rows[1:]:
            if not row:
                continue
            obj = row[0].strip().strip('"')
            for col, val in zip(header[1:], row[1:]):
                if str(val).strip() not in ("1", "1.0"):
                    continue
                fm = re.match(r"frame_(\d+)", col)
                if fm:
                    labels.setdefault((v, s, int(fm.group(1))), set()).add(obj)
    return labels


def main():
    sess = ort.InferenceSession(str(ASSET), providers=["CPUExecutionProvider"])
    labels = load_labels()
    per_cat = defaultdict(lambda: {"support": 0, "hit": 0, "pred": 0, "tp": 0})
    frame_rows = []
    for fp in sorted((REPO / "Video Frames").glob("*.jpeg")):
        m = FRAME_RE.search(fp.name)
        if not m:
            continue
        key = (int(m.group(1)), int(m.group(2)), int(m.group(3)))
        present = labels.get(key, set())
        im = Image.open(fp).convert("RGB")
        found = detect_names(sess, im)
        for blv, coco in BLV_TO_COCO.items():
            s = per_cat[blv]
            truth = blv in present
            pred = coco in found
            if truth: s["support"] += 1
            if pred: s["pred"] += 1
            if truth and pred:
                s["hit"] += 1; s["tp"] += 1
        frame_rows.append({"frame": fp.name, "present": "|".join(sorted(present)),
                           "detected": "|".join(sorted(found & set(BLV_TO_COCO.values())))})
    out = ROOT / "results"
    out.mkdir(exist_ok=True)
    with (out / "blv_obstacle.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(frame_rows[0]))
        w.writeheader(); w.writerows(frame_rows)
    summary = {"dataset": "BLV-Road-Nav-Accessibility, 21 videos / 31 segments / 1795 frames",
               "frames": len(frame_rows),
               "categories": {}, "not_covered": NOT_COVERED}
    for cat, s in sorted(per_cat.items()):
        summary["categories"][cat] = {
            "support_frames": s["support"],
            "recall": round(s["hit"] / s["support"], 4) if s["support"] else None,
            "precision": round(s["tp"] / s["pred"], 4) if s["pred"] else None,
        }
    macro = [v["recall"] for v in summary["categories"].values() if v["recall"] is not None]
    summary["macro_recall"] = round(sum(macro) / len(macro), 4) if macro else None
    (out / "blv_obstacle_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
