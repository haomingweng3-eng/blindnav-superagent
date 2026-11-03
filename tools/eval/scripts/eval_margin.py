"""Marginal SAFE gating on v5: require SAFE logit to beat CONDITIONAL by a margin."""

import io
import json
import sys
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq
import torch
import torch.nn.functional as F
from PIL import Image
from transformers.models.segformer import SegformerForSemanticSegmentation

sys.path.insert(0, str(Path(__file__).resolve().parent))
from eval_segformer_v2 import DATA, FROZEN, gt_remap, load_image, metrics

ROOT = Path(__file__).resolve().parents[1]
CKPT = ROOT / "models" / "segformer_sanpo_b0_v5.pt"
MARGINS = [0.0, .5, 1.0, 1.5, 2.0, 3.0]


def summarize(rows):
    def mean(k):
        vals = [r[k] for r in rows if r[k] is not None]
        return round(float(np.mean(vals)), 4) if vals else None
    return {"frames": len(rows), "valid_rate": mean("valid"), "safe_iou": mean("safe_iou"),
            "safe_recall": mean("safe_recall"), "curb_recall": mean("curb_recall"),
            "road_false_safe": mean("road_false_safe")}


def main():
    ck = torch.load(CKPT, map_location="cpu")
    m = SegformerForSemanticSegmentation.from_pretrained(ck["model"], num_labels=5, ignore_mismatched_sizes=True).eval()
    m.load_state_dict(ck["state_dict"])
    sessions = [p.name for p in sorted(DATA.iterdir()) if p.is_dir()]
    real = [s for s in sessions if s not in FROZEN][21:27]
    out = {}
    for label, sids in [("frozen45", list(FROZEN)), ("real_v2", real)]:
        per_margin = {x: [] for x in MARGINS}
        for sid in sids:
            for fp in sorted((DATA / sid / "video_frames").glob("*.png"))[:20]:
                mp = DATA / sid / "segmentation_masks" / fp.name
                if not mp.exists():
                    continue
                gt = gt_remap(np.array(Image.open(mp))[:, :, 0])
                x = load_image(fp, gt.shape[1], gt.shape[0])
                with torch.no_grad():
                    logits = m(pixel_values=x).logits
                    logits = F.interpolate(logits, size=gt.shape, mode="bilinear", align_corners=False)[0]
                for mg in MARGINS:
                    pred = logits.argmax(0).numpy()
                    gate = (logits[1] - logits[3]).numpy() < mg
                    pred[gate] = 3
                    v, iou, rec, curb, swc, rfs, off, gtoff = metrics(pred, gt)
                    per_margin[mg].append({"valid": v, "safe_iou": iou, "safe_recall": rec,
                                           "curb_recall": curb, "road_false_safe": rfs,
                                           "off": off, "gt_off": gtoff})
        out[label] = {str(mg): summarize(per_margin[mg]) for mg in MARGINS}

    t = pq.ParquetFile(ROOT / "data/cityscapes/test0.parquet").read_row_group(0).to_pydict()
    per_margin = {x: [] for x in MARGINS}
    for i in range(min(100, len(t["image"]))):
        im = Image.open(io.BytesIO(t["image"][i]["bytes"])).convert("RGB")
        a = np.array(Image.open(io.BytesIO(t["semantic_segmentation"][i]["bytes"])))[:, :, 0]
        gt = np.full(a.shape, 4, np.uint8); gt[a == 1] = 1; gt[np.isin(a, [0, 9])] = 3; gt[a == 255] = 0
        im2 = im.resize((512, 288), Image.BILINEAR)
        x = np.asarray(im2, np.float32) / 255.
        x = (x - np.array([.485, .456, .406], np.float32)) / np.array([.229, .224, .225], np.float32)
        x = torch.from_numpy(x.transpose(2, 0, 1))[None]
        with torch.no_grad():
            logits = m(pixel_values=x).logits
            logits = F.interpolate(logits, size=gt.shape, mode="bilinear", align_corners=False)[0]
        for mg in MARGINS:
            pred = logits.argmax(0).numpy(); pred[(logits[1] - logits[3]).numpy() < mg] = 3
            v, iou, rec, curb, swc, rfs, off, gtoff = metrics(pred, gt)
            per_margin[mg].append({"valid": v, "safe_iou": iou, "safe_recall": rec,
                                   "curb_recall": None, "road_false_safe": rfs, "off": off, "gt_off": gtoff})
    out["cityscapes_val"] = {str(mg): summarize(per_margin[mg]) for mg in MARGINS}
    (ROOT / "results" / "segformer_margin_v5.json").write_text(json.dumps(out, indent=1))
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
