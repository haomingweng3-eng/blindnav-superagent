#!/usr/bin/env python3
"""Read-only root-cause and single-layer ablation audit for Mobilio replay CSV."""
from __future__ import annotations
import argparse, csv, json, os
from collections import Counter

GUIDANCE = {"STRAIGHT", "LEFT", "RIGHT"}

def _b(v): return str(v).lower() in {"1", "true", "yes"}

def audit(rows):
    out = {}
    for cond in sorted({r["condition"] for r in rows}):
        x = [r for r in rows if r["condition"] == cond and r["arm"] == "mobilio"]
        unknown = [r for r in x if r.get("safety_state") == "UNKNOWN"]
        causes = Counter()
        for r in unknown:
            s, o, reason = r.get("surface_state"), r.get("occupancy_state"), r.get("reason", "")
            if r.get("calib_ok") not in {"True", "true", "1"}:
                causes["depth_or_ground_calibration"] += 1
            if s == "UNKNOWN" or "surface:" in reason:
                causes["surface_grid"] += 1
            if o == "UNKNOWN" or "occupancy:" in reason:
                causes["occupancy_grid"] += 1
            if s == "UNKNOWN" and o == "UNKNOWN":
                causes["both_maps"] += 1
            if s == "CLEAR" and o == "UNKNOWN":
                causes["occupancy_only"] += 1
            if s == "UNKNOWN" and o in {"CLEAR", "OBSERVE"}:
                causes["surface_only"] += 1
            if "no_metric_depth" in reason:
                causes["no_metric_depth"] += 1
        surface_clear = sum(r.get("surface_state") == "CLEAR" for r in x)
        occupancy_clear = sum(r.get("occupancy_state") == "CLEAR" for r in x)
        surface_guidance = sum(r.get("surface_state") == "CLEAR" and r.get("surface_angle_rad") not in (None, "") for r in x)
        out[cond] = {
            "frames": len(x), "unknown_frames": len(unknown),
            "unknown_causes_multi_label": dict(causes),
            "surface_only_ablation_clear_frames": surface_clear,
            "surface_only_ablation_guidance_candidates": surface_guidance,
            "occupancy_only_ablation_clear_frames": occupancy_clear,
            "authorized_motion_frames": sum(r.get("authorized_motion") in GUIDANCE for r in x),
            "observation_hint_frames": sum(r.get("observation_hint") in GUIDANCE for r in x),
        }
    return out

def main(argv=None):
    ap = argparse.ArgumentParser(); ap.add_argument("--per-frame", required=True); ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    with open(a.per_frame, newline="", encoding="utf-8") as f: result = audit(list(csv.DictReader(f)))
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f: json.dump(result, f, indent=2, sort_keys=True)
    print(json.dumps(result, indent=2, sort_keys=True)); return 0
if __name__ == "__main__": raise SystemExit(main())
