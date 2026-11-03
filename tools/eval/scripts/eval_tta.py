"""Horizontal-flip TTA: SAFE only where both views agree, to cut road false-safe."""

import io
import json
import os
import sys
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq
import torch
import torch.nn.functional as F
from PIL import Image
from transformers.models.segformer import SegformerForSemanticSegmentation

sys.path.insert(0, str(Path(__file__).resolve().parent))
from eval_segformer_v2 import DATA, FROZEN, gt_remap, load_image, metrics, summarize

ROOT = Path(__file__).resolve().parents[1]
CKPT = ROOT / "models" / os.getenv("CKPT", "segformer_sanpo_b0_v5.pt")


def predict(model, x, size, flip):
    if flip:
        x = torch.flip(x, dims=[3])
    logits = model(pixel_values=x).logits
    logits = F.interpolate(logits, size=size, mode="bilinear", align_corners=False)
    if flip:
        logits = torch.flip(logits, dims=[3])
    return logits.argmax(1)[0].numpy()


def main():
    ck = torch.load(CKPT, map_location="cpu")
    m = SegformerForSemanticSegmentation.from_pretrained(ck["model"], num_labels=5, ignore_mismatched_sizes=True).eval()
    m.load_state_dict(ck["state_dict"])
    sessions = [p.name for p in sorted(DATA.iterdir()) if p.is_dir()]
    real = [s for s in sessions if s not in FROZEN][21:27]
    results = {}
    for label, sids in [("frozen45", list(FROZEN)), ("real_v2", real)]:
        rows = []
        for sid in sids:
            for fp in sorted((DATA / sid / "video_frames").glob("*.png"))[:20]:
                mp = DATA / sid / "segmentation_masks" / fp.name
                if not mp.exists():
                    continue
                gt = gt_remap(np.array(Image.open(mp))[:, :, 0])
                x = load_image(fp, 512, 288)
                p0 = predict(m, x, gt.shape, False)
                p1 = predict(m, x, gt.shape, True)
                pred = p0.copy()
                pred[p0 == 1] = np.where(p1[p0 == 1] == 1, 1, 3)
                v, iou, rec, curb, swc, rfs, off, gtoff = metrics(pred, gt)
                rows.append({"session": sid, "valid": v, "safe_iou": iou, "safe_recall": rec, "curb_recall": curb,
                             "sidewalk_as_cond": swc, "road_false_safe": rfs, "off": off, "gt_off": gtoff})
        results[label] = summarize(rows, f"tta_{label}")

    t = pq.ParquetFile(ROOT / "data/cityscapes/test0.parquet").read_row_group(0).to_pydict()
    rows = []
    for i in range(min(100, len(t["image"]))):
        im = Image.open(io.BytesIO(t["image"][i]["bytes"])).convert("RGB")
        a = np.array(Image.open(io.BytesIO(t["semantic_segmentation"][i]["bytes"])))[:, :, 0]
        gt = np.full(a.shape, 4, np.uint8); gt[a == 1] = 1; gt[np.isin(a, [0, 9])] = 3; gt[a == 255] = 0
        im2 = im.resize((512, 288), Image.BILINEAR)
        x = np.asarray(im2, np.float32) / 255.
        x = (x - np.array([.485, .456, .406], np.float32)) / np.array([.229, .224, .225], np.float32)
        x = torch.from_numpy(x.transpose(2, 0, 1))[None]
        p0 = predict(m, x, gt.shape, False); p1 = predict(m, x, gt.shape, True)
        pred = p0.copy(); pred[p0 == 1] = np.where(p1[p0 == 1] == 1, 1, 3)
        v, iou, rec, curb, swc, rfs, off, gtoff = metrics(pred, gt)
        rows.append({"session": "city", "valid": v, "safe_iou": iou, "safe_recall": rec, "curb_recall": curb,
                     "sidewalk_as_cond": swc, "road_false_safe": rfs, "off": off, "gt_off": gtoff})
    results["cityscapes_val"] = summarize(rows, "tta_cityscapes")

    (ROOT / "results" / "segformer_tta_v3.json").write_text(json.dumps(results, indent=1))
    print(json.dumps(results, indent=1))


if __name__ == "__main__":
    main()
