"""Ground-plane depth comparison occupancy: point closer than ground is an obstacle."""

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
DW, DH = 320, 288


def main():
    tx, ty, tz = [np.loadtxt(RUN / "calibration" / f"ray_{n}.csv", delimiter=",", dtype=np.float32) for n in "xyz"]
    ck = torch.load(CKPT, map_location="cpu")
    seg = SegformerForSemanticSegmentation.from_pretrained(
        ck["model"], num_labels=5, ignore_mismatched_sizes=True).eval()
    seg.load_state_dict(ck["state_dict"])
    _, dets = load_gt()
    rgb = [json.loads(x) for x in (RUN / "rgb_frames.jsonl").open()]
    depth = [json.loads(x) for x in (RUN / "depth_frames.jsonl").open()]
    first = rgb[0]["ts_unix_ns"]; dts = [d["ts_unix_ns"] for d in depth]

    def nearest(ts):
        i = bisect.bisect_left(dts, ts)
        j = min((k for k in (i - 1, i, i + 1) if 0 <= k < len(depth)), key=lambda k: abs(dts[k] - ts))
        return depth[j]

    rows = []
    prev_obs = []
    for idx, fp in enumerate(sorted((RUN / "clip_375").glob("*.jpg"))):
        t = 375 + idx * .1
        recs = dets.get(min(dets.keys(), key=lambda k: abs(k - t)), []) if dets else []
        gt = bool(gt_blocking(recs))
        d = nearest(first + int(t * 1e9)); dp = RUN / "depth" / f"{d['frame']:06d}.png"
        if not dp.exists():
            continue
        dep = np.array(Image.open(dp)).astype(np.float32) / 1000.
        valid = dep > 0
        if valid.sum() < 200:
            continue
        pred = seg(pixel_values=load_image(fp, DW, DH)).logits
        pred = torch.nn.functional.interpolate(pred, size=(DH, DW), mode="bilinear", align_corners=False)
        safe = pred.argmax(1)[0].numpy() == 1
        safe = torch.nn.functional.max_pool2d(torch.from_numpy(safe.astype(np.float32))[None, None], 5, 1, 2)[0, 0].numpy() > .5

        M = np.array(d["pose"], np.float32).reshape(4, 4)
        R, tr = M[:3, :3], M[3, :3]
        wx = tx * R[0, 0] + ty * R[1, 0] + tz * R[2, 0]
        wy = tx * R[0, 1] + ty * R[1, 1] + tz * R[2, 1]
        wz = tx * R[0, 2] + ty * R[1, 2] + tz * R[2, 2]
        pc = np.stack([tx * dep, ty * dep, tz * dep], -1)
        pw = pc @ R + tr

        yy = np.arange(DH)[:, None]
        bottom = (yy > DH * .7) & valid & (pc[..., 2] > .8) & (pc[..., 2] < 3.)
        if bottom.sum() < 50:
            continue
        bx, bz, by = pw[..., 0][bottom], pw[..., 2][bottom], pw[..., 1][bottom]
        A = np.stack([bx, bz, np.ones_like(bx)], 1)
        coef, *_ = np.linalg.lstsq(A, by, rcond=None)
        r0 = by - A @ coef; keep = np.abs(r0) < .15
        if keep.sum() >= 50:
            coef, *_ = np.linalg.lstsq(A[keep], by[keep], rcond=None)
        a, b, c = coef
        den = wy - a * wx - b * wz
        rhs = a * tr[0] + b * tr[2] + c - tr[1]
        tg = np.where(np.abs(den) > 1e-6, rhs / den, np.inf)
        z = pc[..., 2]; x = pc[..., 0]
        corridor = (z > .5) & (z < 2.2) & (np.abs(x) < .5)
        obstacle = valid & corridor & (np.abs(tg - dep) > 0) & (dep < tg - .25) & (tg > .5) & (tg < 6)
        frac = float((obstacle & (z < 2.0)).sum() / max(1, corridor.sum()))
        safe_corr = corridor & safe
        frac_safe = float((obstacle & (z < 2.0) & safe).sum() / max(1, safe_corr.sum()))
        near = obstacle & (z < 2.0)
        p1 = prev_obs[-1] if prev_obs else np.zeros_like(near)
        p2 = prev_obs[-2] if len(prev_obs) > 1 else np.zeros_like(near)
        frac_p2 = float((near & p1).sum() / max(1, corridor.sum()))
        frac_p3 = float((near & p1 & p2).sum() / max(1, corridor.sum()))
        prev_obs.append(near); prev_obs = prev_obs[-3:]
        rows.append({"i": idx, "t": t, "gt": gt, "frac": frac, "frac_safe": frac_safe,
                     "frac_p2": frac_p2, "frac_p3": frac_p3,
                     "tg_med": float(np.median(tg[valid & np.isfinite(tg)]))})

    for i, r in enumerate(rows):
        seq = [rows[j]["frac"] for j in range(max(0, i - 1), i + 1)]
        seq_s = [rows[j]["frac_safe"] for j in range(max(0, i - 1), i + 1)]
        r["occ"] = r["frac"] >= .03
        r["occ_t2"] = len(seq) == 2 and all(v >= .03 for v in seq)
        r["occ_safe"] = r["frac_safe"] >= .03
        r["occ_safe_t2"] = len(seq_s) == 2 and all(v >= .03 for v in seq_s)
        r["occ_p2"] = r["frac_p2"] >= .02
        r["occ_p3"] = r["frac_p3"] >= .015

    def score(key):
        tp = sum(r["gt"] and r[key] for r in rows); fp = sum(not r["gt"] and r[key] for r in rows)
        fn = sum(r["gt"] and not r[key] for r in rows); neg = len(rows) - sum(r["gt"] for r in rows)
        return {"frames": len(rows), "gt_blocking": sum(r["gt"] for r in rows),
                "recall": round(tp / max(1, tp + fn), 4),
                "precision": round(tp / max(1, tp + fp), 4) if tp + fp else None,
                "false_positive_rate": round(fp / max(1, neg), 4)}

    sweep = []
    for th in [.01, .02, .03, .05, .08, .12]:
        for key in ("frac", "frac_safe"):
            for r in rows:
                r["_k"] = r[key] >= th
            s = score("_k")
            sweep.append({"threshold": th, "source": key, **s})

    out = {"dataset": "UrbanEgo 375-400s; ground-plane depth residual, no instance model",
           "ckpt": CKPT.name, "frac_median": round(float(np.median([r["frac"] for r in rows])), 4),
           "frac_gt_median": round(float(np.median([r["frac"] for r in rows if r["gt"]])), 4),
           "frac_nogt_median": round(float(np.median([r["frac"] for r in rows if not r["gt"]])), 4),
           "ground_depth_occ": score("occ"), "ground_depth_occ_t2": score("occ_t2"),
           "ground_depth_safe_occ": score("occ_safe"), "ground_depth_safe_occ_t2": score("occ_safe_t2"),
           "ground_depth_pixel_t2": score("occ_p2"), "ground_depth_pixel_t3": score("occ_p3"),
           "sweep": sweep,
           "note": "class-independent; unknown-object recall not measurable; 3% area a priori"}
    (ROOT / "results" / "occupancy_ground.json").write_text(json.dumps(out, indent=1))
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
