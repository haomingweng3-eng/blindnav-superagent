"""General traversability baseline: SegFormer-B0 Cityscapes on SANPO real frames."""

from __future__ import annotations

import csv
import json
import os
import statistics
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F
from PIL import Image
from transformers import SegformerImageProcessor
from transformers.models.segformer import SegformerForSemanticSegmentation

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "sanpo"
MODEL = os.getenv("SEGFORMER_MODEL", "nvidia/segformer-b0-finetuned-cityscapes-1024-1024")
SUFFIX = os.getenv("SEGFORMER_SUFFIX", "b0")
STRICT_GT = {3, 6, 17, 30}
STRICT_PRED = {1, 9}  # sidewalk, terrain
INCLUSIVE_PRED = {0, 1, 9}  # + road


def gt_stats(mask_path: Path):
    a = np.asarray(Image.open(mask_path))
    labels = a[:, :, 0]
    h, w = labels.shape
    y0, y1 = int(h * 0.55), int(h * 0.98)
    x0, x1 = int(w * 0.20), int(w * 0.80)
    roi = labels[y0:y1, x0:x1]
    strict = float(np.isin(roi, list(STRICT_GT)).mean())
    inclusive = float(np.isin(roi, list(STRICT_GT | {1, 5})).mean())
    by0, by1 = int(h * 0.75), int(h * 0.98)
    band = labels[by0:by1, :]
    ys, xs = np.where(np.isin(band, list(STRICT_GT)))
    off = float((xs.mean() - w / 2) / (w / 2)) if len(xs) >= 100 else None
    return strict, inclusive, off, labels


def pred_stats(pred, gt_labels):
    h, w = pred.shape
    y0, y1 = int(h * 0.55), int(h * 0.98)
    x0, x1 = int(w * 0.20), int(w * 0.80)
    roi = pred[y0:y1, x0:x1]
    strict = float(np.isin(roi, list(STRICT_PRED)).mean())
    inclusive = float(np.isin(roi, list(INCLUSIVE_PRED)).mean())
    by0, by1 = int(h * 0.75), int(h * 0.98)
    band = pred[by0:by1, :]
    ys, xs = np.where(np.isin(band, list(STRICT_PRED)))
    off = float((xs.mean() - w / 2) / (w / 2)) if len(xs) >= 100 else None
    # full-frame strict IoU against SANPO GT
    p = np.isin(pred, list(STRICT_PRED))
    g = np.isin(gt_labels, list(STRICT_GT))
    inter = np.logical_and(p, g).sum()
    union = np.logical_or(p, g).sum()
    iou = float(inter / union) if union else 0.0
    gt_side = np.isin(gt_labels, list(STRICT_GT))
    pred_side = pred == 1
    gt_side_n = gt_side.sum()
    sidewalk_recall = float(np.logical_and(gt_side, pred_side).sum() / gt_side_n) if gt_side_n else 0.0
    sidewalk_as_road = float(np.logical_and(gt_labels == 3, pred == 0).sum() / max(1, (gt_labels == 3).sum()))
    return strict, inclusive, off, iou, sidewalk_recall, sidewalk_as_road


def side(off):
    if off is None:
        return "unknown"
    if abs(off) <= 0.12:
        return "center"
    return "left" if off < 0 else "right"


def pct(vals, q):
    if not vals:
        return None
    vals = sorted(vals)
    return vals[min(len(vals) - 1, int(round(q * (len(vals) - 1))))]


def main():
    proc = SegformerImageProcessor.from_pretrained(MODEL)
    model = SegformerForSemanticSegmentation.from_pretrained(MODEL).eval()
    rows = []
    for session in sorted(DATA.iterdir()):
        if not session.is_dir():
            continue
        for fp in sorted((session / "video_frames").glob("*.png")):
            mp = session / "segmentation_masks" / fp.name
            if not mp.exists():
                continue
            im = Image.open(fp).convert("RGB")
            w, h = im.size
            gt_strict, gt_inclusive, gt_off, gt_labels = gt_stats(mp)
            inputs = proc(images=im, return_tensors="pt")
            with torch.no_grad():
                logits = model(**inputs).logits
                logits = F.interpolate(logits, size=(h, w), mode="bilinear", align_corners=False)
                pred = logits.argmax(1)[0].numpy().astype(np.uint8)
            p_strict, p_inclusive, p_off, iou, sw_recall, sw_as_road = pred_stats(pred, gt_labels)
            rows.append({
                "session": session.name, "frame": fp.stem,
                "gt_strict": gt_strict, "gt_inclusive": gt_inclusive, "gt_offset": gt_off,
                "pred_strict": p_strict, "pred_inclusive": p_inclusive, "pred_offset": p_off,
                "sidewalk_recall": sw_recall, "sidewalk_as_road": sw_as_road,
                "pred_side": side(p_off), "gt_side": side(gt_off),
                "iou_strict": iou,
                "gt_available": int(gt_strict >= 0.25),
                "pred_available": int(p_strict >= 0.25),
                "pred_inclusive_available": int(p_inclusive >= 0.25),
            })
    out = ROOT / "results"
    out.mkdir(exist_ok=True)
    with (out / f"sanpo_segformer_{SUFFIX}.csv").open("w", newline="") as f:
        wr = csv.DictWriter(f, fieldnames=list(rows[0]))
        wr.writeheader(); wr.writerows(rows)
    n = len(rows)
    gt_avail = [r for r in rows if r["gt_available"]]
    pred_avail = [r for r in rows if r["pred_available"]]
    both = [r for r in rows if r["gt_available"] and r["pred_available"]]
    errors = [abs(r["pred_offset"] - r["gt_offset"]) for r in both
              if r["pred_offset"] is not None and r["gt_offset"] is not None]
    jitter = {}
    for s in {r["session"] for r in rows}:
        seq = [r for r in rows if r["session"] == s]
        offs = [r["pred_offset"] for r in seq if r["pred_offset"] is not None]
        jitter[s] = round(float(np.mean(np.abs(np.diff(offs)))), 4) if len(offs) > 1 else None
    summary = {
        "dataset": "SANPO-Real, 3 test sessions x 15 frames",
        "model": MODEL,
        "frames": n,
        "gt_walkable_available_rate": round(len(gt_avail) / n, 4),
        "pred_valid_corridor_rate": round(len(pred_avail) / n, 4),
        "pred_inclusive_corridor_rate": round(sum(r["pred_inclusive_available"] for r in rows) / n, 4),
        "gt_inclusive_mean": round(statistics.mean(r["gt_inclusive"] for r in rows), 4),
        "sidewalk_pixel_recall": round(statistics.mean(r["sidewalk_recall"] for r in rows), 4),
        "sidewalk_predicted_as_road_rate": round(statistics.mean(r["sidewalk_as_road"] for r in rows), 4),
        "pred_valid_when_gt_available": round(len(both) / max(1, len(gt_avail)), 4),
        "pred_valid_when_gt_unavailable": round(
            sum(1 for r in rows if not r["gt_available"] and r["pred_available"]) /
            max(1, n - len(gt_avail)), 4),
        "strict_iou_median": round(statistics.median([r["iou_strict"] for r in rows]), 4),
        "center_error_median": round(statistics.median(errors), 4) if errors else None,
        "center_error_p90": round(pct(errors, 0.9), 4) if errors else None,
        "temporal_jitter_by_session": jitter,
    }
    (out / f"sanpo_segformer_{SUFFIX}_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
