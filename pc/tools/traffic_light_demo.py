#!/usr/bin/env python3
"""Traffic-light stub smoke demo against ImVisible labels (no model, no images needed).

Prints the three-state decision for each labelled frame and then demonstrates the
consecutive-frame confirmation filter.  There is no "go" output: ``green`` still requires the
person to confirm before crossing.

    python tools/traffic_light_demo.py --limit 12
    python tools/traffic_light_demo.py --split validation --limit 0
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from blindnav_pc.traffic_light import (TrafficLightFilter, imvisible_mode_to_state,
                                       imvisible_row_to_observation, read_imvisible_rows)

DEFAULT_LABELS = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "data", "eval", "imvisible", "testing_file.csv"))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--labels", default=DEFAULT_LABELS)
    ap.add_argument("--split", choices=["training", "validation", "testing"], default=None,
                    help="convenience override: uses <labels-dir>/<split>_file.csv")
    ap.add_argument("--limit", type=int, default=12, help="0 = all rows")
    ap.add_argument("--min-confirm", type=int, default=3, dest="min_confirm")
    ap.add_argument("--min-confidence", type=float, default=0.6, dest="min_confidence")
    args = ap.parse_args(argv)

    path = args.labels
    if args.split:
        path = os.path.join(os.path.dirname(path), f"{args.split}_file.csv")
    if not os.path.isfile(path):
        print(f"labels not found: {path}", file=sys.stderr)
        return 2
    rows = read_imvisible_rows(path)
    if args.limit > 0:
        rows = rows[: args.limit]

    print(f"labels={path} rows={len(rows)} min_confirm={args.min_confirm}")
    print(f"{'file':<28} {'mode':>4} {'block':<11} {'raw_state':<10} source")
    counts: Counter = Counter()
    for row in rows:
        state = imvisible_mode_to_state(row.get("mode"))
        counts[state] += 1
        print(f"{row.get('file', ''):<28} {row.get('mode', ''):>4} {row.get('block', ''):<11} "
              f"{state:<10} imvisible")
    print(f"raw states: {dict(counts)}")

    filt = TrafficLightFilter(min_confirm_frames=args.min_confirm,
                              min_confidence=args.min_confidence)
    print("\nconfirmation demo (same evidence repeated):")
    for label, obs in (("green x3", [imvisible_row_to_observation({"file": "demo", "mode": "1"})] * 3),
                       ("countdown-blank x3", [imvisible_row_to_observation({"file": "demo", "mode": "3"})] * 3)):
        decision = None
        for item in obs:
            decision = filt.update(item)
        print(f"  {label:<20} -> {decision.state:<10} stable={decision.stable} "
              f"auto_go={decision.auto_go} requires_confirmation={decision.requires_user_confirmation} "
              f"reason={decision.reason}")
        filt.reset()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
