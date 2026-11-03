"""Build a YOLO door dataset from filtered OpenImages train annotations + images."""

import csv
import json
import random
import shutil
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ANN = ROOT / "data/openimages/door_train_ann.csv"
OUT = ROOT / "data/door_oi"
LIMIT = 800


def main():
    by = defaultdict(list)
    with ANN.open() as f:
        for r in csv.DictReader(f, fieldnames=["ImageID", "Source", "LabelName", "Confidence",
                                               "XMin", "XMax", "YMin", "YMax", "IsOccluded",
                                               "IsTruncated", "IsGroupOf", "IsDepiction", "IsInside"]):
            by[r["ImageID"]].append(r)
    ids = [i for i, b in by.items() if len(b) <= 6]
    random.Random(0).shuffle(ids)
    ids = ids[:LIMIT]
    (OUT / "images/train").mkdir(parents=True, exist_ok=True)
    (OUT / "labels/train").mkdir(parents=True, exist_ok=True)
    def one(i):
        dst = OUT / "images/train" / f"{i}.jpg"
        if not dst.exists():
            try:
                urllib.request.urlretrieve(f"https://open-images-dataset.s3.amazonaws.com/train/{i}.jpg", dst)
            except Exception:
                return 0
        out = []
        for r in by[i]:
            x1, x2 = float(r["XMin"]), float(r["XMax"])
            y1, y2 = float(r["YMin"]), float(r["YMax"])
            out.append(f"0 {(x1+x2)/2:.6f} {(y1+y2)/2:.6f} {x2-x1:.6f} {y2-y1:.6f}")
        (OUT / "labels/train" / f"{i}.txt").write_text("\n".join(out) + "\n")
        return 1
    with ThreadPoolExecutor(max_workers=24) as ex:
        ok = sum(ex.map(one, ids))
    print("downloaded", ok)
    (OUT / "data.yaml").write_text(
        f"path: {OUT}\ntrain: images/train\nval: images/train\nnames:\n  0: Door\n")


if __name__ == "__main__":
    main()
