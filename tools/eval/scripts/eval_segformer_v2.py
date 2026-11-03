"""Compare Cityscapes SegFormer vs SANPO-finetuned SegFormer on frozen + real_v2."""

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
from transformers.models.segformer import SegformerForSemanticSegmentation

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "sanpo"
CKPT = ROOT / "models" / os.getenv('CKPT','segformer_sanpo_b0.pt')
SUFFIX=os.getenv('SUFFIX','')
FROZEN = ["5LlqRK-hWoDLSW5MmoLjKj6uQtZMKjb9",
          "972O8sd5HpUbGeEE_UAb1g0z1OZUtfHl",
          "GxMb4zhAvoM5jbF54kfcs8wxTL4fqNnT"]
SAFE_GT = {3, 5, 6, 17}
BOUND_GT = {2}
COND_GT = {1, 30}


def gt_remap(mask):
    out = np.full(mask.shape, 0, np.uint8)
    out[np.isin(mask, list(SAFE_GT))] = 1
    out[np.isin(mask, list(BOUND_GT))] = 2
    out[np.isin(mask, list(COND_GT))] = 3
    out[(mask > 0) & (out == 0)] = 4
    return out


def load_image(fp, w, h):
    im = Image.open(fp).convert("RGB").resize((w, h), Image.BILINEAR)
    x = np.asarray(im, np.float32) / 255.0
    x = (x - np.array([0.485, 0.456, 0.406], np.float32)) / np.array([0.229, 0.224, 0.225], np.float32)
    return torch.from_numpy(x.transpose(2, 0, 1))[None]


def metrics(pred, gt):
    safe_p = pred == 1; safe_g = gt == 1
    inter = np.logical_and(safe_p, safe_g).sum(); union = np.logical_or(safe_p, safe_g).sum()
    safe_iou = float(inter / union) if union else 0.0
    safe_recall = float(inter / safe_g.sum()) if safe_g.sum() else 0.0
    curb_g = gt == 2; curb_p = pred == 2
    curb_recall = float(np.logical_and(curb_p, curb_g).sum() / curb_g.sum()) if curb_g.sum() else None
    sidewalk = gt == 1
    sidewalk_as_cond = float(np.isin(pred[sidewalk], [3, 4]).mean()) if sidewalk.sum() else None
    road = gt == 3
    road_false_safe = float((pred[road] == 1).mean()) if road.sum() else None
    h, w = pred.shape
    y0, y1 = int(h * .55), int(h * .98); x0, x1 = int(w * .2), int(w * .8)
    roi = pred[y0:y1, x0:x1]
    valid = float((roi == 1).mean() >= .25)
    by0, by1 = int(h * .75), int(h * .98)
    ys, xs = np.where(pred[by0:by1, :] == 1)
    off = float((xs.mean() - w / 2) / (w / 2)) if len(xs) >= 100 else None
    ys, xs = np.where(gt[by0:by1, :] == 1)
    gtoff = float((xs.mean() - w / 2) / (w / 2)) if len(xs) >= 100 else None
    return valid, safe_iou, safe_recall, curb_recall, sidewalk_as_cond, road_false_safe, off, gtoff


def run(model, sessions, mode, w, h):
    rows = []
    for sid in sessions:
        for fp in sorted((DATA / sid / "video_frames").glob("*.png"))[:20]:
            mp = DATA / sid / "segmentation_masks" / fp.name
            if not mp.exists(): continue
            gt = gt_remap(np.array(Image.open(mp))[:, :, 0])
            x = load_image(fp, w, h)
            with torch.no_grad():
                logits = model(pixel_values=x).logits
                logits = F.interpolate(logits, size=gt.shape, mode="bilinear", align_corners=False)
                pred = logits.argmax(1)[0].numpy()
            if mode == "city":
                remap = np.full(pred.shape, 4, np.uint8)
                remap[pred == 1] = 1
                remap[np.isin(pred, [0, 9])] = 3
                pred = remap
            m = metrics(pred, gt)
            rows.append({"session": sid, "frame": fp.stem, "valid": m[0], "safe_iou": m[1],
                         "safe_recall": m[2], "curb_recall": m[3], "sidewalk_as_cond": m[4],
                         "road_false_safe": m[5], "off": m[6], "gt_off": m[7]})
    return rows


def summarize(rows, name):
    offs = [r["off"] for r in rows if r["off"] is not None and r["gt_off"] is not None]
    errs = [abs(r["off"] - r["gt_off"]) for r in rows if r["off"] is not None and r["gt_off"] is not None]
    def mean(key):
        vals=[r[key] for r in rows if r[key] is not None]
        return round(float(np.mean(vals)),4) if vals else None
    jitters={}
    for s in {r["session"] for r in rows}:
        seq=[r["off"] for r in rows if r["session"]==s and r["off"] is not None]
        jitters[s]=round(float(np.mean(np.abs(np.diff(seq)))),4) if len(seq)>1 else None
    return {"name":name,"frames":len(rows),"valid_rate":mean("valid"),"safe_iou":mean("safe_iou"),
            "safe_recall":mean("safe_recall"),"curb_recall":mean("curb_recall"),
            "sidewalk_as_conditional":mean("sidewalk_as_cond"),"road_false_safe":mean("road_false_safe"),
            "center_error_median":round(float(statistics.median(errs)),4) if errs else None,
            "center_error_p90":round(float(np.percentile(errs,90)),4) if errs else None,
            "temporal_jitter":jitters}


def main():
    city = SegformerForSemanticSegmentation.from_pretrained(
        "nvidia/segformer-b0-finetuned-cityscapes-1024-1024").eval()
    ck = torch.load(CKPT, map_location="cpu")
    fine = SegformerForSemanticSegmentation.from_pretrained(
        ck["model"], num_labels=5, ignore_mismatched_sizes=True).eval()
    fine.load_state_dict(ck["state_dict"])
    out = ROOT / "results"; out.mkdir(exist_ok=True)
    all_rows=[]
    for label, sessions in [("frozen45", FROZEN), ("real_v2", [p.name for p in sorted(DATA.iterdir()) if p.is_dir() and p.name not in FROZEN][21:27])]:
        for model, mode, w, h, name in [(city,"city",512,512,"cityscapes"),(fine,"fine",512,288,"sanpo_finetune")]:
            rows=run(model,sessions,mode,w,h)
            s=summarize(rows,f"{label}_{name}")
            all_rows.append(s)
            print(json.dumps(s,ensure_ascii=False))
    json.dump(all_rows,open(out/f"segformer_v2_summary{SUFFIX}.json","w"),indent=1)


if __name__ == "__main__":
    main()
