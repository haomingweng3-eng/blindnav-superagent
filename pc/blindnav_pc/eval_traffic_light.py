"""Offline three-state traffic-light eval.

``python -m blindnav_pc.eval_traffic_light --ann labels.csv --images <dir-or-tar> --out out/``

Images are optional.  A missing directory, an unextracted tar, or filenames that do not match
the CSV still produce ``metrics.json`` and a Chinese ``summary.md`` from the annotations alone.
UNCERTAIN is never relabelled as GREEN.  Illegal outputs (CROSS, go-ahead, 可以过街) are counted
as uncertain, not as a green hit.  A green prediction below ``--min-green-confidence`` is also
downgraded to uncertain before scoring.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Optional, Sequence

from .traffic_light import (GREEN, RED, STATES, UNCERTAIN, TrafficLightThreeState,
                            label_to_state, parse_imvisible_csv)

BASELINE_NAME = "opencv_hsv_color_roi"
_PLACEHOLDER = None


def apply_green_gate(state: str, confidence, min_green_confidence: float) -> str:
    """Fail closed.  Low-confidence green and any non-state become uncertain, never green."""
    if state not in STATES:
        return UNCERTAIN
    try:
        conf = float(confidence)
    except (TypeError, ValueError):
        conf = 0.0
    if state == GREEN and conf < float(min_green_confidence):
        return UNCERTAIN
    return state


def score_states(gt_states: Sequence[str], pred_states: Sequence[str]) -> dict:
    """Confusion and per-class precision/recall.

    A prediction of UNCERTAIN is only a hit for UNCERTAIN.  It is not added to the green
    predicted column, and ground-truth UNCERTAIN is not rewritten to GREEN.
    """
    if len(gt_states) != len(pred_states):
        raise ValueError("ground truth and predictions must have the same length")
    matrix = {gt: {pred: 0 for pred in STATES} for gt in STATES}
    illegal = 0
    for gt, pred in zip(gt_states, pred_states):
        if gt not in STATES:
            raise ValueError(f"illegal ground truth {gt!r}")
        if pred not in STATES:
            pred = UNCERTAIN
            illegal += 1
        matrix[gt][pred] += 1
    per_class = {}
    for state in STATES:
        tp = matrix[state][state]
        support = sum(matrix[state].values())
        predicted = sum(matrix[gt][state] for gt in STATES)
        per_class[state] = {
            "support": support,
            "predicted": predicted,
            "true_positive": tp,
            "precision": (tp / predicted) if predicted else None,
            "recall": (tp / support) if support else None,
        }
    scored = len(gt_states)
    uncertain_preds = sum(matrix[gt][UNCERTAIN] for gt in STATES)
    return {
        "confusion": matrix,
        "per_class": per_class,
        "unsafe_green": sum(matrix[gt][GREEN] for gt in STATES if gt != GREEN),
        "illegal_outputs": illegal,
        # No branch above moves an UNCERTAIN prediction into the green column.
        "uncertain_treated_as_green": 0,
        "pred_uncertain_rate": (uncertain_preds / scored) if scored else None,
    }


def _placeholder_metrics() -> dict:
    return {
        "confusion": {gt: {pred: _PLACEHOLDER for pred in STATES} for gt in STATES},
        "per_class": {
            state: {"support": 0, "predicted": None, "true_positive": None,
                    "precision": None, "recall": None}
            for state in STATES
        },
        "unsafe_green": 0,
        "illegal_outputs": 0,
        "uncertain_treated_as_green": 0,
        "pred_uncertain_rate": None,
    }


def resolve_image(images_root: str, record) -> Optional[str]:
    """Return an extracted frame path, or None when the image is not on disk.

    A tar path, a missing directory, or a basename that is not in the directory is a miss.
    This function does not extract archives.
    """
    candidates = []
    if record.path and os.path.isabs(record.path) and os.path.isfile(record.path):
        candidates.append(record.path)
    if images_root and os.path.isdir(images_root):
        for name in (record.path, record.image_id):
            if not name:
                continue
            candidates.append(os.path.join(images_root, name))
            candidates.append(os.path.join(images_root, os.path.basename(name)))
    seen = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        if os.path.isfile(candidate):
            return candidate
    return None


def _read_bgr(path: str):
    import cv2
    return cv2.imread(path, cv2.IMREAD_COLOR)


def _class_counts(records) -> dict:
    counts = {state: 0 for state in STATES}
    for record in records:
        counts[record.gt_state] += 1
    return counts


def _pct(value) -> str:
    if value is None:
        return "未计算"
    return f"{100.0 * float(value):.1f}%"


def _summary_md(metrics: dict) -> str:
    counts = metrics["class_counts"]
    red = metrics["per_class"][RED]
    green = metrics["per_class"][GREEN]
    if metrics["mode"] == "annotation_only":
        lead = ("本次没有可用图像（目录不存在、仍是 tar，或文件名对不上）。"
                "只统计标注，混淆矩阵是占位，缺图样本没有被当成绿灯。")
    else:
        lead = ("预测来自 HSV/OpenCV 颜色基线（离线脚手架，不是最终模型）。"
                "输出只有红灯、绿灯、不确定，绿灯不是过街许可。")
    lines = [
        "# 红绿灯三态评测",
        "",
        lead,
        "",
        f"- 模式：{metrics['mode']}",
        f"- 标注条数：{metrics['rows']}",
        f"- 类别计数：红灯 {counts[RED]}，绿灯 {counts[GREEN]}，不确定 {counts[UNCERTAIN]}",
        f"- 标注不确定占比：{_pct(metrics['uncertain_rate'])}",
        f"- 找到图像：{metrics['images_found']}，缺失：{metrics['images_missing']}",
        f"- 红灯精确率：{_pct(red['precision'])}，召回率：{_pct(red['recall'])}",
        f"- 绿灯精确率：{_pct(green['precision'])}，召回率：{_pct(green['recall'])}",
        f"- 预测为不确定的占比：{_pct(metrics['pred_uncertain_rate'])}",
        f"- 把不确定记成绿灯的次数：{metrics['uncertain_treated_as_green']}（规则上恒为 0）",
        f"- 非绿灯标注却预测为绿灯：{metrics['unsafe_green']}",
        f"- 非法输出（CROSS / 可以过街等）改记为不确定：{metrics['illegal_outputs']}",
        "",
        "绿灯只表示灯色像绿色。红灯和不确定都按等待处理，不能覆盖 STOP / OBSERVE / 拒答。",
        "",
    ]
    return "\n".join(lines)


def run_eval(ann: str, images: str, out: str, min_green_confidence: float = 0.6,
             model: Optional[TrafficLightThreeState] = None) -> dict:
    """Score ``ann`` and write ``metrics.json`` plus Chinese ``summary.md`` under ``out``."""
    if not os.path.isfile(ann):
        raise FileNotFoundError(ann)
    records = parse_imvisible_csv(ann)
    counts = _class_counts(records)
    rows = len(records)
    found_gt = []
    found_pred = []
    images_found = 0
    illegal_raw = 0
    predictor = model if model is not None else TrafficLightThreeState()
    for record in records:
        path = resolve_image(images, record)
        if path is None:
            continue
        frame = _read_bgr(path)
        if frame is None:
            continue
        state, confidence = predictor.predict(frame)
        if state not in STATES:
            illegal_raw += 1
        gated = apply_green_gate(state, confidence, min_green_confidence)
        found_gt.append(record.gt_state)
        found_pred.append(gated)
        images_found += 1
    images_missing = rows - images_found
    if images_found:
        scored = score_states(found_gt, found_pred)
        mode = "baseline"
        scored["illegal_outputs"] += illegal_raw
        # Support in the scored block is over images that were actually read.
        for state in STATES:
            scored["per_class"][state]["annotation_support"] = counts[state]
    else:
        scored = _placeholder_metrics()
        mode = "annotation_only"
        for state in STATES:
            scored["per_class"][state]["support"] = counts[state]
            scored["per_class"][state]["annotation_support"] = counts[state]
    prediction_states = sorted(set(found_pred))
    if any(state not in STATES for state in prediction_states):
        raise RuntimeError("scored predictions left the three-state set")
    if label_to_state("CROSS") != UNCERTAIN or label_to_state("可以过街") != UNCERTAIN:
        raise RuntimeError("go-ahead labels must collapse to uncertain")
    metrics = {
        "mode": mode,
        "baseline": BASELINE_NAME if mode == "baseline" else None,
        "rows": rows,
        "scored_rows": images_found,
        "class_counts": counts,
        "uncertain_rate": (counts[UNCERTAIN] / rows) if rows else None,
        "images_root": os.path.abspath(images) if images else "",
        "images_found": images_found,
        "images_missing": images_missing,
        "missing_images_counted_as_green": 0,
        "min_green_confidence": float(min_green_confidence),
        "auto_go": False,
        "states": list(STATES),
        "prediction_states": prediction_states,
        **scored,
    }
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, "metrics.json"), "w", encoding="utf-8") as fh:
        json.dump(metrics, fh, ensure_ascii=False, indent=2)
    with open(os.path.join(out, "summary.md"), "w", encoding="utf-8") as fh:
        fh.write(_summary_md(metrics))
    return metrics


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="红绿灯三态离线评测。图像缺失时只写标注统计，不把不确定当成绿灯。")
    parser.add_argument("--ann", required=True, help="ImVisible CSV（file,mode 或 image,label）")
    parser.add_argument("--images", default="",
                        help="已解压的图像目录，或尚未解压的 tar 路径提示")
    parser.add_argument("--out", required=True, help="写入 metrics.json 与 summary.md 的目录")
    parser.add_argument("--min-green-confidence", type=float, default=0.6,
                        dest="min_green_confidence",
                        help="低于该置信度的绿灯在计分前改为不确定")
    args = parser.parse_args(argv)
    if not os.path.isfile(args.ann):
        print(f"标注不存在: {args.ann}", file=sys.stderr)
        return 2
    metrics = run_eval(args.ann, args.images, args.out,
                       min_green_confidence=args.min_green_confidence)
    print(f"mode={metrics['mode']} rows={metrics['rows']} images_found={metrics['images_found']} "
          f"uncertain_treated_as_green={metrics['uncertain_treated_as_green']} out={args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
