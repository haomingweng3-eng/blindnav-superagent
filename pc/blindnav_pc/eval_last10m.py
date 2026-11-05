"""Offline Last10m door-proposal metrics.

``python -m blindnav_pc.eval_last10m --labels <dir_or_file> --out <dir>``

``--labels`` is a YOLO label file, a directory of label files, a JSON case file,
or the bundled fixture name ``synthetic``.  A missing Entrances path falls back
to that fixture and still writes ``metrics.json`` and a Chinese ``summary.md``.

Each case is scored with :func:`blindnav_pc.last10m.propose_doors`.  After the
filter the proposal length is required to be <= 3.  A case with any eligible
door of unknown distance must refuse.  Top-k hit is reported only for cases
that have ground truth and are supposed to propose.

This harness does not steer.  It does not claim the W1 curb or near-field gates.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from dataclasses import dataclass
from typing import Optional, Sequence

import numpy as np

from .last10m import (MAX_CANDIDATES, DEFAULT_RANGE_M, DoorCandidate, propose_doors)

FIXTURE_NAME = "synthetic"
BINS = ("missing", "0_5m", "5_10m", "gt_10m", "no_eligible")
_MISSING_DISTANCE = {"none", "nan", "null", "-", "?", ""}


@dataclass(frozen=True)
class EvalCase:
    name: str
    doors: tuple
    gt: tuple
    gt_available: bool


def _door(class_id, cx, cy, w, h, confidence=1.0, distance_m=None) -> DoorCandidate:
    return DoorCandidate(int(class_id), float(cx), float(cy), float(w), float(h),
                         float(confidence), None if distance_m is None else float(distance_m))


def synthetic_cases() -> list:
    """Small door set used when Entrances labels are not on disk."""
    def gt_case(name, rows, available=True):
        doors, flags = [], []
        for row in rows:
            doors.append(_door(*row[:5], confidence=row[5], distance_m=row[6]))
            flags.append(bool(row[7]))
        return EvalCase(name, tuple(doors), tuple(flags), available)

    # class, cx, cy, w, h, confidence, distance_m, gt
    return [
        gt_case("five_near", [
            (0, 0.50, 0.80, 0.10, 0.20, 0.95, 1.0, True),
            (0, 0.40, 0.75, 0.10, 0.18, 0.90, 2.0, True),
            (0, 0.60, 0.70, 0.10, 0.16, 0.85, 3.0, True),
            (0, 0.30, 0.65, 0.08, 0.14, 0.80, 4.0, True),
            (0, 0.70, 0.60, 0.08, 0.12, 0.70, 8.0, True),
        ]),
        gt_case("missing_only", [
            (0, 0.50, 0.80, 0.12, 0.20, 0.90, None, True),
        ]),
        gt_case("too_far", [
            (0, 0.50, 0.55, 0.08, 0.10, 0.90, 12.0, True),
            (0, 0.40, 0.50, 0.08, 0.10, 0.80, 15.0, True),
        ]),
        gt_case("low_confidence", [
            (0, 0.50, 0.80, 0.10, 0.20, 0.20, 2.0, True),
            (0, 0.40, 0.70, 0.10, 0.20, 0.30, 3.0, True),
        ]),
        EvalCase("empty", (), (), False),
        gt_case("ambiguous_cutoff", [
            (0, 0.50, 0.80, 0.10, 0.20, 0.90, 1.0, True),
            (0, 0.42, 0.78, 0.10, 0.20, 0.90, 1.1, True),
            (0, 0.58, 0.76, 0.10, 0.20, 0.90, 1.2, True),
            (0, 0.35, 0.74, 0.10, 0.20, 0.90, 1.25, True),
        ]),
        gt_case("mid_single", [
            (0, 0.50, 0.70, 0.12, 0.18, 0.88, 6.5, True),
        ]),
        gt_case("mixed_missing", [
            (0, 0.50, 0.80, 0.10, 0.20, 0.92, 4.0, True),
            (0, 0.30, 0.60, 0.10, 0.16, 0.80, None, True),
        ]),
        gt_case("six_clamp", [
            (0, 0.20, 0.80, 0.08, 0.16, 0.90, float(d), True) for d in (1.0, 2.0, 3.0, 4.0, 5.0, 6.0)
        ]),
        gt_case("gt_among_distractors", [
            (0, 0.40, 0.80, 0.10, 0.20, 0.95, 1.0, False),
            (0, 0.50, 0.75, 0.12, 0.22, 0.93, 2.0, True),
            (0, 0.60, 0.70, 0.09, 0.15, 0.80, 3.0, False),
        ]),
        gt_case("gt_beyond_cap", [
            (0, 0.20, 0.80, 0.08, 0.10, 0.90, 1.0, False),
            (0, 0.30, 0.78, 0.08, 0.10, 0.90, 2.0, False),
            (0, 0.40, 0.76, 0.08, 0.10, 0.90, 3.0, False),
            (0, 0.70, 0.60, 0.08, 0.10, 0.90, 9.0, True),
        ]),
    ]


def _parse_distance(token: str) -> Optional[float]:
    if token.lower() in _MISSING_DISTANCE:
        return None
    value = float(token)
    if value < 0.0 or not math.isfinite(value):
        raise ValueError(f"distance must be non-negative, got {token!r}")
    return value


def _parse_gt(token: str) -> bool:
    return token.lower() in {"1", "true", "yes", "y", "gt"}


def parse_yolo_cases(path: str) -> list:
    """One YOLO file is one case.

    Columns: ``class cx cy w h [confidence [distance [gt]]]``.
    Five columns leave distance unknown, so the stub must refuse.
    Label boxes are ground truth unless an 8th column says otherwise.
    """
    doors = []
    flags = []
    with open(path, "r", encoding="utf-8-sig") as fh:
        for line in fh:
            parts = line.split()
            if not parts or parts[0].startswith("#") or len(parts) < 5:
                continue
            confidence = float(parts[5]) if len(parts) >= 6 else 1.0
            distance = _parse_distance(parts[6]) if len(parts) >= 7 else None
            flags.append(_parse_gt(parts[7]) if len(parts) >= 8 else True)
            doors.append(_door(parts[0], parts[1], parts[2], parts[3], parts[4],
                               confidence, distance))
    name = os.path.splitext(os.path.basename(path))[0]
    return [EvalCase(name, tuple(doors), tuple(flags), any(flags))]


def _case_from_json(raw: dict, default_name: str) -> EvalCase:
    if "candidates" not in raw and "doors" not in raw:
        raise ValueError(f"case {default_name!r} needs candidates")
    rows = raw.get("candidates", raw.get("doors"))
    if not isinstance(rows, list):
        raise ValueError(f"case {default_name!r} candidates must be a list")
    doors, flags = [], []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ValueError(f"candidate {index} in {default_name!r} must be an object")
        distance = row.get("distance_m", row.get("distance"))
        if distance is not None:
            distance = float(distance)
        doors.append(_door(row.get("class_id", row.get("class", 0)),
                           row["cx"], row["cy"], row["w"], row["h"],
                           row.get("confidence", 1.0), distance))
        flags.append(bool(row.get("gt", True)))
    if "gt_available" in raw:
        available = bool(raw["gt_available"])
    else:
        available = any(flags)
    return EvalCase(str(raw.get("name", default_name)), tuple(doors), tuple(flags), available)


def parse_json_cases(path: str) -> list:
    with open(path, "r", encoding="utf-8-sig") as fh:
        data = json.load(fh)
    if isinstance(data, list):
        cases = data
    elif isinstance(data, dict) and "cases" in data:
        cases = data["cases"]
    elif isinstance(data, dict):
        cases = [data]
    else:
        raise ValueError(f"{path} must be a case object or a list of cases")
    if not isinstance(cases, list):
        raise ValueError(f"{path} cases must be a list")
    stem = os.path.splitext(os.path.basename(path))[0]
    out = []
    for index, raw in enumerate(cases):
        if not isinstance(raw, dict):
            raise ValueError(f"case {index} in {path} must be an object")
        out.append(_case_from_json(raw, f"{stem}_{index}"))
    return out


def _label_files(path: str) -> list:
    files = []
    for dirpath, dirnames, filenames in os.walk(path):
        dirnames[:] = sorted(name for name in dirnames if not name.startswith("."))
        for name in sorted(filenames):
            if name.startswith("."):
                continue
            lower = name.lower()
            if lower.endswith(".txt") or lower.endswith(".json"):
                files.append(os.path.join(dirpath, name))
    return files


def resolve_labels(labels: str) -> dict:
    """Load cases.  A missing path uses the bundled synthetic fixture."""
    if labels == FIXTURE_NAME:
        return {"cases": synthetic_cases(), "mode": "synthetic_fixture",
                "requested": labels, "fallback": False}
    if os.path.isdir(labels):
        cases = []
        for path in _label_files(labels):
            if path.lower().endswith(".json"):
                cases.extend(parse_json_cases(path))
            else:
                cases.extend(parse_yolo_cases(path))
        return {"cases": cases, "mode": "labels_dir", "requested": os.path.abspath(labels),
                "fallback": False}
    if os.path.isfile(labels):
        if labels.lower().endswith(".json"):
            cases = parse_json_cases(labels)
        else:
            cases = parse_yolo_cases(labels)
        return {"cases": cases, "mode": "labels_file", "requested": os.path.abspath(labels),
                "fallback": False}
    return {"cases": synthetic_cases(), "mode": "synthetic_fallback",
            "requested": labels, "fallback": True}


def _eligible(doors: Sequence[DoorCandidate], min_confidence: float) -> list:
    return [door for door in doors if door.confidence >= min_confidence]


def classify_case(case: EvalCase, proposal, min_confidence: float = 0.5,
                  max_distance_m: float = DEFAULT_RANGE_M) -> dict:
    """Distance bin, refuse-correctness, candidate count, and optional top-k hit."""
    doors = list(case.doors)
    eligible = _eligible(doors, min_confidence)
    if any(door.distance_m is None for door in eligible):
        bin_name = "missing"
        correct = bool(proposal.refused and proposal.reason == "distance_unknown")
    elif not doors:
        bin_name = "no_eligible"
        correct = bool(proposal.refused and proposal.reason == "no_candidate")
    elif not eligible:
        bin_name = "no_eligible"
        correct = bool(proposal.refused and proposal.reason == "low_confidence")
    elif all(door.distance_m is not None and door.distance_m > max_distance_m for door in eligible):
        bin_name = "gt_10m"
        correct = bool(proposal.refused and proposal.reason == "out_of_range")
    else:
        nearest = min(door.distance_m for door in eligible if door.distance_m is not None)
        bin_name = "0_5m" if nearest <= 5.0 else "5_10m"
        if proposal.refused:
            correct = proposal.reason == "ambiguous"
        else:
            distances_ok = all(door.distance_m is not None and door.distance_m <= max_distance_m
                               for door in proposal.candidates)
            correct = distances_ok and 1 <= len(proposal.candidates) <= MAX_CANDIDATES
    top_k_hit = _top_k_hit(case, proposal, min_confidence, max_distance_m)
    return {
        "name": case.name,
        "bin": bin_name,
        "correct": correct,
        "refused": bool(proposal.refused),
        "reason": proposal.reason,
        "candidate_count_after_filter": len(proposal.candidates),
        "eligible_count": proposal.candidate_count,
        "total_count": proposal.total_count,
        "gt_available": bool(case.gt_available),
        "top_k_hit": top_k_hit,
        "limit_ok": len(proposal.candidates) <= MAX_CANDIDATES,
    }


def _top_k_hit(case: EvalCase, proposal, min_confidence: float, max_distance_m: float):
    """1/0 when this case should be scored, else None.

    Correct refusals for unknown distance or a near-tie are not top-k misses.
    The hit is whether the nearest in-range ground-truth door is among the
    proposals (at most three).
    """
    if not case.gt_available or len(case.gt) != len(case.doors):
        return None
    positives = []
    for door, is_gt in zip(case.doors, case.gt):
        if not is_gt or door.confidence < min_confidence:
            continue
        if door.distance_m is None or door.distance_m > max_distance_m:
            continue
        positives.append(door)
    if not positives:
        return None
    if proposal.refused and proposal.reason in ("distance_unknown", "ambiguous"):
        return None
    if proposal.refused:
        return 0
    nearest = min(positives, key=lambda door: (door.distance_m, -door.area, -door.confidence))
    return 1 if nearest in proposal.candidates else 0


def score_cases(cases: Sequence[EvalCase], min_confidence: float = 0.5,
                max_distance_m: float = DEFAULT_RANGE_M) -> dict:
    """Run ``propose_doors`` and aggregate limit, refuse, top-k, and distance bins."""
    rows = []
    for case in cases:
        proposal = propose_doors(case.doors, max_candidates=MAX_CANDIDATES,
                                 max_distance_m=max_distance_m, min_confidence=min_confidence,
                                 require_distance=True)
        if len(proposal.candidates) > MAX_CANDIDATES:
            raise RuntimeError("propose_doors returned more than 3 candidates")
        rows.append(classify_case(case, proposal, min_confidence, max_distance_m))
    return aggregate_rows(rows)


def _rate(count: int, total: int):
    if total <= 0:
        return None
    return count / total


def aggregate_rows(rows: Sequence[dict]) -> dict:
    n = len(rows)
    refused = sum(1 for row in rows if row["refused"])
    counts = [int(row["candidate_count_after_filter"]) for row in rows]
    violations = sum(1 for row in rows if not row["limit_ok"] or row["candidate_count_after_filter"] > MAX_CANDIDATES)
    bins = {}
    for name in BINS:
        group = [row for row in rows if row["bin"] == name]
        correct = sum(1 for row in group if row["correct"])
        bins[name] = {"n": len(group), "correct": correct, "correct_rate": _rate(correct, len(group))}
    correct_all = sum(1 for row in rows if row["correct"])
    scored = [row for row in rows if row["top_k_hit"] is not None]
    hits = sum(int(row["top_k_hit"]) for row in scored)
    reasons = {}
    for row in rows:
        reasons[row["reason"]] = reasons.get(row["reason"], 0) + 1
    return {
        "n_cases": n,
        "candidate_count_after_filter": counts,
        "max_candidate_count": max(counts) if counts else 0,
        "mean_candidate_count": (sum(counts) / len(counts)) if counts else None,
        "max_candidates_limit": MAX_CANDIDATES,
        "limit_violations": violations,
        "limit_enforced": violations == 0 and all(count <= MAX_CANDIDATES for count in counts),
        "refuse_count": refused,
        "refuse_rate": _rate(refused, n),
        "refuse_reasons": reasons,
        "top_k": MAX_CANDIDATES,
        "top_k_scored": len(scored),
        "top_k_hits": hits,
        "top_k_hit_rate": _rate(hits, len(scored)),
        "distance_bins": bins,
        "distance_bin_refuse_correct_count": correct_all,
        "distance_bin_refuse_correct_rate": _rate(correct_all, n),
        "missing_distance_refuse_correct_rate": bins["missing"]["correct_rate"],
        "cases": list(rows),
    }


def _round_numbers(value):
    if isinstance(value, dict):
        return {str(k): _round_numbers(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_round_numbers(v) for v in value]
    if isinstance(value, (float, np.floating)):
        value = float(value)
        if not math.isfinite(value):
            return None
        return round(value, 6)
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.bool_,)):
        return bool(value)
    return value


def _fmt_pct(value) -> str:
    return "未计算" if value is None else f"{100.0 * float(value):.1f}%"


def _fmt_count(value) -> str:
    return "未计算" if value is None else f"{float(value):.2f}"


def _summary_md(metrics: dict) -> str:
    if metrics["fallback"]:
        lead = (f"请求的标注不在磁盘上（{metrics['requested']}）。"
                "本次改用合成夹具，不下载 Entrances。")
    elif metrics["mode"] == "synthetic_fixture":
        lead = "本次使用合成门候选夹具。Entrances 图像和标注不是本报告的输入。"
    elif metrics["n_cases"] == 0:
        lead = "目录里没有读到 YOLO 或 JSON 标注。"
    else:
        lead = f"本次读取标注：{metrics['requested']}。"
    bins = metrics["distance_bins"]
    lines = [
        "# Last10m 门候选离线评测",
        "",
        lead,
        "提议来自 last10m.py。只做门候选，不规划接近路径，也不控制。",
        "距离未知的样本必须拒答。过滤后的候选数不超过 3。",
        "纯单目只作为有安全员的低速受控演示。本评测不覆盖 W1 路缘召回和近场危险召回，这两条门槛尚未在这里通过。",
        "",
        f"- 模式：{metrics['mode']}",
        f"- 样本数 n：{metrics['n_cases']}",
        f"- 过滤后候选数最大值：{metrics['max_candidate_count']}（上限 {metrics['max_candidates_limit']}）",
        f"- 过滤后候选数均值：{_fmt_count(metrics['mean_candidate_count'])}",
        f"- 超过 3 个候选的样本：{metrics['limit_violations']}",
        f"- 拒答率：{_fmt_pct(metrics['refuse_rate'])}（{metrics['refuse_count']} 个）",
        f"- top-{metrics['top_k']} 命中率（有 GT 且应当提议时）：{_fmt_pct(metrics['top_k_hit_rate'])}"
        f"（{metrics['top_k_hits']} / {metrics['top_k_scored']}）",
        f"- 距离分箱拒答正确率：{_fmt_pct(metrics['distance_bin_refuse_correct_rate'])}",
        f"- 缺距离分箱正确率：{_fmt_pct(metrics['missing_distance_refuse_correct_rate'])}"
        f"（{bins['missing']['correct']} / {bins['missing']['n']}）",
        f"- 分箱样本：缺距离 {bins['missing']['n']}，0–5 m {bins['0_5m']['n']}，"
        f"5–10 m {bins['5_10m']['n']}，大于 10 m {bins['gt_10m']['n']}，"
        f"无合格候选 {bins['no_eligible']['n']}",
        "",
        "没有地面真值、或因为缺距离 / 截断处难分先后而正确拒答的样本，不计入 top-k 分母。",
        "auto_go 为 false。本报告不给出过街许可。",
        "",
    ]
    return "\n".join(lines)


def run_eval(labels: str, out: str) -> dict:
    """Score ``labels`` and write ``metrics.json`` plus Chinese ``summary.md``."""
    resolved = resolve_labels(labels)
    scored = score_cases(resolved["cases"])
    if not scored["limit_enforced"]:
        raise RuntimeError("Last10m eval failed the <=3 candidate limit")
    metrics = {
        "mode": resolved["mode"],
        "requested": resolved["requested"],
        "fallback": resolved["fallback"],
        "labels_absent": bool(resolved["fallback"]),
        "fixture": FIXTURE_NAME if resolved["fallback"] or resolved["mode"] == "synthetic_fixture" else None,
        "max_distance_m": DEFAULT_RANGE_M,
        "min_confidence": 0.5,
        "require_distance": True,
        "pure_monocular": "supervised_low_speed_constrained_demo",
        "w1_curb_recall_gate": "not_claimed",
        "w1_near_field_recall_gate": "not_claimed",
        "auto_go": False,
        **scored,
    }
    metrics = _round_numbers(metrics)
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, "metrics.json"), "w", encoding="utf-8") as fh:
        json.dump(metrics, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    with open(os.path.join(out, "summary.md"), "w", encoding="utf-8") as fh:
        fh.write(_summary_md(metrics))
    return metrics


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Last10m 门候选离线评测。Entrances 标注缺失时使用合成夹具。")
    parser.add_argument("--labels", required=True, help="标注目录、标注文件，或合成夹具名 synthetic")
    parser.add_argument("--out", required=True, help="写入 metrics.json 与 summary.md 的目录")
    args = parser.parse_args(argv)
    try:
        metrics = run_eval(args.labels, args.out)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"无法评测: {exc}", file=sys.stderr)
        return 2
    print(f"mode={metrics['mode']} n={metrics['n_cases']} "
          f"max_candidates={metrics['max_candidate_count']} "
          f"refuse_rate={metrics['refuse_rate']} "
          f"top_k_hit_rate={metrics['top_k_hit_rate']} "
          f"missing_distance_correct={metrics['missing_distance_refuse_correct_rate']} "
          f"out={args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
