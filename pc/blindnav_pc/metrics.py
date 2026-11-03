"""Metrics from per-frame JSONL logs: 误停率 / 漏停率 / 指令翻转次数 / 延迟."""
from __future__ import annotations

import csv
import json
from typing import List, Optional

import numpy as np


def read_jsonl(path: str) -> List[dict]:
    with open(path, "r", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def load_labels_csv(path: str) -> List[dict]:
    """CSV ``t_ms,should_stop`` (should_stop 1 = the person should be stopped at that time)."""
    with open(path, "r", newline="", encoding="utf-8-sig") as f:
        return [{"t_ms": float(r["t_ms"]), "should_stop": int(float(r["should_stop"]))} for r in csv.DictReader(f)]


def compute_metrics(records: List[dict], labels: Optional[List[dict]] = None, window_ms: float = 2000.0) -> dict:
    recs = list(records)
    cmds = [r.get("command") for r in recs]
    ts = np.array([float(r.get("t_ms", i)) for i, r in enumerate(recs)])
    flip_idx = [i for i in range(1, len(cmds)) if cmds[i] != cmds[i - 1]]
    dur_min = (ts[-1] - ts[0]) / 60000.0 if len(ts) > 1 else 0.0
    flip_t = ts[flip_idx] if flip_idx else np.array([])
    max_win = 0
    for t in flip_t:                                   # most flips inside any window starting at a flip
        max_win = max(max_win, int(((flip_t >= t) & (flip_t < t + window_ms)).sum()))
    out = {"n_frames": len(recs), "command_flips": len(flip_idx),
           "flips_per_min": (len(flip_idx) / dur_min) if dur_min > 0 else 0.0,
           "max_flips_in_window": max_win, "window_ms": window_ms,
           "false_stop_rate": None, "missed_stop_rate": None}
    if labels and recs:
        lt = np.array([float(l["t_ms"]) for l in labels])
        ly = np.array([int(l["should_stop"]) for l in labels])
        y = np.array([ly[int(np.argmin(np.abs(lt - t)))] for t in ts])
        stop = np.array([c == "STOP" for c in cmds])
        neg, pos = (y == 0), (y == 1)
        out["false_stop_rate"] = float((stop & neg).sum() / neg.sum()) if neg.any() else None
        out["missed_stop_rate"] = float((~stop & pos).sum() / pos.sum()) if pos.any() else None
    lat = [float(r["latency_ms"]) for r in recs if r.get("latency_ms") is not None]
    out["latency_ms_p50"] = float(np.percentile(lat, 50)) if lat else None
    out["latency_ms_p95"] = float(np.percentile(lat, 95)) if lat else None
    out["latency_ms_mean"] = float(np.mean(lat)) if lat else None
    return out


def format_report(m: dict) -> str:
    def f(x, nd=3):
        return "N/A（未提供标注）" if x is None else f"{x:.{nd}f}"
    return "\n".join([
        f"帧数：{m.get('n_frames', 0)}",
        f"误停率：{f(m.get('false_stop_rate'))}（不该停却输出 STOP 的帧占比）",
        f"漏停率：{f(m.get('missed_stop_rate'))}（该停却没输出 STOP 的帧占比）",
        f"指令翻转次数：{m.get('command_flips', 0)}（{m.get('flips_per_min', 0):.1f} 次/分钟；"
        f"任意 {m.get('window_ms', 2000)/1000:.0f}s 窗口内最多 {m.get('max_flips_in_window', 0)} 次）",
        f"延迟：p50={f(m.get('latency_ms_p50'), 1)} ms，p95={f(m.get('latency_ms_p95'), 1)} ms，"
        f"均值={f(m.get('latency_ms_mean'), 1)} ms",
    ])
