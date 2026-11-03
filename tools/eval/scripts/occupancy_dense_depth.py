"""Class-independent occupancy with dense monocular depth + safety mask + temporal accumulation."""

import bisect
import json
import sys
from pathlib import Path

import numpy as np
import torch
from PIL import Image
from transformers import pipeline
from transformers.models.segformer import SegformerForSemanticSegmentation

sys.path.insert(0, str(Path(__file__).resolve().parent))
from eval_segformer_v2 import load_image
from urbanego_obstacle import RUN, gt_blocking, load_gt

ROOT = Path(__file__).resolve().parents[1]
W, H = 512, 288
CKPT = ROOT / "models" / "segformer_sanpo_b0_v3.pt"
if not CKPT.exists():
    CKPT = ROOT / "models" / "segformer_sanpo_b0_v2.pt"


def dilate(mask, k=5):
    m = torch.from_numpy(mask.astype(np.float32))[None, None]
    m = torch.nn.functional.max_pool2d(m, k, 1, k // 2)
    return m[0, 0].numpy() > .5


def main():
    ck = torch.load(CKPT, map_location="cpu")
    seg = SegformerForSemanticSegmentation.from_pretrained(
        ck["model"], num_labels=5, ignore_mismatched_sizes=True).eval()
    seg.load_state_dict(ck["state_dict"])
    import os
    depth_model = pipeline("depth-estimation", model="depth-anything/Depth-Anything-V2-Small-hf",
                           device=os.getenv("DEVICE", "mps" if torch.backends.mps.is_available() else "cpu"))

    _, dets = load_gt()
    rgb = [json.loads(x) for x in (RUN / "rgb_frames.jsonl").open()]
    depth = [json.loads(x) for x in (RUN / "depth_frames.jsonl").open()]
    first = rgb[0]["ts_unix_ns"]
    dts = [d["ts_unix_ns"] for d in depth]

    def nearest_depth(ts):
        i = bisect.bisect_left(dts, ts)
        j = min((k for k in (i - 1, i, i + 1) if 0 <= k < len(depth)),
                key=lambda k: abs(dts[k] - ts))
        return depth[j]

    rows = []
    for idx, fp in enumerate(sorted((RUN / "clip_375").glob("*.jpg"))):
        t = 375.0 + idx * 0.1
        recs = dets.get(min(dets.keys(), key=lambda k: abs(k - t)), []) if dets else []
        gt = bool(gt_blocking(recs))

        im = Image.open(fp).convert("RGB")
        pred = seg(pixel_values=load_image(fp, W, H)).logits
        pred = torch.nn.functional.interpolate(pred, size=(H, W), mode="bilinear", align_corners=False)
        safe = pred.argmax(1)[0].numpy() == 1

        out = depth_model(im)
        dense = out["predicted_depth"].detach().cpu().numpy()
        dense = np.asarray(Image.fromarray(dense.astype(np.float32)).resize((W, H), Image.BILINEAR))

        d = nearest_depth(first + int(t * 1e9))
        dp = RUN / "depth" / f"{d['frame']:06d}.png"
        scale = None
        cover = None
        if dp.exists():
            sparse = np.array(Image.open(dp).resize((W, H), Image.NEAREST)).astype(np.float32) / 1000.0
            valid = (sparse > 0) & (dense > .05)
            cover = float(valid.mean())
            if valid.sum() >= 50:
                ratio = sparse[valid] / dense[valid]
                ratio = ratio[(ratio > .05) & (ratio < 20)]
                if len(ratio) >= 50:
                    scale = float(np.median(ratio))
        metric = dense * scale if scale is not None else None

        ys, xs = np.mgrid[0:H, 0:W]
        corridor = (xs >= W * .2) & (xs <= W * .8) & (ys >= H * .55) & (ys <= H * .98)
        safe_corr = corridor & dilate(safe, 5)
        occ = (metric < 2.0) & (metric > 0) if metric is not None else np.zeros((H, W), bool)
        f_corr = float((occ & corridor).sum() / max(1, corridor.sum()))
        f_safe = float((occ & safe_corr).sum() / max(1, safe_corr.sum()))
        rows.append({
            "i": idx, "t": t, "gt": gt, "scale": scale, "depth_cover": cover,
            "safe_frac": float(safe.mean()), "corridor_occ": f_corr, "safe_occ": f_safe,
        })

    # temporal accumulation over 3 frames (majority)
    for i, r in enumerate(rows):
        for key in ("corridor_occ", "safe_occ"):
            seq = [rows[j][key] for j in range(max(0, i - 2), i + 1)]
            r[key + "_t2"] = all(v >= .05 for v in seq[-2:])
            r[key + "_t3"] = sum(v >= .05 for v in seq) >= 2

    def score(key):
        tp = sum(r["gt"] and r[key] for r in rows)
        fp = sum(not r["gt"] and r[key] for r in rows)
        fn = sum(r["gt"] and not r[key] for r in rows)
        neg = len(rows) - sum(r["gt"] for r in rows)
        return {"frames": len(rows), "gt_blocking": sum(r["gt"] for r in rows),
                "recall": round(tp / max(1, tp + fn), 4),
                "precision": round(tp / max(1, tp + fp), 4) if tp + fp else None,
                "false_positive_rate": round(fp / max(1, neg), 4)}

    out = {
        "dataset": "UrbanEgo clip 375-400s, 10fps; Depth-Anything-V2-Small dense depth + SANPO safety seg + temporal",
        "ckpt": str(CKPT.name),
        "depth_scale_frames": sum(1 for r in rows if r["scale"] is not None),
        "mean_sparse_depth_coverage": round(float(np.mean([r["depth_cover"] for r in rows if r["depth_cover"] is not None])), 4),
        "mean_safe_frac": round(float(np.mean([r["safe_frac"] for r in rows])), 4),
        "corridor_occ_now": score("corridor_occ"),
        "corridor_occ_t2": score("corridor_occ_t2"),
        "corridor_occ_t3": score("corridor_occ_t3"),
        "safe_occ_now": score("safe_occ"),
        "safe_occ_t2": score("safe_occ_t2"),
        "safe_occ_t3": score("safe_occ_t3"),
        "note": "class-independent; unknown-object recall not measurable without object GT; thresholds fixed a priori at 5% area, 2m",
    }
    (ROOT / "results" / "occupancy_dense_depth.json").write_text(json.dumps(out, indent=1))
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
