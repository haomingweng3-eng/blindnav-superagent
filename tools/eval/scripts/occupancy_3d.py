"""3D occupancy from metric depth rays + pose: ground removal, corridor voxels, temporal."""

import bisect
import json
import sys
from pathlib import Path

import numpy as np
import torch
from PIL import Image
from transformers.models.segformer import SegformerForSemanticSegmentation

sys.path.insert(0, str(Path(__file__).resolve().parent))
from eval_segformer_v2 import load_image
from urbanego_obstacle import RUN, gt_blocking, load_gt

ROOT = Path(__file__).resolve().parents[1]
CKPT = ROOT / "models" / "segformer_sanpo_b0_v3.pt"
if not CKPT.exists():
    CKPT = ROOT / "models" / "segformer_sanpo_b0_v2.pt"
DW, DH = 320, 288


def load_rays():
    p = RUN / "calibration"
    return [np.loadtxt(p / f"ray_{n}.csv", delimiter=",", dtype=np.float32) for n in "xyz"]


def main():
    rx, ry, rz = load_rays()
    ck = torch.load(CKPT, map_location="cpu")
    seg = SegformerForSemanticSegmentation.from_pretrained(
        ck["model"], num_labels=5, ignore_mismatched_sizes=True).eval()
    seg.load_state_dict(ck["state_dict"])

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
    prev_cells = set()
    for idx, fp in enumerate(sorted((RUN / "clip_375").glob("*.jpg"))):
        t = 375.0 + idx * 0.1
        recs = dets.get(min(dets.keys(), key=lambda k: abs(k - t)), []) if dets else []
        gt = bool(gt_blocking(recs))
        d = nearest_depth(first + int(t * 1e9))
        dp = RUN / "depth" / f"{d['frame']:06d}.png"
        if not dp.exists():
            continue
        dep = np.array(Image.open(dp)).astype(np.float32) / 1000.0
        valid = dep > 0
        if valid.sum() < 200:
            prev_cells = set()
            continue

        pred = seg(pixel_values=load_image(fp, DW, DH)).logits
        pred = torch.nn.functional.interpolate(pred, size=(DH, DW), mode="bilinear", align_corners=False)
        safe = pred.argmax(1)[0].numpy() == 1
        safe = torch.nn.functional.max_pool2d(
            torch.from_numpy(safe.astype(np.float32))[None, None], 5, 1, 2)[0, 0].numpy() > .5

        M = np.array(d["pose"], np.float32).reshape(4, 4)
        pc = np.stack([rx * dep, ry * dep, rz * dep], -1)  # camera frame, metres
        pw = pc @ M[:3, :3] + M[3, :3]                     # world frame

        yy = np.arange(DH)[:, None]
        bottom = (yy > DH * .7) & valid & (pc[..., 2] > .8) & (pc[..., 2] < 3.0)
        if bottom.sum() >= 50:
            bx, bz, by = pw[..., 0][bottom], pw[..., 2][bottom], pw[..., 1][bottom]
            A = np.stack([bx, bz, np.ones_like(bx)], 1)
            coef, *_ = np.linalg.lstsq(A, by, rcond=None)
            resid = by - A @ coef
            keep = np.abs(resid) < .15
            if keep.sum() >= 50:
                coef, *_ = np.linalg.lstsq(A[keep], by[keep], rcond=None)
            plane = coef[0] * pw[..., 0] + coef[1] * pw[..., 2] + coef[2]
            height = pw[..., 1] - plane
        else:
            height = pw[..., 1] - float(np.percentile(pw[..., 1][valid], 3))
        ground = float(np.median(pw[..., 1][bottom])) if bottom.sum() >= 50 else 0.0
        z = pc[..., 2]
        x = pc[..., 0]
        corridor = (z > .5) & (z < 2.0) & (np.abs(x) < .35)
        obstacle = valid & corridor & (height > .15) & (height < 1.6)
        safe_obstacle = obstacle & safe

        def cells(mask):
            out = set()
            ys, xs = np.where(mask)
            for xx, zz in zip(x[ys, xs], z[ys, xs]):
                out.add((round(float(xx) / .1), round(float(zz) / .1)))
            return out

        c_all = cells(obstacle)
        c_safe = cells(safe_obstacle)
        both = c_all & prev_cells
        both_safe = c_safe & prev_cells
        prev_cells = c_all

        rows.append({"i": idx, "t": t, "gt": gt,
                     "occ": len(c_all) >= 5, "occ_safe": len(c_safe) >= 5,
                     "occ_t2": len(both) >= 3, "occ_safe_t2": len(both_safe) >= 3,
                     "cells": len(c_all), "cells_safe": len(c_safe),
                     "ground": ground, "valid_frac": float(valid.mean())})

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
        "dataset": "UrbanEgo clip 375-400s; metric depth -> 3D world points, ground removal, corridor voxels 0.1m",
        "ckpt": CKPT.name,
        "mean_valid_depth": round(float(np.mean([r["valid_frac"] for r in rows])), 4),
        "cells_median": round(float(np.median([r["cells"] for r in rows])), 1),
        "cells_p90": round(float(np.percentile([r["cells"] for r in rows], 90)), 1),
        "cells_gt_median": round(float(np.median([r["cells"] for r in rows if r["gt"]])), 1),
        "cells_nogt_median": round(float(np.median([r["cells"] for r in rows if not r["gt"]])), 1),
        "ground_p10_p90": [round(float(np.percentile([r["ground"] for r in rows], 10)), 2),
                           round(float(np.percentile([r["ground"] for r in rows], 90)), 2)],
        "corridor_3d": score("occ"),
        "corridor_3d_temporal": score("occ_t2"),
        "safe_corridor_3d": score("occ_safe"),
        "safe_corridor_3d_temporal": score("occ_safe_t2"),
        "note": "class-independent; unknown-object recall not measurable; voxel counts 3 cells a priori",
    }
    (ROOT / "results" / "occupancy_3d.json").write_text(json.dumps(out, indent=1))
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
