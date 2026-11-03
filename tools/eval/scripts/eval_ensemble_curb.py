"""Ensemble v5 SAFE with v6 BOUNDARY: keep v5 corridor, add v6 curb labels."""

import json
import sys
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F
from PIL import Image
from transformers.models.segformer import SegformerForSemanticSegmentation

sys.path.insert(0, str(Path(__file__).resolve().parent))
from eval_segformer_v2 import DATA, FROZEN, gt_remap, load_image, metrics, summarize

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    ck = torch.load(ROOT / "models" / name, map_location="cpu")
    m = SegformerForSemanticSegmentation.from_pretrained(ck["model"], num_labels=5, ignore_mismatched_sizes=True).eval()
    m.load_state_dict(ck["state_dict"])
    return m


def pred(m, x, shape):
    with torch.no_grad():
        o = m(pixel_values=x).logits
        return F.interpolate(o, size=shape, mode="bilinear", align_corners=False).argmax(1)[0].numpy()


def main():
    v5 = load("segformer_sanpo_b0_v5.pt")
    v6 = load("segformer_sanpo_b0_v6.pt")
    sessions = [p.name for p in sorted(DATA.iterdir()) if p.is_dir()]
    real = [s for s in sessions if s not in FROZEN][21:27]
    out = {}
    for label, sids in [("frozen45", list(FROZEN)), ("real_v2", real)]:
        rows = []
        for sid in sids:
            for fp in sorted((DATA / sid / "video_frames").glob("*.png"))[:20]:
                mp = DATA / sid / "segmentation_masks" / fp.name
                if not mp.exists():
                    continue
                gt = gt_remap(np.array(Image.open(mp))[:, :, 0])
                x = load_image(fp, 512, 288)
                p5 = pred(v5, x, gt.shape)
                p6 = pred(v6, x, gt.shape)
                curb = p6 == 2
                curb = torch.nn.functional.max_pool2d(torch.from_numpy(curb.astype(np.float32))[None, None], 5, 1, 2)[0, 0].numpy() > .5
                p = p5.copy(); p[curb] = 2
                v, iou, rec, cr, swc, rfs, off, gtoff = metrics(p, gt)
                rows.append({"session": sid, "valid": v, "safe_iou": iou, "safe_recall": rec, "curb_recall": cr,
                             "sidewalk_as_cond": swc, "road_false_safe": rfs, "off": off, "gt_off": gtoff})
        out[label] = summarize(rows, f"ensemble_{label}")
    (ROOT / "results" / "segformer_ensemble_curb.json").write_text(json.dumps(out, indent=1))
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
