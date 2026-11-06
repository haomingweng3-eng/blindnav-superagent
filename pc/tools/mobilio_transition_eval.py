#!/usr/bin/env python3
"""Independent, read-only transition analysis for a frozen Mobilio A/B CSV."""
from __future__ import annotations

import argparse, csv, json, os
from collections import Counter

WARNING = {"STOP", "LEFT", "RIGHT", "OBSERVE"}

def _bool(row, key):
    return str(row.get(key, "")).lower() in {"1", "true", "yes"}

def _warning(row):
    return str(row.get("effective_command", "")) in WARNING

def analyze(rows):
    by_key = {}
    for row in rows:
        by_key[(row["condition"], row["session"], row["frame"], row["arm"])] = row
    out = {}
    for condition in sorted({k[0] for k in by_key}):
        pairs = []
        for (c, s, f, arm), base in sorted(by_key.items()):
            if c != condition or arm != "baseline_split_c":
                continue
            cand = by_key.get((c, s, f, "mobilio"))
            if cand is not None:
                pairs.append((base, cand))
        counts = Counter()
        for base, cand in pairs:
            danger = _bool(base, "danger_any")
            clear = _bool(base, "confirmed_clear")
            bw, cw = _warning(base), _warning(cand)
            if danger and base.get("effective_command") == "STRAIGHT" and cw:
                counts["baseline_wrong_straight_to_candidate_warning"] += 1
            if danger and base.get("effective_command") == "STRAIGHT" and str(cand.get("safety_state")) == "UNKNOWN":
                counts["baseline_wrong_straight_to_candidate_unknown"] += 1
            if clear and base.get("effective_command") == "STRAIGHT" and str(cand.get("safety_state")) in {"UNKNOWN", "BLOCKED"}:
                counts["baseline_correct_straight_to_candidate_unknown_or_stop"] += 1
            if danger and bw and not cw:
                counts["baseline_correct_warning_to_candidate_miss"] += 1
            counts["danger_frames"] += int(danger)
            counts["confirmed_clear_frames"] += int(clear)
            counts["baseline_warning_on_danger"] += int(danger and bw)
            counts["candidate_warning_on_danger"] += int(danger and cw)
            counts["candidate_unknown_on_danger"] += int(danger and str(cand.get("safety_state")) == "UNKNOWN")
            counts["baseline_guidance_on_clear"] += int(clear and base.get("effective_command") == "STRAIGHT")
            counts["candidate_guidance_on_clear"] += int(clear and cand.get("effective_command") == "STRAIGHT")
        out[condition] = dict(counts)
        out[condition]["paired_frames"] = len(pairs)
    return out

def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-frame", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    with open(args.per_frame, newline="", encoding="utf-8") as fh:
        result = analyze(list(csv.DictReader(fh)))
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(result, fh, indent=2, sort_keys=True)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
