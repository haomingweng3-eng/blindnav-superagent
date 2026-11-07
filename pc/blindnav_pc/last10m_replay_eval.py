"""Deterministic audit for the Last10m structured replay.

This evaluates evidence gates from the replay answers only.  It does not claim
that the embedded VLM answers are live perception or that a replay frame proves
device performance.  In particular, an unlabeled door frame is kept as refusal
context and is never scored as a wrong entrance.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _target_locked(frame: dict[str, Any], target: dict[str, Any]) -> bool:
    if frame.get("target_visible") is not True:
        return False
    identity = frame.get("target_identity")
    accepted = {target.get("name"), *(target.get("aliases") or [])}
    return bool(identity and identity in accepted)


def _relation(frame: dict[str, Any]) -> str:
    relations = frame.get("relations") or []
    same = [r for r in relations if r.get("relation") == "SAME_TARGET"]
    neighbor = [r for r in relations if r.get("relation") == "NEIGHBOR"]
    if len(same) == 1:
        return "CORRECT_SAME_TARGET"
    if len(same) > 1:
        return "MULTIPLE_SAME_TARGET"
    if neighbor:
        return "NEIGHBOR_REJECTED"
    return "UNLABELED_REFUSAL_CONTEXT"


def _ground(frame: dict[str, Any]) -> str:
    ground = frame.get("ground") or {}
    if ground.get("visible") is not True:
        return "NOT_VISIBLE"
    if ground.get("obstruction") is True:
        return "OBSTRUCTED"
    if ground.get("obstruction") is False:
        return "CLEAR_CONFIRMED"
    return "VISIBLE_UNRESOLVED"


def evaluate_scenarios(root: dict[str, Any]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    target_correct = 0
    relation_counts = {
        "evaluated": 0,
        "correct_same_target": 0,
        "neighbor_rejected": 0,
        "wrong_entrance": 0,
        "unlabeled_refusal_context": 0,
        "multiple_same_target": 0,
    }
    ground_counts = {
        "obstructed": 0,
        "clear_confirmed": 0,
        "visible_unresolved": 0,
        "not_visible": 0,
    }
    authorized = 0

    for scenario in root.get("scenarios", []):
        target = scenario.get("target") or {}
        for frame_spec in scenario.get("frames", []):
            raw = json.loads(frame_spec.get("json", "{}"))
            locked = _target_locked(raw, target)
            relation = _relation(raw)
            ground = _ground(raw)
            if locked:
                target_correct += 1
            if relation != "UNLABELED_REFUSAL_CONTEXT":
                relation_counts["evaluated"] += 1
            if relation == "CORRECT_SAME_TARGET":
                relation_counts["correct_same_target"] += 1
            elif relation == "NEIGHBOR_REJECTED":
                relation_counts["neighbor_rejected"] += 1
            elif relation == "MULTIPLE_SAME_TARGET":
                relation_counts["multiple_same_target"] += 1
                relation_counts["wrong_entrance"] += 1
            elif relation == "UNLABELED_REFUSAL_CONTEXT":
                relation_counts["unlabeled_refusal_context"] += 1
            if ground == "OBSTRUCTED":
                ground_counts["obstructed"] += 1
            elif ground == "CLEAR_CONFIRMED":
                ground_counts["clear_confirmed"] += 1
            elif ground == "VISIBLE_UNRESOLVED":
                ground_counts["visible_unresolved"] += 1
            else:
                ground_counts["not_visible"] += 1
            can_authorize = relation == "CORRECT_SAME_TARGET" and ground == "CLEAR_CONFIRMED"
            authorized += int(can_authorize)
            rows.append({
                "scenario_id": scenario.get("id"),
                "frame": frame_spec.get("label"),
                "image": frame_spec.get("image"),
                "target_locked": locked,
                "relation": relation,
                "ground": ground,
                "motion_authorized": can_authorize,
                "provenance": "REPLAY",
            })

    frame_count = len(rows)
    return {
        "schema": "last10m-replay-eval/1",
        "provenance": "REPLAY",
        "scenarios": len(root.get("scenarios", [])),
        "frames": frame_count,
        "target_lock": {"correct": target_correct, "total": frame_count},
        "entrance_relation": relation_counts,
        "ground": ground_counts,
        "motion_authorization": {
            "authorized": authorized,
            "total": frame_count,
            "rule": "SAME_TARGET plus CLEAR_CONFIRMED ground evidence",
        },
        "frames_detail": rows,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenarios", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    result = evaluate_scenarios(json.loads(Path(args.scenarios).read_text(encoding="utf-8")))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
