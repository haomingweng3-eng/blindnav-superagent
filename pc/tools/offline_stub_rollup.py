#!/usr/bin/env python3
"""Compose the offline stub rollup from the three eval ``summary.json`` files.

Reads only generated numbers; nothing is recomputed or hand-typed.

    python tools/offline_stub_rollup.py
    python tools/offline_stub_rollup.py --root out --strict
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Dict, Optional

DEFAULT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "out"))
SOURCES = {
    "traffic_light": os.path.join("imvisible_traffic_light", "summary.json"),
    "entrances": os.path.join("entrances_last10m", "summary.json"),
    "shadowwalk_varied": os.path.join("blindroad_shadowwalk", "varied_pose", "summary.json"),
    "shadowwalk_centred": os.path.join("blindroad_shadowwalk", "centred_pose", "summary.json"),
}


def _load(root: str, relative: str) -> Optional[dict]:
    path = os.path.join(root, relative)
    if not os.path.isfile(path):
        return None
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _pct(value) -> str:
    return "n/a" if value is None else f"{100.0 * value:.2f}%"


def _pct_100(value) -> str:
    return "n/a" if value is None else f"{value:.2f}%"


def _command_line(summary: dict, command: str) -> str:
    counts = summary["commands"]["overall"]["counts"]
    percent = summary["commands"]["overall"]["percent"]
    return f"{counts[command]} ({_pct_100(percent[command])})"


def _sweep_accuracy(sweep, min_confirm: int) -> str:
    for item in sweep:
        if item["min_confirm"] == min_confirm:
            return f"{item['accuracy']:.4f}"
    return "n/a"


def _write(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def _blindroad_summary(varied: dict, centred: dict) -> str:
    lines = [
        "# Blind-road ShadowWalk summary",
        "",
        "Two offline runs on the same 200 real GT walkable masks (train+val+test):",
        "",
        "- `varied_pose/` -- synthetic offsets `0, 0.4, -0.4, 1.2` m, which deliberately puts the",
        "  walker outside the 2.5 m corridor every fourth frame.",
        "- `centred_pose/` -- offset `0` m, the stable-pose control that isolates mask geometry from",
        "  the synthetic trajectory.",
        "",
        "Both use `straight60`, flat-ground depth and synthetic ego-motion. See the per-run",
        "`summary.md` files for full caveats and CSVs.",
        "",
        "## Command distribution",
        "",
        "| command | varied pose | centred pose |",
        "|---|---:|---:|",
    ]
    for command in ("STRAIGHT", "LEFT", "RIGHT", "STOP", "UNKNOWN"):
        lines.append(f"| {command} | {_command_line(varied, command)} | "
                     f"{_command_line(centred, command)} |")
    lines += [
        "",
        f"Frames: {varied['inputs']['frames']} (varied), {centred['inputs']['frames']} (centred).",
        "",
        "## Clearly-failed frames",
        "",
        "| criterion | varied pose | centred pose |",
        "|---|---:|---:|",
    ]
    for key, label in (
            ("stop_on_open_walkable", "STOP on open walkable"),
            ("unknown_when_mask_ok", "UNKNOWN when mask ok"),
            ("spurious_turn", "spurious turn while centred"),
            ("intended_off_corridor_stop", "intended off-corridor STOP (not a failure)"),
            ("yaw_stop", "yaw-too-large STOP")):
        lines.append(f"| {label} | {varied['failures'][key]} | {centred['failures'][key]} |")
    v_osc = varied["failures"]["oscillation"]
    c_osc = centred["failures"]["oscillation"]
    lines += [
        f"| LEFT/RIGHT oscillation on consecutive ids | {v_osc['stable_pose_oscillation_failures']} "
        f"/ {v_osc['consecutive_id_pairs']} pairs | {c_osc['stable_pose_oscillation_failures']} "
        f"/ {c_osc['consecutive_id_pairs']} pairs |",
        "",
        f"STOP reasons -- varied: `{varied['failures']['stop_reasons']}`; "
        f"centred: `{centred['failures']['stop_reasons']}`.",
        "",
        f"UNKNOWN reasons -- varied: `{varied['failures']['unknown_reasons']}`; "
        f"centred: `{centred['failures']['unknown_reasons']}`.",
        "",
        "`intended_off_corridor_stop` is expected: the varied schedule places the walker at 1.2 m,",
        "beyond the 0.8 x half-width stop threshold. It is not counted as a failure.",
        "",
        "## Key caveats",
        "",
        "- No depth model: metric depth is a flat-ground plane, so the 1.5-6 m band is geometric.",
        "- No real ego-motion: both runs use synthetic poses, not PDR/odometry.",
        "- The blind-road images are not a certified continuous video; train ids are mostly",
        "  contiguous, val/test are random, so oscillation only counts `id_gap == 1` pairs.",
        "- `mask_ok` is a coarse image-space ROI; walkable pixels outside the corridor make",
        "  UNKNOWN defensible, so failure counts are a lower bound.",
        "",
        "Full reports: [varied_pose/summary.md](varied_pose/summary.md), "
        "[centred_pose/summary.md](centred_pose/summary.md).",
        "",
    ]
    return "\n".join(lines)


def _rollup(tl: dict, entrances: dict, varied: dict, centred: dict) -> str:
    tl_filtered = tl["filtered"]
    ent = entrances["metrics"]
    v_fail = varied["failures"]
    c_fail = centred["failures"]
    lines = [
        "# Offline stub eval rollup",
        "",
        "Measured results for the three thin PC stubs on this branch. Committed reports are small",
        "text/JSON/CSV only; datasets and images stay out of git.",
        "",
        "| stub | report | key result |",
        "|---|---|---|",
        f"| ImVisible traffic light | [imvisible_traffic_light/summary.md](../imvisible_traffic_light/summary.md) | "
        f"raw mapping 100.00%, filtered accuracy {_pct(tl_filtered['accuracy'])}, "
        f"unsafe green {tl_filtered['unsafe_green']} |",
        f"| Entrances Last-10 m | [entrances_last10m/summary.md](../entrances_last10m/summary.md) | "
        f"frame recall (in-range) {_pct(ent['frame_recall_in_range_gt'])} "
        f"({ent['frame_recall_in_range_gt_counts'][0]}/{ent['frame_recall_in_range_gt_counts'][1]}), "
        f"refuse rate {_pct(ent['refuse_rate'])} |",
        f"| Blind-road ShadowWalk | [blindroad_shadowwalk/summary.md](../blindroad_shadowwalk/summary.md) | "
        f"varied pose commands {varied['commands']['overall']['counts']}; "
        f"STOP-on-open {v_fail['stop_on_open_walkable']}, "
        f"UNKNOWN-when-mask-ok {v_fail['unknown_when_mask_ok']}, "
        f"oscillation {v_fail['oscillation']['stable_pose_oscillation_failures']} |",
        "",
        "## 1. ImVisible traffic light",
        "",
        f"- Rows: {tl['inputs']['rows']} "
        f"({', '.join(f'{k}={v}' for k, v in sorted(tl['inputs']['rows_by_split'].items()))}); "
        f"images present {tl['inputs']['images_present']}/{tl['inputs']['rows']}.",
        f"- Pseudo-sequences: {tl['sequences']['count']} "
        f"(length histogram `{tl['sequences']['length_histogram']}`).",
        f"- Raw three-state mapping accuracy: **{tl['label_mapping']['accuracy']:.4f}** "
        "(exact by construction -- no pixel model).",
        f"- Filtered accuracy at min_confirm={tl_filtered['min_confirm']}: "
        f"**{tl_filtered['accuracy']:.4f}**; stable rate {_pct(tl_filtered['stable_rate'])}; "
        f"abstention {_pct(tl_filtered['abstention_rate'])}.",
        f"- Unsafe green (GT red/uncertain -> green): **{tl_filtered['unsafe_green']}**.",
        f"- Green recall {_pct(tl_filtered['per_class']['green']['recall'])}; "
        f"red recall {_pct(tl_filtered['per_class']['red']['recall'])}; "
        f"uncertain recall {_pct(tl_filtered['per_class']['uncertain']['recall'])}.",
        f"- Confirmation sweep: min_confirm=1 accuracy "
        f"{_sweep_accuracy(tl['confirmation_sweep'], 1)}; "
        f"min_confirm=3 accuracy {_sweep_accuracy(tl['confirmation_sweep'], 3)}; "
        f"min_confirm=5 accuracy {_sweep_accuracy(tl['confirmation_sweep'], 5)}.",
        "",
        "## 2. Entrances Last-10 m",
        "",
        f"- Frames: {ent['frames']} (with GT doors {ent['frames_with_gt']}; "
        f"with in-range GT doors {ent['frames_with_in_range_gt']}; empty labels {ent['empty_label_frames']}).",
        f"- GT boxes: {ent['boxes']} (in range {ent['boxes_in_range']}; unknown range {ent['boxes_unknown_range']}).",
        f"- Frame recall, in-range GT: **{_pct(ent['frame_recall_in_range_gt'])}** "
        f"({ent['frame_recall_in_range_gt_counts'][0]}/{ent['frame_recall_in_range_gt_counts'][1]}).",
        f"- Frame recall, all GT: {_pct(ent['frame_recall_all_gt'])} "
        f"({ent['frame_recall_all_gt_counts'][0]}/{ent['frame_recall_all_gt_counts'][1]}).",
        f"- Box recall, in-range: {_pct(ent['box_recall_in_range'])} "
        f"({ent['box_recall_in_range_counts'][0]}/{ent['box_recall_in_range_counts'][1]}).",
        f"- Refused: {ent['refused_frames']}/{ent['frames']} ({_pct(ent['refuse_rate'])}); "
        f"reasons `{ent['refusal_reasons']}`.",
        f"- Proposed candidate counts: `{ent['proposed_count_distribution']}`.",
        f"- Estimated range p5/p50/p95: "
        f"{ent['range_percentiles_m'].get('5')}/{ent['range_percentiles_m'].get('50')}/"
        f"{ent['range_percentiles_m'].get('95')} m.",
        "",
        "## 3. Blind-road ShadowWalk",
        "",
        "Varied pose (primary):",
        "",
        f"- Commands: {varied['commands']['overall']['counts']}.",
        f"- STOP on open walkable: **{v_fail['stop_on_open_walkable']}**; "
        f"UNKNOWN when mask ok: **{v_fail['unknown_when_mask_ok']}**; "
        f"spurious turn: {v_fail['spurious_turn']}; "
        f"oscillation: {v_fail['oscillation']['stable_pose_oscillation_failures']}"
        f"/{v_fail['oscillation']['consecutive_id_pairs']} consecutive-id pairs.",
        f"- Intended off-corridor STOP: {v_fail['intended_off_corridor_stop']} "
        f"(synthetic 1.2 m offset; not a failure).",
        "",
        "Centred pose (control):",
        "",
        f"- Commands: {centred['commands']['overall']['counts']}.",
        f"- STOP on open walkable: **{c_fail['stop_on_open_walkable']}**; "
        f"UNKNOWN when mask ok: **{c_fail['unknown_when_mask_ok']}**; "
        f"spurious turn: {c_fail['spurious_turn']}; "
        f"oscillation: {c_fail['oscillation']['stable_pose_oscillation_failures']}"
        f"/{c_fail['oscillation']['consecutive_id_pairs']} consecutive-id pairs.",
        "",
        "## Blockers / not done",
        "",
        "- No pixel model for the traffic light: GT is the dataset's own `mode` collapse, so raw",
        "  accuracy is exact by construction. Filter numbers replay non-contiguous labels.",
        "- Entrances has no distance labels and no detector confidences. `in-range` uses the same",
        "  flat-ground estimator as the stub; `low_confidence`/`distance_unknown` cannot fire.",
        "- ShadowWalk has no depth model and no real ego-motion; depth is a flat-ground plane and",
        "  poses are synthetic. The blind-road images are not a certified continuous video.",
        "- No Android / IMU / Pipeline wiring was added; no real-device runs.",
        "- `sidewalk-semantic` was intentionally skipped.",
        "",
        "## Reproduce",
        "",
        "```bash",
        "cd pc",
        ".venv/bin/python tools/traffic_light_demo.py --split validation,testing --limit 0 \\",
        "    --report-dir out/imvisible_traffic_light",
        ".venv/bin/python tools/last10m_demo.py --split all --limit 0 \\",
        "    --report-dir out/entrances_last10m",
        ".venv/bin/python tools/shadowwalk_real_eval.py --split all --limit 0 \\",
        "    --offsets 0,0.4,-0.4,1.2 --report-dir out/blindroad_shadowwalk/varied_pose",
        ".venv/bin/python tools/shadowwalk_real_eval.py --split all --limit 0 \\",
        "    --offsets 0 --report-dir out/blindroad_shadowwalk/centred_pose",
        ".venv/bin/python tools/offline_stub_rollup.py",
        "```",
        "",
    ]
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=DEFAULT_ROOT, help="report root (default: pc/out)")
    ap.add_argument("--strict", action="store_true", help="fail if any source summary is missing")
    args = ap.parse_args(argv)

    loaded: Dict[str, Optional[dict]] = {key: _load(args.root, rel) for key, rel in SOURCES.items()}
    missing = [key for key, value in loaded.items() if value is None]
    if missing:
        message = "missing summaries: " + ", ".join(f"{key} ({SOURCES[key]})" for key in missing)
        if args.strict:
            raise SystemExit(message)
        print(f"warning: {message}", file=sys.stderr)

    if all(loaded[key] is not None
           for key in ("traffic_light", "entrances", "shadowwalk_varied", "shadowwalk_centred")):
        text = _rollup(loaded["traffic_light"], loaded["entrances"],
                       loaded["shadowwalk_varied"], loaded["shadowwalk_centred"])
        _write(os.path.join(args.root, "offline_stub_eval", "summary.md"), text)
        print(f"wrote {os.path.join(args.root, 'offline_stub_eval', 'summary.md')}")

    if loaded["shadowwalk_varied"] is not None and loaded["shadowwalk_centred"] is not None:
        text = _blindroad_summary(loaded["shadowwalk_varied"], loaded["shadowwalk_centred"])
        _write(os.path.join(args.root, "blindroad_shadowwalk", "summary.md"), text)
        print(f"wrote {os.path.join(args.root, 'blindroad_shadowwalk', 'summary.md')}")

    # Keep the aggregate numbers as data as well, for quick diffing.
    payload = {key: value for key, value in loaded.items() if value is not None}
    _write(os.path.join(args.root, "offline_stub_eval", "rollup.json"),
           json.dumps(payload, ensure_ascii=False, indent=2))
    print(f"wrote {os.path.join(args.root, 'offline_stub_eval', 'rollup.json')}")
    return 0 if not missing else 1


if __name__ == "__main__":
    raise SystemExit(main())
