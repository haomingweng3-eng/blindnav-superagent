"""YOLO-World BLV vocabulary baseline on BLV-Road-Nav frames."""

from __future__ import annotations

import csv
import json
import os
import re
import time
from collections import defaultdict
from pathlib import Path

from ultralytics import YOLO

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT / "data" / "BLV-Road-Nav-Accessibility"
FRAME_RE = re.compile(r"video-(\d+)-segment-(\d+)-frame-(\d+)")
VOCAB = [
    "person", "bicycle", "wheelchair", "dog", "bench", "chair", "table",
    "bollard", "pole", "pillar", "railing", "barrier", "foldout sign",
    "stairs", "curb", "raised entryway", "trash bin", "construction object",
    "overhanging branch", "doorway",
    "scooter", "stroller", "shopping cart", "fire hydrant", "traffic cone",
    "construction barrier", "guardrail", "handrail", "tactile paving",
    "curb ramp", "ramp", "gate", "fence", "street light", "traffic sign",
    "tree branch", "pothole", "mailbox", "bus stop", "trash can",
]
# BLV label -> YOLO-World prompt name
MAP = {
    "Person": "person", "Bicycle": "bicycle", "Wheelchair": "wheelchair", "Dog": "dog",
    "Bench": "bench", "Chair": "chair", "Table": "table", "Pole": "pole",
    "Pillar": "pillar", "Railing": "railing", "Barrier Post": "barrier",
    "Foldout Sign": "foldout sign", "Stairs": "stairs", "Curb": "curb",
    "Sloped Curb": "curb", "Raised Entryway": "raised entryway",
    "Trash Bins": "trash bin", "Doorway": "doorway",
    "Overhanging Tree Branches": "overhanging branch",
}


def load_labels():
    labels = {}
    for p in (REPO / "Ground Truth Annotation").glob("*.csv"):
        m = re.match(r"video-(\d+)-segment-(\d+)", p.stem)
        if not m: continue
        v, s = int(m.group(1)), int(m.group(2))
        rows = list(csv.reader(p.open(encoding="utf-8", errors="replace")))
        header = rows[0]
        for row in rows[1:]:
            if not row: continue
            obj = row[0].strip().strip('"')
            for col, val in zip(header[1:], row[1:]):
                if str(val).strip() in ("1", "1.0"):
                    fm = re.match(r"frame_(\d+)", col)
                    if fm: labels.setdefault((v, s, int(fm.group(1))), set()).add(obj)
    return labels


def main(limit=0):
    model = YOLO(str(ROOT / "yolov8s-worldv2.pt"))
    model.set_classes(VOCAB)
    labels = load_labels()
    paths = sorted((REPO / "Video Frames").glob("*.jpeg"))
    if limit: paths = paths[:limit]
    per = defaultdict(lambda: {"support": 0, "hit": 0, "pred": 0, "tp": 0})
    lat = []
    for start in range(0, len(paths), 16):
        chunk = paths[start:start + 16]
        t0 = time.time()
        results = model.predict([str(p) for p in chunk], imgsz=640, conf=0.15,
                                device=os.getenv("DEVICE", "mps"), verbose=False)
        lat.append((time.time() - t0) / len(chunk) * 1000)
        for fp, r in zip(chunk, results):
            m = FRAME_RE.search(fp.name)
            key = (int(m.group(1)), int(m.group(2)), int(m.group(3)))
            present = labels.get(key, set())
            found = set()
            for c in r.boxes.cls.tolist():
                found.add(r.names[int(c)])
            for blv, prompt in MAP.items():
                d = per[blv]; truth = blv in present; pred = prompt in found
                if truth: d["support"] += 1
                if pred: d["pred"] += 1
                if truth and pred: d["hit"] += 1; d["tp"] += 1
    cats = {k: {"support": v["support"],
                "recall": round(v["hit"] / v["support"], 4) if v["support"] else None,
                "precision": round(v["tp"] / v["pred"], 4) if v["pred"] else None}
            for k, v in per.items()}
    macro = [v["recall"] for v in cats.values() if v["recall"] is not None]
    summary = {"dataset": "BLV-Road-Nav, YOLO-World s", "frames": len(paths),
               "vocab": VOCAB, "categories": cats,
               "macro_recall": round(sum(macro) / len(macro), 4) if macro else None,
               "latency_ms_median": round(float(sorted(lat)[len(lat)//2]), 1) if lat else None,
               "model_mb": round((ROOT / "yolov8s-worldv2.pt").stat().st_size / 1e6, 1)}
    out = ROOT / "results"; out.mkdir(exist_ok=True)
    suffix = ("100" if limit else "full") + ("_v40" if len(VOCAB) > 20 else "")
    (out / f"blv_yoloworld_{suffix}.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    import sys
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 0)
