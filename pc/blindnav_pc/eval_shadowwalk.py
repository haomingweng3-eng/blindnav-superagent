"""Offline straight-path / corridor-keeping metrics for ShadowWalk.

``python -m blindnav_pc.eval_shadowwalk --corridor <json|fixture> --out <dir>``

``--corridor`` is a bundled session fixture (``straight_keep``, ``mild_drift``,
``coverage_and_stop``), a preset corridor (``straight60``, ``left_arc80``,
``right_arc100``), a corridor JSON, or a session JSON.  A missing session file
falls back to the bundled straight-path fixture and still writes ``metrics.json``
and a Chinese ``summary.md``.

The harness calls :class:`blindnav_pc.shadowwalk.ShadowWalkTracker`.  It does not
localise, plan, or control.  Hints stay in LEFT / RIGHT / STRAIGHT / STOP / UNKNOWN.
Pure monocular output here is a supervised low-speed constrained demo score only.
W1 curb recall and near-field hazard recall are not scored and are not claimed.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from typing import Optional, Sequence

import numpy as np

from .shadowwalk import (LEFT, RIGHT, STOP, STRAIGHT, UNKNOWN, Corridor, Pose2D,
                         ShadowWalkConfig, ShadowWalkTracker, get_corridor, PRESETS)

FIXTURE_NAMES = ("straight_keep", "mild_drift", "coverage_and_stop")
FALLBACK_FIXTURE = "straight_keep"
HINTS = (STRAIGHT, LEFT, RIGHT, STOP, UNKNOWN)
_BAND_OFFSETS = (-1.0, -0.8, -0.6, -0.4, -0.2, 0.0, 0.2, 0.4, 0.6, 0.8, 1.0)
_GAP_OFFSETS = tuple(float(v) for v in np.linspace(-0.8, 0.8, 12))


def _keeping_frames(n: int = 8, step_m: float = 1.0, start_m: float = 2.0) -> list:
    return [
        {"s_m": start_m + i * step_m, "offset_m": 0.0, "yaw_error_deg": 0.0, "points": "band"}
        for i in range(n)
    ]


def _fixture_specs() -> dict:
    return {
        "straight_keep": {
            "name": "straight_keep",
            "corridor": "straight60",
            "frames": _keeping_frames(),
        },
        "mild_drift": {
            "name": "mild_drift",
            "corridor": "straight60",
            "frames": [
                {"s_m": 2.0, "offset_m": 0.0, "yaw_error_deg": 0.0, "points": "band"},
                {"s_m": 3.0, "offset_m": 0.5, "yaw_error_deg": 0.0, "points": "band"},
                {"s_m": 4.0, "offset_m": 0.6, "yaw_error_deg": 0.0, "points": "band"},
                {"s_m": 5.0, "offset_m": -0.5, "yaw_error_deg": 0.0, "points": "band"},
                {"s_m": 6.0, "offset_m": -0.6, "yaw_error_deg": 0.0, "points": "band"},
            ],
        },
        "coverage_and_stop": {
            "name": "coverage_and_stop",
            "corridor": "straight60",
            "frames": [
                {"s_m": 2.0, "offset_m": 0.0, "yaw_error_deg": 0.0, "points": "band"},
                {"s_m": 3.0, "offset_m": 0.0, "yaw_error_deg": 0.0, "points": "none"},
                {"s_m": 4.0, "offset_m": 0.0, "yaw_error_deg": 0.0, "points": "gap"},
                {"s_m": 5.0, "offset_m": 1.2, "yaw_error_deg": 0.0, "points": "band"},
                {"s_m": 6.0, "offset_m": 0.0, "yaw_error_deg": 40.0, "points": "band"},
                {"pose": None},
            ],
        },
    }


def _load_json(path: str) -> dict:
    with open(path, "r", encoding="utf-8-sig") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise ValueError(f"{path} must be a JSON object")
    return data


def _corridor_from_spec(spec) -> Corridor:
    if isinstance(spec, Corridor):
        return spec
    if isinstance(spec, str):
        if spec in PRESETS:
            return get_corridor(spec)
        raise ValueError(f"unknown corridor {spec!r}; choose from {sorted(PRESETS)}")
    if isinstance(spec, dict):
        if "points" in spec:
            return Corridor.from_dict(spec)
        if "corridor" in spec:
            return _corridor_from_spec(spec["corridor"])
    raise ValueError("corridor spec needs a preset name or {name,width_m,points}")


def _as_session(data: dict, default_name: str) -> dict:
    if "frames" in data:
        if not isinstance(data["frames"], list) or not data["frames"]:
            raise ValueError("session frames must be a non-empty list")
        corridor = _corridor_from_spec(data.get("corridor", data))
        return {"name": str(data.get("name", default_name)), "corridor": corridor,
                "frames": data["frames"]}
    if "points" in data:
        corridor = Corridor.from_dict(data)
        return {"name": corridor.name, "corridor": corridor, "frames": _keeping_frames()}
    raise ValueError("JSON must be a corridor {points: [...]} or a session {frames: [...]}")


def resolve_corridor(corridor_arg: str) -> dict:
    """Return a session plus how it was chosen.

    Keys: ``session`` (name, corridor, frames), ``mode``, ``requested``, ``fallback``.
    """
    specs = _fixture_specs()
    requested = corridor_arg
    if corridor_arg in specs:
        spec = specs[corridor_arg]
        return {
            "session": {"name": spec["name"], "corridor": _corridor_from_spec(spec["corridor"]),
                        "frames": spec["frames"]},
            "mode": "synthetic_fixture",
            "requested": requested,
            "fallback": False,
            "fixture": corridor_arg,
        }
    if corridor_arg in PRESETS:
        corridor = get_corridor(corridor_arg)
        return {
            "session": {"name": corridor.name, "corridor": corridor, "frames": _keeping_frames()},
            "mode": "synthetic_preset",
            "requested": requested,
            "fallback": False,
            "fixture": corridor_arg,
        }
    looks_like_path = os.path.sep in corridor_arg or corridor_arg.endswith(".json")
    if os.path.isfile(corridor_arg):
        data = _load_json(corridor_arg)
        session = _as_session(data, os.path.splitext(os.path.basename(corridor_arg))[0])
        mode = "session_json" if "frames" in data else "corridor_json"
        return {"session": session, "mode": mode, "requested": os.path.abspath(corridor_arg),
                "fallback": False, "fixture": None}
    if looks_like_path or not os.path.exists(corridor_arg):
        if looks_like_path:
            spec = specs[FALLBACK_FIXTURE]
            return {
                "session": {"name": spec["name"],
                            "corridor": _corridor_from_spec(spec["corridor"]),
                            "frames": spec["frames"]},
                "mode": "synthetic_fallback",
                "requested": corridor_arg,
                "fallback": True,
                "fixture": FALLBACK_FIXTURE,
            }
    known = ", ".join(list(FIXTURE_NAMES) + sorted(PRESETS))
    raise ValueError(f"unknown corridor {corridor_arg!r}; choose a fixture, preset, or JSON ({known})")


def _sample_band(corridor: Corridor, s_from: float, s_to: float, offsets: Sequence[float],
                 step_m: float) -> np.ndarray:
    if s_to < s_from:
        return np.zeros((0, 2))
    samples = np.arange(s_from, s_to + 1e-9, step_m)
    if len(samples) == 0:
        return np.zeros((0, 2))
    offs = np.asarray(offsets, dtype=np.float64)
    chunks = []
    for s in samples:
        x, z, rx, rz = corridor.point_at(float(s))
        chunks.append(np.column_stack([x + rx * offs, z + rz * offs]))
    return np.vstack(chunks)


def points_for_frame(corridor: Corridor, frame: dict, pose: Optional[Pose2D],
                     cfg: ShadowWalkConfig) -> np.ndarray:
    """Walkable map points for one synthetic frame.  ``band`` covers the tracker window."""
    spec = frame.get("points", "band")
    if isinstance(spec, list):
        if not spec:
            return np.zeros((0, 2))
        return np.asarray(spec, dtype=np.float64).reshape(-1, 2)
    if spec == "none" or pose is None:
        return np.zeros((0, 2))
    s0, _ = corridor.project(pose.x_m, pose.z_m)
    if spec == "gap":
        # One coverage bin only: enough points to pass min_points, not enough bins.
        return _sample_band(corridor, s0 + cfg.near_m, s0 + cfg.near_m + cfg.bin_m - 0.05,
                            _GAP_OFFSETS, 0.05)
    if spec == "band":
        return _sample_band(corridor, s0 + cfg.near_m, s0 + cfg.far_m, _BAND_OFFSETS, 0.2)
    raise ValueError(f"unknown points spec {spec!r}")


def _pose_for_frame(corridor: Corridor, frame: dict) -> Optional[Pose2D]:
    if "pose" in frame and frame["pose"] is None:
        return None
    if isinstance(frame.get("pose"), dict):
        raw = frame["pose"]
        return Pose2D(float(raw["x_m"]), float(raw["z_m"]), float(raw.get("yaw_rad", 0.0)))
    if "s_m" not in frame:
        raise ValueError("frame needs s_m or pose")
    return corridor.pose_at(float(frame["s_m"]), offset_m=float(frame.get("offset_m", 0.0)),
                            yaw_error_rad=math.radians(float(frame.get("yaw_error_deg", 0.0))))


def run_session(session: dict, cfg: Optional[ShadowWalkConfig] = None) -> list:
    """Replay one session through :class:`ShadowWalkTracker` and return per-frame rows."""
    config = cfg or ShadowWalkConfig()
    corridor = session["corridor"]
    tracker = ShadowWalkTracker(corridor, config)
    rows = []
    for index, frame in enumerate(session["frames"]):
        if not isinstance(frame, dict):
            raise ValueError(f"frame {index} must be an object")
        pose = _pose_for_frame(corridor, frame)
        points = points_for_frame(corridor, frame, pose, config)
        hint = tracker.update(points, pose)
        if hint.hint not in HINTS:
            raise RuntimeError(f"hint left the ShadowWalk set: {hint.hint!r}")
        yaw = hint.yaw_error_rad
        rows.append({
            "index": index,
            "hint": hint.hint,
            "text": hint.text,
            "reason": hint.reason,
            "offset_m": hint.offset_m if math.isfinite(hint.offset_m) else None,
            "yaw_error_deg": math.degrees(yaw) if math.isfinite(yaw) else None,
            "coverage": float(hint.coverage),
            "n_points": int(hint.n_points),
            "progress_m": float(hint.progress_m) if math.isfinite(hint.progress_m) else None,
        })
    return rows


def _linear_percentile(values: Sequence[float], q: float) -> Optional[float]:
    xs = sorted(float(v) for v in values)
    if not xs:
        return None
    if len(xs) == 1:
        return xs[0]
    pos = (q / 100.0) * (len(xs) - 1)
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    weight = pos - lo
    return xs[lo] * (1.0 - weight) + xs[hi] * weight


def _rate(count: int, total: int) -> Optional[float]:
    if total <= 0:
        return None
    return count / total


def summarize_frames(rows: Sequence[dict], min_coverage: float = 0.5) -> dict:
    """Lateral MAE/P95, heading drift, correction/flip counts, UNKNOWN/STOP, coverage fail.

    Lateral and heading stats use frames whose pose projection is finite.  A coverage
    failure is a frame the tracker rejected with ``corridor_unseen``.  A flip is an
    adjacent LEFT↔RIGHT pair.  A correction is a LEFT or RIGHT hint.
    """
    n = len(rows)
    offsets = [abs(float(row["offset_m"])) for row in rows if row["offset_m"] is not None]
    yaws = [float(row["yaw_error_deg"]) for row in rows if row["yaw_error_deg"] is not None]
    hints = [row["hint"] for row in rows]
    counts = {hint: sum(1 for hint_i in hints if hint_i == hint) for hint in HINTS}
    correction_count = counts[LEFT] + counts[RIGHT]
    flip_count = sum(1 for a, b in zip(hints, hints[1:]) if {a, b} == {LEFT, RIGHT})
    coverage_fail_count = sum(1 for row in rows if row["reason"] == "corridor_unseen")
    unknown_count = counts[UNKNOWN]
    stop_count = counts[STOP]
    coverages = [float(row["coverage"]) for row in rows if row["offset_m"] is not None]
    illegal = 0
    for row in rows:
        text = f"{row['hint']} {row.get('text', '')}"
        if "CROSS" in text.upper() or "可以过街" in text or "现在走" in text:
            illegal += 1
    return {
        "n_frames": n,
        "n_offset_frames": len(offsets),
        "lateral_offset_mae_m": (sum(offsets) / len(offsets)) if offsets else None,
        "lateral_offset_p95_m": _linear_percentile(offsets, 95),
        "heading_drift_deg": (sum(abs(v) for v in yaws) / len(yaws)) if yaws else None,
        "heading_drift_net_deg": (yaws[-1] - yaws[0]) if yaws else None,
        "correction_count": correction_count,
        "correction_rate": _rate(correction_count, n),
        "flip_count": flip_count,
        "flip_rate": _rate(flip_count, n - 1) if n else None,
        "unknown_count": unknown_count,
        "unknown_rate": _rate(unknown_count, n),
        "stop_count": stop_count,
        "stop_rate": _rate(stop_count, n),
        "coverage_fail_count": coverage_fail_count,
        "coverage_fail_rate": _rate(coverage_fail_count, n),
        "mean_coverage": (sum(coverages) / len(coverages)) if coverages else None,
        "min_coverage": float(min_coverage),
        "hint_counts": counts,
        "illegal_hints": illegal,
        "auto_go": False,
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


def _fmt_m(value) -> str:
    return "未计算" if value is None else f"{float(value):.3f} m"


def _fmt_deg(value) -> str:
    return "未计算" if value is None else f"{float(value):.2f}°"


def _fmt_pct(value) -> str:
    return "未计算" if value is None else f"{100.0 * float(value):.1f}%"


def _summary_md(metrics: dict) -> str:
    if metrics["fallback"]:
        lead = (f"请求的 session 不在磁盘上（{metrics['requested']}）。"
                f"本次改用合成夹具 {metrics['fixture']}，不读取 Blind-road 图像。")
    elif metrics["mode"] == "synthetic_fixture":
        lead = f"本次使用合成夹具 {metrics['fixture']}。真实走廊 session 不是本报告的输入。"
    elif metrics["mode"] == "synthetic_preset":
        lead = (f"预置走廊 {metrics['corridor']['name']} 上走了一条合成的贴中心线轨迹，"
                "用来做直行 / 走廊保持的基线。")
    else:
        lead = f"本次读取 {metrics['mode']}：{metrics['requested']}。"
    counts = metrics["hint_counts"]
    lines = [
        "# ShadowWalk 走廊保持离线评测",
        "",
        lead,
        "指标来自 shadowwalk.py 的走廊投影和提示。这里不定位、不规划、不控制。",
        "纯单目只作为有安全员的低速受控演示。本评测不覆盖 W1 路缘召回和近场危险召回，这两条门槛尚未在这里通过。",
        "",
        f"- 模式：{metrics['mode']}",
        f"- 走廊：{metrics['corridor']['name']}，长度 {metrics['corridor']['length_m']:.1f} m，"
        f"宽度 {metrics['corridor']['width_m']:.1f} m",
        f"- 帧数 n：{metrics['n_frames']}",
        f"- 横向偏移 MAE：{_fmt_m(metrics['lateral_offset_mae_m'])}，"
        f"P95：{_fmt_m(metrics['lateral_offset_p95_m'])}",
        f"- 航向漂移（相对走廊切向的绝对误差均值）：{_fmt_deg(metrics['heading_drift_deg'])}",
        f"- 航向净变化（末帧减首帧）：{_fmt_deg(metrics['heading_drift_net_deg'])}",
        f"- 修正次数（LEFT / RIGHT）：{metrics['correction_count']}",
        f"- 翻转次数（相邻帧 LEFT 与 RIGHT 对调）：{metrics['flip_count']}",
        f"- UNKNOWN 率：{_fmt_pct(metrics['unknown_rate'])}（{metrics['unknown_count']} 帧）",
        f"- STOP 率：{_fmt_pct(metrics['stop_rate'])}（{metrics['stop_count']} 帧）",
        f"- 覆盖失败率（corridor_unseen）：{_fmt_pct(metrics['coverage_fail_rate'])}"
        f"（{metrics['coverage_fail_count']} 帧）",
        f"- 提示计数：直行 {counts[STRAIGHT]}，向左 {counts[LEFT]}，向右 {counts[RIGHT]}，"
        f"停下 {counts[STOP]}，无法确认 {counts[UNKNOWN]}",
        "",
        "横向误差只统计有位姿的帧。覆盖失败只统计走廊覆盖不足的帧。",
        "提示只有沿走廊直行、稍向左、稍向右、请停下、前方走廊无法确认。auto_go 为 false。",
        "",
    ]
    return "\n".join(lines)


def run_eval(corridor: str, out: str, cfg: Optional[ShadowWalkConfig] = None) -> dict:
    """Score ``corridor`` and write ``metrics.json`` plus Chinese ``summary.md``."""
    resolved = resolve_corridor(corridor)
    config = cfg or ShadowWalkConfig()
    session = resolved["session"]
    rows = run_session(session, config)
    scored = summarize_frames(rows, min_coverage=config.min_coverage)
    corridor_obj = session["corridor"]
    metrics = {
        "mode": resolved["mode"],
        "requested": resolved["requested"],
        "fallback": resolved["fallback"],
        "fixture": resolved["fixture"],
        "session": session["name"],
        "session_absent": bool(resolved["fallback"]),
        "corridor": {
            "name": corridor_obj.name,
            "width_m": corridor_obj.width_m,
            "length_m": corridor_obj.length_m,
            "points": [[float(x), float(z)] for x, z in corridor_obj.points],
        },
        "pure_monocular": "supervised_low_speed_constrained_demo",
        "w1_curb_recall_gate": "not_claimed",
        "w1_near_field_recall_gate": "not_claimed",
        "states": list(HINTS),
        "frames": rows,
        **scored,
    }
    if metrics["illegal_hints"] != 0 or metrics["auto_go"] is not False:
        raise RuntimeError("ShadowWalk eval emitted a forbidden hint")
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
        description="ShadowWalk 直行 / 走廊保持离线评测。真实 session 缺失时使用合成夹具。")
    parser.add_argument("--corridor", required=True,
                        help="走廊 JSON、session JSON、预置走廊名，或合成夹具名")
    parser.add_argument("--out", required=True, help="写入 metrics.json 与 summary.md 的目录")
    args = parser.parse_args(argv)
    try:
        metrics = run_eval(args.corridor, args.out)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"无法评测: {exc}", file=sys.stderr)
        return 2
    print(f"mode={metrics['mode']} n={metrics['n_frames']} "
          f"lateral_mae={metrics['lateral_offset_mae_m']} "
          f"heading_drift_deg={metrics['heading_drift_deg']} "
          f"corrections={metrics['correction_count']} flips={metrics['flip_count']} "
          f"unknown_rate={metrics['unknown_rate']} stop_rate={metrics['stop_rate']} "
          f"coverage_fail_rate={metrics['coverage_fail_rate']} out={args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
