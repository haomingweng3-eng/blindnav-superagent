#!/usr/bin/env python3
"""Audit real Last10m replay scenarios and their entrance relation labels."""
from __future__ import annotations
import argparse, json, os

def audit(path):
    root = json.load(open(path, encoding="utf-8"))
    scenarios = []
    for scenario in root.get("scenarios", []):
        frames = []
        for frame in scenario.get("frames", []):
            result = json.loads(frame["json"])
            relations = result.get("relations", [])
            same = [r["door_id"] for r in relations if r.get("relation") == "SAME_TARGET"]
            neighbor = [r["door_id"] for r in relations if r.get("relation") == "NEIGHBOR"]
            frames.append({
                "label": frame.get("label"), "image": frame.get("image"),
                "target_visible": result.get("target_visible"),
                "target_identity": result.get("target_identity"),
                "door_count": len(result.get("doors", [])),
                "same_target_ids": same, "neighbor_ids": neighbor,
                "relation_labeled": bool(relations),
                "ground_visible": result.get("ground", {}).get("visible"),
                "ground_obstruction": result.get("ground", {}).get("obstruction"),
                "need_view": result.get("need_view"),
            })
        scenarios.append({"id": scenario.get("id"), "title": scenario.get("title"),
                          "target": scenario.get("target"), "provenance": scenario.get("provenance"),
                          "frames": frames})
    return {"schema": root.get("schema"), "scenario_count": len(scenarios),
            "frame_count": sum(len(s["frames"]) for s in scenarios), "scenarios": scenarios,
            "all_frames_have_relation_or_refusal_context": all(
                f["relation_labeled"] or f["door_count"] == 0 for s in scenarios for f in s["frames"]
            )}

def main(argv=None):
    ap = argparse.ArgumentParser(); ap.add_argument("--scenarios", required=True); ap.add_argument("--out", required=True)
    args = ap.parse_args(argv); result = audit(args.scenarios)
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f: json.dump(result, f, ensure_ascii=False, indent=2)
    print(json.dumps(result, ensure_ascii=False, indent=2)); return 0
if __name__ == "__main__": raise SystemExit(main())
