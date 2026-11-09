"""Diagnostic-only occupancy detour-hint evaluation over frozen replay rows.

The variant never changes safety state, effective command, or authorization. It
only marks a partially observed occupancy ray as an observation hint when there
is positive observed clearance and no occupied body cell. UNKNOWN remains
UNKNOWN and the hint is not a motion instruction.
"""
from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path
from typing import Iterable, Mapping

GUIDANCE = {"STRAIGHT", "LEFT", "RIGHT"}


def _true(value: object) -> bool:
    return str(value).lower() in {"1", "true", "yes"}


def _hint(row: Mapping[str, str]) -> bool:
    try:
        distance = float(row.get("occupancy_clear_distance_m") or 0.0)
        blocking = int(float(row.get("occupancy_blocking_cells") or 0.0))
    except (TypeError, ValueError):
        return False
    return (
        row.get("occupancy_state") == "UNKNOWN"
        and distance > 0.0
        and blocking == 0
    )


def evaluate_rows(rows: Iterable[Mapping[str, str]]) -> dict:
    rows = list(rows)
    hints = [r for r in rows if _hint(r)]
    authorized = [r for r in rows if r.get("effective_command") in GUIDANCE
                  or r.get("authorized_motion") in GUIDANCE]
    danger = [r for r in rows if _true(r.get("danger_any"))]
    danger_guided = [r for r in danger if r in authorized]
    confirmed_clear = [r for r in rows if _true(r.get("confirmed_clear"))]
    false_stops = [r for r in confirmed_clear if r.get("effective_command") == "STOP"]
    return {
        "frames": len(rows),
        "hint_frames": len(hints),
        "hint_on_danger": sum(_true(r.get("danger_any")) for r in hints),
        "hint_on_confirmed_clear": sum(_true(r.get("confirmed_clear")) for r in hints),
        "hint_reasons": dict(Counter(r.get("reason", "") for r in hints)),
        "authorized_motion_frames": len(authorized),
        "authorized_motion_unchanged": True,
        "danger_forward_guidance_misses": {
            "numerator": len(danger_guided),
            "denominator": len(danger),
            "rate": (len(danger_guided) / len(danger)) if danger else None,
        },
        "confirmed_clear_false_stops": {
            "numerator": len(false_stops),
            "denominator": len(confirmed_clear),
            "rate": (len(false_stops) / len(confirmed_clear)) if confirmed_clear else None,
        },
        "hint_contract": (
            "observation_hint only; UNKNOWN remains UNKNOWN; no CLEAR, STOP, "
            "authorized_motion, or directional TTS is produced"
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--per-frame", required=True)
    parser.add_argument("--condition", default="production")
    parser.add_argument("--arm", default="mobilio")
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    with open(args.per_frame, newline="", encoding="utf-8") as f:
        rows = [r for r in csv.DictReader(f)
                if r.get("condition") == args.condition and r.get("arm") == args.arm]
    result = {
        "schema": "mobilio-occupancy-hint-eval/1",
        "provenance": "DESKTOP_REAL_DATA_REPLAY",
        "condition": args.condition,
        "arm": args.arm,
        "result": evaluate_rows(rows),
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
