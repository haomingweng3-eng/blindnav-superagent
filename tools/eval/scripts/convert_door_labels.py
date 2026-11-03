"""Convert Roboflow polygon door labels to YOLO bbox format."""

import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "data" / "doors_kaggle"
DST = ROOT / "data" / "door_yolo"
for split, target in [("train", "train"), ("valid", "val"), ("test", "test")]:
    (DST / "images" / target).mkdir(parents=True, exist_ok=True)
    (DST / "labels" / target).mkdir(parents=True, exist_ok=True)
    for img in (SRC / split / "images").glob("*.jpg"):
        shutil.copy2(img, DST / "images" / target / img.name)
    for lab in (SRC / split / "labels").glob("*.txt"):
        out = []
        for line in lab.read_text().splitlines():
            v = line.split()
            if len(v) < 5:
                continue
            cls = int(float(v[0]))
            pts = [float(x) for x in v[1:]]
            xs, ys = pts[0::2], pts[1::2]
            x1, x2, y1, y2 = min(xs), max(xs), min(ys), max(ys)
            out.append(f"{cls} {(x1+x2)/2:.6f} {(y1+y2)/2:.6f} {x2-x1:.6f} {y2-y1:.6f}")
        (DST / "labels" / target / lab.name).write_text("\n".join(out) + ("\n" if out else ""))
(DST / "data.yaml").write_text(f"path: {DST}\ntrain: images/train\nval: images/val\ntest: images/test\nnames:\n  0: Door\n")
print("done", DST)
