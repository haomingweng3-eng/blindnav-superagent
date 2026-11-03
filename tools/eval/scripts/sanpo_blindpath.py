"""Evaluate the production blind-path ONNX model on SANPO real frames."""

from __future__ import annotations

import csv
import json
import math
import statistics
from pathlib import Path

import numpy as np
import onnxruntime as ort
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
ASSET = Path("/Users/mima0000/blindnav-app/app/src/main/assets/yolo-seg-int8.onnx")
DATA = ROOT / "data" / "sanpo"
LABELS = {1: "road", 3: "sidewalk", 5: "crosswalk", 6: "paved_trail", 17: "other_walkable", 30: "terrain"}
STRICT = {3, 6, 17, 30}
INCLUSIVE = {1, 3, 5, 6, 17, 30}
SIZE = 640


def letterbox(im: Image.Image):
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


def nms(cands, thr=0.45):
    cands.sort(key=lambda c: -c[4])
    keep = []
    for a in cands:
        if all(not (a[5] == b[5] and iou(a[:4], b[:4]) > thr) for b in keep):
            keep.append(a)
    return keep


def run_model(sess, im):
    x, padX, padY, newW, newH = letterbox(im)
    out = sess.run(["output0", "output1"], {"images": x})
    boxes = out[0][0]  # 38 x 8400
    proto = out[1][0]  # 32 x 160 x 160
    n = 8400
    cand = []
    best_blind, best_blind_score = -1, 0.25
    best_cross = 0.0
    for i in range(n):
        s0, s1 = float(boxes[4, i]), float(boxes[5, i])
        score = max(s0, s1)
        if not math.isfinite(score) or score < 0.25:
            continue
        cls = 1 if s1 > s0 else 0
        cx, cy, w, h = (float(boxes[k, i]) for k in range(4))
        cand.append([cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2, score, cls, i])
        if cls == 1 and score > best_blind_score:
            best_blind, best_blind_score = i, score
        if cls == 0:
            best_cross = max(best_cross, score)
    nms(cand)
    crosswalk = best_cross >= 0.4
    if best_blind < 0:
        return False, None, crosswalk, 0.0
    cx = boxes[0, best_blind]; cy = boxes[1, best_blind]
    w = boxes[2, best_blind]; h = boxes[3, best_blind]
    band_start = padY + int(newH * 0.75)
    band_end = padY + int(newH * 1.0)
    x0 = max(0, math.ceil(max(padX, cx - w / 2) / 4))
    x1 = min(160, math.ceil(min(padX + newW, cx + w / 2) / 4))
    y0 = max(0, band_start // 4, math.ceil((cy - h / 2) / 4))
    y1 = min(160, band_end // 4, math.ceil((cy + h / 2) / 4))
    coeff = boxes[6:38, best_blind]
    sum_x = 0.0
    count = 0
    for y in range(y0, min(y1, 160)):
        for x in range(x0, x1):
            v = float(np.dot(coeff, proto[:, y, x]))
            if v > -20 and 1 / (1 + math.exp(-v)) > 0.5:
                sum_x += x * 4
                count += 1
    if count < 30:
        return False, None, crosswalk, best_blind_score
    center = padX + newW / 2
    return True, (sum_x / count - center) / (newW / 2), crosswalk, best_blind_score


def gt_stats(mask_path: Path):
    a = np.asarray(Image.open(mask_path))
    labels = a[:, :, 0]
    h, w = labels.shape
    # lower-center ROI used for corridor availability
    y0, y1 = int(h * 0.55), int(h * 0.98)
    x0, x1 = int(w * 0.20), int(w * 0.80)
    roi = labels[y0:y1, x0:x1]
    strict = np.isin(roi, list(STRICT)).mean()
    inclusive = np.isin(roi, list(INCLUSIVE)).mean()
    # center offset in the near band (bottom 25% of image), as in the model
    by0, by1 = int(h * 0.75), int(h * 0.98)
    band = labels[by0:by1, :]
    ys, xs = np.where(np.isin(band, list(STRICT)))
    offset = None
    if len(xs) >= 100:
        offset = (xs.mean() - w / 2) / (w / 2)
    return float(strict), float(inclusive), offset


def side(offset):
    if offset is None:
        return "unknown"
    if abs(offset) <= 0.12:
        return "center"
    return "left" if offset < 0 else "right"


def pct(vals, q):
    if not vals:
        return None
    vals = sorted(vals)
    return vals[min(len(vals) - 1, int(round(q * (len(vals) - 1))))]


def main():
    sess = ort.InferenceSession(str(ASSET), providers=["CPUExecutionProvider"])
    rows = []
    for session in sorted(DATA.iterdir()):
        if not session.is_dir():
            continue
        frames = sorted((session / "video_frames").glob("*.png"))
        for fp in frames:
            mp = session / "segmentation_masks" / fp.name
            if not mp.exists():
                continue
            im = Image.open(fp).convert("RGB")
            on_path, offset, cross, score = run_model(sess, im)
            strict, inclusive, gt_off = gt_stats(mp)
            rows.append({
                "session": session.name, "frame": fp.stem,
                "model_on_path": int(on_path), "model_offset": offset,
                "model_side": side(offset), "crosswalk": int(cross), "score": score,
                "gt_strict": strict, "gt_inclusive": inclusive,
                "gt_offset": gt_off, "gt_side": side(gt_off),
                "gt_available": int(strict >= 0.25),
            })
    out = ROOT / "results"
    out.mkdir(exist_ok=True)
    with (out / "sanpo_blindpath.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader(); w.writerows(rows)

    n = len(rows)
    gt_avail = [r for r in rows if r["gt_available"]]
    model_avail = [r for r in rows if r["model_on_path"]]
    both = [r for r in rows if r["gt_available"] and r["model_on_path"]]
    errors = [abs(r["model_offset"] - r["gt_offset"]) for r in both
              if r["model_offset"] is not None and r["gt_offset"] is not None]
    jitter = {}
    flips = {}
    for s in {r["session"] for r in rows}:
        seq = [r for r in rows if r["session"] == s]
        offs = [r["model_offset"] for r in seq if r["model_offset"] is not None]
        jitter[s] = round(float(np.mean(np.abs(np.diff(offs)))), 4) if len(offs) > 1 else None
        sides = [r["model_side"] for r in seq]
        flips[s] = sum(1 for a, b in zip(sides, sides[1:]) if a != b and a != "unknown" and b != "unknown")
    summary = {
        "dataset": "SANPO-Real, 3 test sessions x 15 frames",
        "frames": n,
        "model_valid_corridor_rate": round(len(model_avail) / n, 4),
        "gt_walkable_available_rate": round(len(gt_avail) / n, 4),
        "model_on_path_when_gt_available": round(len(both) / max(1, len(gt_avail)), 4),
        "model_on_path_when_gt_unavailable": round(
            sum(1 for r in rows if not r["gt_available"] and r["model_on_path"]) /
            max(1, n - len(gt_avail)), 4),
        "center_error_median": round(statistics.median(errors), 4) if errors else None,
        "center_error_p90": round(pct(errors, 0.9), 4) if errors else None,
        "temporal_jitter_by_session": jitter,
        "side_flips_by_session": flips,
        "gt_class_note": "strict = sidewalk/paved trail/other walkable/terrain; road excluded",
    }
    (out / "sanpo_blindpath_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
