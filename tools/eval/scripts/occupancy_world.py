"""World-coordinate occupancy accumulation: project metric depth into world voxels across frames."""

import bisect
import json
import sys
from collections import deque
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
from urbanego_obstacle import RUN, gt_blocking, load_gt

ROOT = Path(__file__).resolve().parents[1]
VOX = .15


def main():
    rx, ry, rz = [np.loadtxt(RUN / "calibration" / f"ray_{n}.csv", delimiter=",", dtype=np.float32) for n in "xyz"]
    yy = np.arange(288)[:, None]
    _, dets = load_gt()
    rgb = [json.loads(x) for x in (RUN / "rgb_frames.jsonl").open()]
    depth = [json.loads(x) for x in (RUN / "depth_frames.jsonl").open()]
    first = rgb[0]["ts_unix_ns"]; dts = [d["ts_unix_ns"] for d in depth]

    def nearest(ts):
        i = bisect.bisect_left(dts, ts)
        j = min((k for k in (i - 1, i, i + 1) if 0 <= k < len(depth)), key=lambda k: abs(dts[k] - ts))
        return depth[j]

    history = deque(maxlen=8)
    rows = []
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
            history.append(set()); continue
        M = np.array(d["pose"], np.float32).reshape(4, 4)
        R, tr = M[:3, :3], M[3, :3]
        pc = np.stack([rx * dep, ry * dep, rz * dep], -1)
        pw = pc @ R + tr

        bottom = (yy > 288 * .7) & valid & (pc[..., 2] > .8) & (pc[..., 2] < 3.)
        if bottom.sum() < 50:
            history.append(set()); continue
        bx, bz, by = pw[..., 0][bottom], pw[..., 2][bottom], pw[..., 1][bottom]
        A = np.stack([bx, bz, np.ones_like(bx)], 1)
        coef, *_ = np.linalg.lstsq(A, by, rcond=None)
        r0 = by - A @ coef; keep = np.abs(r0) < .15
        if keep.sum() >= 50:
            coef, *_ = np.linalg.lstsq(A[keep], by[keep], rcond=None)
        a, b, c = coef
        height = pw[..., 1] - (a * pw[..., 0] + b * pw[..., 2] + c)
        z = pc[..., 2]; x = pc[..., 0]
        corridor = (z > .4) & (z < 1.8) & (np.abs(x) < .3)
        obs = valid & corridor & (height > .4) & (height < 1.6)
        ys, xs = np.where(obs)
        keys = set(zip(np.round(pw[..., 0][ys, xs] / VOX).astype(np.int32),
                       np.round(pw[..., 1][ys, xs] / VOX).astype(np.int32),
                       np.round(pw[..., 2][ys, xs] / VOX).astype(np.int32)))
        history.append(keys)
        counts = {}
        for s in history:
            for k in s:
                counts[k] = counts.get(k, 0) + 1

        def near_count(kmin):
            pts = np.array([k for k, v in counts.items() if v >= kmin], np.float32) * VOX
            if not len(pts):
                return 0
            pcm = (pts - tr) @ R.T
            sel = (pcm[:, 2] > .5) & (pcm[:, 2] < 2.0) & (np.abs(pcm[:, 0]) < .4)
            return int(sel.sum())

        rows.append({"i": idx, "t": t, "gt": gt, "k1": near_count(1), "k2": near_count(2),
                     "k3": near_count(3), "k5": near_count(5)})

    def score(key, th):
        tp = sum(r["gt"] and r[key] >= th for r in rows)
        fp = sum(not r["gt"] and r[key] >= th for r in rows)
        fn = sum(r["gt"] and r[key] < th for r in rows)
        neg = len(rows) - sum(r["gt"] for r in rows)
        return {"recall": round(tp / max(1, tp + fn), 4),
                "precision": round(tp / max(1, tp + fp), 4) if tp + fp else None,
                "false_positive_rate": round(fp / max(1, neg), 4)}

    out = {"dataset": "UrbanEgo 375-400s; world voxel accumulation 0.15m, 8-frame window",
           "frames": len(rows), "gt_blocking": sum(r["gt"] for r in rows),
           "variants": {}}
    for k in ("k1", "k2", "k3", "k5"):
        for th in (1, 2, 3, 4, 5):
            out["variants"][f"{k}_cells{th}"] = score(k, th)
    import os
    (ROOT / "results" / f"occupancy_world{os.getenv('SUFFIX','')}.json").write_text(json.dumps(out, indent=1))
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
