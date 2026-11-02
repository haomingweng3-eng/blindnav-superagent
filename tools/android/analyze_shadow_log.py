#!/usr/bin/env python3
"""分析 BlindNavSensorLogger 落下的 events.jsonl，产出真机阶段要的那几张表。

只用标准库（json/statistics），**不要**用系统 Homebrew python：那台机器上 numpy 会先加载
/opt/homebrew/Cellar/libomp，Homebrew torch 又自带一份，import torch 直接 OMP Error #15 abort。
任何 python3 都能跑本脚本。

用法：
    python3 tools/android/analyze_shadow_log.py <shadowlog 目录> [--markdown out.md]

字段口径与 app/src/main/java/com/blindnav/app/shadow/ 一一对应，单位：
  *_us 微秒、*_ms 毫秒、*_ns 纳秒、*_m 米、*_px 像素。
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path


def percentiles(values, unit=""):
    if not values:
        return {"count": 0, "unit": unit}
    ordered = sorted(values)

    def pct(p):
        index = min(len(ordered) - 1, int(round(p * (len(ordered) - 1))))
        return ordered[index]

    return {
        "unit": unit,
        "count": len(ordered),
        "median": round(statistics.median(ordered), 3),
        "p90": round(pct(0.90), 3),
        "p95": round(pct(0.95), 3),
        "max": round(ordered[-1], 3),
    }


def load(session_dir: Path):
    events = []
    path = session_dir / "events.jsonl"
    if not path.exists():
        return [], {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            events.append({"type": "PARSE_ERROR"})
    meta = {}
    meta_path = session_dir / "meta.json"
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            meta = {"parse_error": True}
    return events, meta


def series(source, key, cast=float):
    out = []
    for e in source:
        value = e.get(key)
        if value is None:
            continue
        try:
            out.append(cast(value))
        except (TypeError, ValueError):
            continue
    return out


def analyze(session_dir: Path):
    events, meta = load(session_dir)
    by_type = Counter(e.get("type", "?") for e in events)
    frames = [e for e in events if e.get("type") == "FRAME"]
    poses = [e for e in events if e.get("type") == "POSE"]
    shadows = [e for e in events if e.get("type") == "SHADOW"]
    telemetry = [e for e in events if e.get("type") == "TELEMETRY"]
    sessions = [e for e in events if e.get("type") == "SESSION"]
    selftests = [e for e in events if e.get("type") == "SELFTEST"]
    inference = [e for e in events if e.get("type") == "INFERENCE"]
    wp_shadows = [e for e in shadows if e.get("policy") == "worldpath"]
    sma_shadows = [e for e in shadows if e.get("policy") == "safe_motion"]

    report = {
        "session": session_dir.name,
        "event_counts": dict(by_type),
        "capability": next((s for s in sessions if s.get("event") == "capability"), None),
        "session_stop": next((s for s in sessions if s.get("event") == "stop"), None),
        "fatal": [s.get("reason") for s in sessions if s.get("event") == "fatal"],
        "meta_latency": meta.get("latency_ms", {}),
        "meta_dropped": meta.get("events_dropped"),
    }

    report["selftest"] = {
        "runs": len(selftests),
        "checks": (selftests[-1].get("checks") if selftests else None),
        "failed": [c for c in (selftests[-1].get("checks", "").split(" | ") if selftests else [])
                   if "=FAIL" in c or "=ERROR" in c],
    }

    # 时间基准：这是"能不能拿 ARCore 当主时钟"的直接证据，不是通过/失败闸门。
    report["clock"] = {
        "camera_ts_source": next((f.get("camera_ts_source") for f in frames if f.get("camera_ts_source")), None),
        "cross_domain_comparable": any(f.get("cross_domain_comparable") for f in frames),
        "ar_cam_delta_ns": max((f.get("ar_cam_delta_ns") or 0) for f in frames) if frames else None,
        "ar_cam_delta_spread_ns": max([f.get("ar_cam_delta_spread_ns") or 0 for f in frames], default=None),
        "sampled_frames": len(frames),
        "note": "spread 只说明 ar-cam 差值是否稳定；能否跨域相减取决于 camera_ts_source 是否 REALTIME。",
    }

    tracking = Counter(e.get("tracking") for e in poses) or Counter(e.get("tracking") for e in frames)
    report["tracking"] = {
        "pose_events": len(poses),
        "counts": dict(tracking),
        "failure_reasons": dict(Counter(e.get("failure") for e in poses if e.get("failure"))),
        "yaw_step_deg": None,
    }
    yaw = series(poses, "yaw_deg")
    if len(yaw) > 2:
        deltas = [abs(((b - a + 540) % 360) - 180) for a, b in zip(yaw, yaw[1:]) if a is not None and b is not None]
        report["tracking"]["yaw_step_deg"] = percentiles(deltas, "deg/frame")
    translation = []
    for a, b in zip(poses, poses[1:]):
        try:
            translation.append(((b["tx"] - a["tx"]) ** 2 + (b["tz"] - a["tz"]) ** 2) ** 0.5)
        except (KeyError, TypeError):
            continue
    report["tracking"]["step_distance_m"] = percentiles(translation, "m/frame")

    report["depth"] = {
        "coverage": percentiles(series(frames, "depth_coverage"), "ratio"),
        "confident_coverage": percentiles(series(frames, "raw_depth_confident_coverage"), "ratio"),
        "mean_confidence": percentiles(series(frames, "raw_depth_mean_confidence"), "0-255"),
        "nearband_median_m": percentiles(series(frames, "depth_nearband_median_m"), "m"),
        "raw_nearband_median_m": percentiles(series(frames, "raw_depth_nearband_median_m"), "m"),
        "not_yet_available": sum(1 for e in frames if e.get("depth_unavailable_reason") == "NOT_YET_AVAILABLE"),
        "unavailable_reasons": dict(Counter(e.get("depth_unavailable_reason") for e in frames
                                            if e.get("depth_unavailable_reason"))),
        "errors": dict(Counter(e.get("depth_error") for e in frames if e.get("depth_error"))),
        "staleness_ms": percentiles(series(frames, "depth_staleness_ms"), "ms"),
        # 位宽约定的经验判据：真机若出现 >8191mm 的像素，说明这台设备确实走完整 16-bit。
        "px_beyond_8191mm": max([e.get("depth_px_beyond_8191mm") or 0 for e in frames], default=0),
        "raw_px_beyond_8191mm": max([e.get("raw_depth_px_beyond_8191mm") or 0 for e in frames], default=0),
        "max_mm_seen": max([e.get("depth_max_mm_seen") or 0 for e in frames], default=0),
        "conf_size_matches": all(e.get("raw_depth_conf_size_matches", True) for e in frames),
        "jitter": {
            "static_pairs": max([e.get("depth_static_pairs") or 0 for e in frames], default=0),
            "static_holds": max([e.get("depth_static_holds") or 0 for e in frames], default=0),
            "mean_m": max([e.get("depth_jitter_mean_m") or 0 for e in frames], default=None),
            "max_m": max([e.get("depth_jitter_max_m") or 0 for e in frames], default=None),
        },
    }

    report["rgb"] = {
        "acquire_convert_us": percentiles(series(frames, "rgb_acquire_convert_us"), "us"),
        "missing_frames": sum(1 for e in frames if e.get("rgb_available") is False),
        "truncated_frames": sum(1 for e in frames if e.get("rgb_truncated")),
        "errors": dict(Counter(e.get("rgb_error") for e in frames if e.get("rgb_error"))),
        "sizes": dict(Counter("%sx%s" % (e.get("rgb_w"), e.get("rgb_h")) for e in frames if e.get("rgb_w"))),
    }

    seg = [e for e in inference if e.get("model") == "segformer_cityscapes"]
    seg_runs = [e for e in seg if e.get("seg_total_us") is not None]
    report["segmentation"] = {
        "events": len(seg),
        "runs": len(seg_runs),
        "load": next(({"available": e.get("available"), "path": e.get("path"),
                       "input_size": e.get("input_size"), "reason": e.get("reason")}
                      for e in seg if e.get("event") == "load"), None),
        "preprocess_us": percentiles(series(seg_runs, "seg_preprocess_us"), "us"),
        "inference_us": percentiles(series(seg_runs, "seg_inference_us"), "us"),
        "postprocess_us": percentiles(series(seg_runs, "seg_postprocess_us"), "us"),
        "total_us": percentiles(series(seg_runs, "seg_total_us"), "us"),
        "traversable_ratio": percentiles(series(seg_runs, "seg_traversable_ratio"), "ratio"),
        "last_class_hist": (seg_runs[-1].get("seg_class_hist") if seg_runs else None),
        "run_failed": sum(1 for e in seg if e.get("event") == "run_failed"),
    }
    report["scene_semantics"] = {
        "frames_with_semantics": sum(1 for e in frames if e.get("sem_grid")),
        "traversable_ratio": percentiles(series(frames, "sem_traversable_ratio"), "ratio"),
        "aspect_mismatch_frames": sum(1 for e in frames if e.get("sem_aspect_mismatch")),
        "label_hist": next((e.get("sem_label_hist") for e in reversed(frames) if e.get("sem_label_hist")), None),
        "curb_boundary_available": False,
    }

    det = [e for e in inference if e.get("model") == "yolov8n"]
    det_runs = [e for e in det if e.get("det_us") is not None]
    report["detector"] = {
        "runs": len(det_runs),
        "us": percentiles(series(det_runs, "det_us"), "us"),
        "hit_rate": round(sum(1 for e in det_runs if (e.get("det_hits") or 0) > 0) / max(1, len(det_runs)), 4),
        "names": dict(Counter(n for e in det_runs for n in (e.get("det_names") or "").split(",") if n)),
    }

    def shadow_block(items, action_key):
        actions = [e.get(action_key) for e in items if e.get(action_key)]
        flips = sum(1 for a, b in zip(actions, actions[1:]) if a != b)
        return {
            "records": len(items),
            "actions": dict(Counter(actions)),
            "command_flip_rate": round(flips / max(1, len(actions) - 1), 4) if len(actions) > 1 else None,
            "lateral_error_m": percentiles(series(items, "wp_lateral_error_m"), "m"),
            "confidence": percentiles(series(items, "wp_confidence"), "ratio"),
            "stale_ms": percentiles(series(items, "wp_stale_ms"), "ms"),
            "evidence_rejected": dict(Counter(e.get("wp_evidence_rejected") for e in items
                                              if e.get("wp_evidence_rejected"))),
        }

    report["worldpath_shadow"] = shadow_block(wp_shadows, "wp_would_have_said")
    report["worldpath_shadow"]["reproject_max_px"] = percentiles(series(wp_shadows, "wp_reproject_max_px"), "px")
    report["worldpath_shadow"]["points"] = percentiles(series(wp_shadows, "wp_points"), "count")
    report["worldpath_shadow"]["mask_source"] = dict(Counter(e.get("wp_mask_source") for e in wp_shadows
                                                             if e.get("wp_mask_source")))
    report["safe_motion_shadow"] = {
        "records": len(sma_shadows),
        "actions": dict(Counter(e.get("sma_would_have_said") for e in sma_shadows if e.get("sma_would_have_said"))),
        "would_stop": sum(1 for e in sma_shadows if e.get("sma_would_stop")),
        "unknown_candidates": sum(1 for e in sma_shadows if e.get("unknown_obstacle_candidate")),
        "evidence_stale": sum(1 for e in sma_shadows if e.get("sma_evidence_stale")),
        "traversability_source": dict(Counter(e.get("sma_traversability_source") for e in sma_shadows
                                              if e.get("sma_traversability_source"))),
        "mask_age_ms": percentiles(series(sma_shadows, "sma_mask_age_ms"), "ms"),
    }

    report["telemetry"] = {
        "samples": len(telemetry),
        "fps": percentiles(series(telemetry, "fps"), "fps"),
        "cpu_pct": percentiles(series(telemetry, "cpu_pct"), "%"),
        "pss_mb": percentiles([v / 1024.0 for v in series(telemetry, "pss_kb")], "MB"),
        "thermal_first": (telemetry[0].get("thermal_status") if telemetry else None),
        "thermal_last": (telemetry[-1].get("thermal_status") if telemetry else None),
        "battery_first": (telemetry[0].get("battery_pct") if telemetry else None),
        "battery_last": (telemetry[-1].get("battery_pct") if telemetry else None),
    }

    overlay_index = session_dir / "overlay" / "index.jsonl"
    report["overlay"] = {
        "frames_saved": sum(1 for _ in overlay_index.read_text(encoding="utf-8").splitlines())
        if overlay_index.exists() else 0,
        "must_inspect_by_eye": True,
    }
    return report


def to_markdown(reports, out: Path):
    lines = ["# BlindNav 真机影子日志分析", "",
             "> 本报告全部来自真机 events.jsonl。凡 count=0 的项表示没有采到数据，不是没问题。", ""]
    for r in reports:
        lines.append("## %s" % r["session"])
        cap = r.get("capability") or {}
        lines += ["", "| 项 | 值 |", "|---|---|"]
        for key in ("arcore_availability", "arcore_version", "arcore_depth_automatic",
                    "arcore_depth_raw_only", "arcore_depth_mode_used", "arcore_semantic_enabled",
                    "camera_timestamp_source", "arcore_failure"):
            if key in cap:
                lines.append("| %s | %s |" % (key, cap.get(key)))
        stop = r.get("session_stop") or {}
        lines.append("| frames / fresh / image / depth / depth_not_yet / tracking | %s / %s / %s / %s / %s / %s |" % (
            stop.get("frames"), stop.get("fresh_frames"), stop.get("frames_with_image"),
            stop.get("frames_with_depth"), stop.get("frames_depth_not_yet"), stop.get("frames_tracking")))
        lines.append("| events_dropped | %s |" % r.get("meta_dropped"))
        lines.append("| fatal | %s |" % (r.get("fatal") or "-"))
        lines.append("")
        for section in ("selftest", "clock", "tracking", "rgb", "depth", "scene_semantics",
                        "segmentation", "detector", "worldpath_shadow", "safe_motion_shadow",
                        "telemetry", "overlay"):
            lines += ["### %s" % section, "```json",
                      json.dumps(r.get(section), ensure_ascii=False, indent=2, default=str), "```", ""]
        lines += ["### latency (device-side reservoir, us)", "```json",
                  json.dumps(r.get("meta_latency"), ensure_ascii=False, indent=2, default=str), "```", ""]
    out.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("log_dir", help="shadowlog 目录（内含 session-*）")
    parser.add_argument("--markdown", help="额外输出 markdown 报告路径")
    args = parser.parse_args()

    root = Path(args.log_dir)
    sessions = sorted(p for p in root.glob("session-*") if p.is_dir()) if root.is_dir() else []
    if not sessions and (root / "events.jsonl").exists():
        sessions = [root]
    if not sessions:
        print("找不到 session-*/events.jsonl：%s" % root, file=sys.stderr)
        return 2

    reports = [analyze(s) for s in sessions]
    print(json.dumps(reports if len(reports) > 1 else reports[0], ensure_ascii=False, indent=2, default=str))
    if args.markdown:
        to_markdown(reports, Path(args.markdown))
        print("markdown -> %s" % args.markdown, file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
