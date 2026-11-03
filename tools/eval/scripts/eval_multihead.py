"""Evaluate multi-head SAFE/ROAD + CURB model."""

import io
import json
import sys
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq
import torch
import torch.nn.functional as F
from PIL import Image
from transformers.models.segformer.modeling_segformer import SegformerDecodeHead, SegformerModel

sys.path.insert(0, str(Path(__file__).resolve().parent))
from eval_segformer_v2 import DATA, FROZEN, gt_remap, load_image, metrics, summarize
from train_multihead import MultiHead
from train_sanpo_segformer import MODEL

ROOT = Path(__file__).resolve().parents[1]
THS = [.3, .5, .7]


def load(path):
    ck = torch.load(path, map_location="cpu")
    m = MultiHead.__new__(MultiHead)
    torch.nn.Module.__init__(m)
    m.backbone = SegformerModel.from_pretrained(MODEL)
    m.backbone.load_state_dict({k[len("segformer."):]: v for k, v in ck["backbone"].items()
                                if k.startswith("segformer.")}, strict=False)
    from transformers import SegformerConfig
    cfg = SegformerConfig.from_pretrained(MODEL); cfg.num_labels = 3
    m.head_main = SegformerDecodeHead(cfg)
    cfg2 = SegformerConfig.from_pretrained(MODEL); cfg2.num_labels = 2
    m.head_curb = SegformerDecodeHead(cfg2)
    m.head_main.load_state_dict(ck["head_main"]); m.head_curb.load_state_dict(ck["head_curb"])
    m.eval(); return m


def load_pil(im, w=512, h=288):
    im = im.convert("RGB").resize((w, h), Image.BILINEAR)
    x = np.asarray(im, np.float32) / 255.
    x = (x - np.array([.485, .456, .406], np.float32)) / np.array([.229, .224, .225], np.float32)
    return torch.from_numpy(x.transpose(2, 0, 1))[None]


def infer(m, x, shape):
    with torch.no_grad():
        main, curb = m(x)
    main = F.interpolate(main, size=shape, mode="bilinear", align_corners=False)[0]
    curb = F.interpolate(curb, size=shape, mode="bilinear", align_corners=False)[0]
    p = main.argmax(0).numpy().astype(np.uint8)
    p = np.select([p == 1, p == 2], [1, 3], 4).astype(np.uint8)
    return p, torch.softmax(curb, 0)[1].numpy()


def main():
    m = load(ROOT / "models" / "segformer_multihead_v1.pt")
    sessions = [p.name for p in sorted(DATA.iterdir()) if p.is_dir()]
    real = [s for s in sessions if s not in FROZEN][21:27]
    out = {}
    for label, sids in [("frozen45", list(FROZEN)), ("real_v2", real)]:
        rows = {th: [] for th in THS}
        for sid in sids:
            for fp in sorted((DATA / sid / "video_frames").glob("*.png"))[:20]:
                mp = DATA / sid / "segmentation_masks" / fp.name
                if not mp.exists():
                    continue
                gt = gt_remap(np.array(Image.open(mp))[:, :, 0])
                x = load_image(fp, 512, 288)
                p, cp = infer(m, x, gt.shape)
                for th in THS:
                    pred = p.copy(); pred[cp >= th] = 2
                    v, iou, rec, cr, swc, rfs, off, gtoff = metrics(pred, gt)
                    rows[th].append({"session": sid, "valid": v, "safe_iou": iou, "safe_recall": rec,
                                     "curb_recall": cr, "sidewalk_as_cond": swc,
                                     "road_false_safe": rfs, "off": off, "gt_off": gtoff})
        out[label] = {str(th): summarize(rows[th], f"{label}_{th}") for th in THS}

    t = pq.ParquetFile(ROOT / "data/cityscapes/test0.parquet").read_row_group(0).to_pydict()
    rows = {th: [] for th in THS}
    for i in range(min(100, len(t["image"]))):
        im = Image.open(io.BytesIO(t["image"][i]["bytes"])).convert("RGB")
        a = np.array(Image.open(io.BytesIO(t["semantic_segmentation"][i]["bytes"])))[:, :, 0]
        gt = np.full(a.shape, 4, np.uint8); gt[a == 1] = 1; gt[np.isin(a, [0, 9])] = 3; gt[a == 255] = 0
        x = load_pil(im)
        p, cp = infer(m, x, gt.shape)
        for th in THS:
            pred = p.copy(); pred[cp >= th] = 2
            v, iou, rec, cr, swc, rfs, off, gtoff = metrics(pred, gt)
            rows[th].append({"session": "city", "valid": v, "safe_iou": iou, "safe_recall": rec,
                             "curb_recall": None, "sidewalk_as_cond": swc,
                             "road_false_safe": rfs, "off": off, "gt_off": gtoff})
    out["cityscapes_val"] = {str(th): summarize(rows[th], f"city_{th}") for th in THS}
    (ROOT / "results" / "multihead_eval.json").write_text(json.dumps(out, indent=1))
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
