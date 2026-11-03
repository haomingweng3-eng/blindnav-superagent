"""Metrics from per-frame JSONL logs.

Labels (CSV): ``t_ms`` plus either the legacy ``should_stop`` (0/1) or per-frame ``action`` in
FORWARD / LEFT / RIGHT / STOP and an optional ``preferred_side`` (left | right | center | empty).

Prediction classes: FORWARD, RECENTER (= forward-like, it is not an avoidance), LEFT, RIGHT, STOP,
OBSERVE (hold; neither forward nor stop).
  * unsafe_forward : predicted FORWARD/RECENTER although the label wants STOP / LEFT / RIGHT
                     (denominator: frames whose label is not FORWARD); ``unsafe_forward_stop`` = label STOP only.
  * false_stop     : predicted STOP although the label is not STOP (denominator: label != STOP frames).
  * wrong_side     : predicted LEFT/RIGHT opposite to the labelled side (preferred_side, else the label action),
                     denominator = frames with a predicted side command and a labelled side.
  * oscillation    : L<->R reversals per minute (a side command opposite to the previous side command that was
                     issued < ``reversal_window_ms`` earlier) and the total command flips per minute.
  * latency        : p50 / p95 / mean of per-frame ``latency_ms``.
"""
from __future__ import annotations

import csv
import json
from typing import List, Optional, Sequence

import numpy as np


def read_jsonl(path: str) -> List[dict]:
    with open(path, "r", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


ACTIONS = ("FORWARD", "LEFT", "RIGHT", "STOP")


def load_labels_csv(path: str) -> List[dict]:
    """CSV ``t_ms`` + (``should_stop`` | ``action`` [+ ``preferred_side``]).  A legacy ``should_stop`` row is
    converted to action STOP / FORWARD."""
    out = []
    with open(path, "r", newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            row = {"t_ms": float(r["t_ms"])}
            act = (r.get("action") or "").strip().upper()
            if act and act not in ACTIONS:
                raise ValueError(f"label action must be one of {ACTIONS}, got {act!r}")
            if r.get("should_stop") not in (None, ""):
                row["should_stop"] = int(float(r["should_stop"]))
            if act:
                row["action"] = act
                row.setdefault("should_stop", int(act == "STOP"))
            if "should_stop" not in row:
                raise ValueError("label row needs 'should_stop' or 'action'")
            side = (r.get("preferred_side") or "").strip().lower()
            if side not in ("", "left", "right", "center"):
                raise ValueError(f"preferred_side must be left|right|center|empty, got {side!r}")
            if act or side:
                row["preferred_side"] = side
            out.append(row)
    return out


def _pred_class(c: Optional[str]) -> str:
    return "FORWARD" if c == "RECENTER" else (c or "")


def side_reversals(cmds: Sequence[str], ts: np.ndarray, window_ms: float) -> int:
    last_side, last_t, n = "", -1e18, 0
    for c, t in zip(cmds, ts):
        if c in ("LEFT", "RIGHT"):
            if last_side and c != last_side and t - last_t <= window_ms:
                n += 1
            last_side, last_t = c, t
    return n


def compute_metrics(records: List[dict], labels: Optional[List[dict]] = None, window_ms: float = 2000.0,
                    reversal_window_ms: float = 2000.0) -> dict:
    recs = list(records)
    cmds = [r.get("command") for r in recs]
    ts = np.array([float(r.get("t_ms", i)) for i, r in enumerate(recs)])
    flip_idx = [i for i in range(1, len(cmds)) if cmds[i] != cmds[i - 1]]
    dur_min = (ts[-1] - ts[0]) / 60000.0 if len(ts) > 1 else 0.0
    flip_t = ts[flip_idx] if flip_idx else np.array([])
    max_win = 0
    for t in flip_t:                                   # most flips inside any window starting at a flip
        max_win = max(max_win, int(((flip_t >= t) & (flip_t < t + window_ms)).sum()))
    rev = side_reversals(cmds, ts, reversal_window_ms) if recs else 0
    out = {"n_frames": len(recs), "command_flips": len(flip_idx),
           "flips_per_min": (len(flip_idx) / dur_min) if dur_min > 0 else 0.0,
           "max_flips_in_window": max_win, "window_ms": window_ms,
           "side_reversals": rev, "oscillation_per_min": (rev / dur_min) if dur_min > 0 else 0.0,
           "false_stop_rate": None, "missed_stop_rate": None, "unsafe_forward_rate": None,
           "unsafe_forward_stop_rate": None, "wrong_side_rate": None}
    if labels and recs:
        lt = np.array([float(l["t_ms"]) for l in labels])
        near = [labels[int(np.argmin(np.abs(lt - t)))] for t in ts]
        y = np.array([int(l["should_stop"]) for l in near])
        stop = np.array([c == "STOP" for c in cmds])
        neg, pos = (y == 0), (y == 1)
        out["false_stop_rate"] = float((stop & neg).sum() / neg.sum()) if neg.any() else None
        out["missed_stop_rate"] = float((~stop & pos).sum() / pos.sum()) if pos.any() else None
        if all("action" in l for l in labels):
            gt = np.array([l["action"] for l in near])
            pred = np.array([_pred_class(c) for c in cmds])
            fwd = pred == "FORWARD"
            need = gt != "FORWARD"
            out["unsafe_forward_rate"] = float((fwd & need).sum() / need.sum()) if need.any() else None
            gs = gt == "STOP"
            out["unsafe_forward_stop_rate"] = float((fwd & gs).sum() / gs.sum()) if gs.any() else None
            side_gt = np.array([l["preferred_side"] if l.get("preferred_side") in ("left", "right")
                                else {"LEFT": "left", "RIGHT": "right"}.get(l["action"], "") for l in near])
            ps = np.array([{"LEFT": "left", "RIGHT": "right"}.get(c, "") for c in cmds])
            both = (ps != "") & (side_gt != "")
            out["wrong_side_rate"] = float(((ps != side_gt) & both).sum() / both.sum()) if both.any() else None
            out["n_wrong_side"] = int(((ps != side_gt) & both).sum())
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
        f"危险前进率（unsafe-forward）：{f(m.get('unsafe_forward_rate'))}（标注要求 STOP/绕行时仍输出 FORWARD；仅 STOP 标注："
        f"{f(m.get('unsafe_forward_stop_rate'))}）",
        f"选错侧率（wrong-side）：{f(m.get('wrong_side_rate'))}（输出 LEFT/RIGHT 与标注的 preferred_side 相反）",
        f"左右来回切换（oscillation）：{m.get('side_reversals', 0)} 次（{m.get('oscillation_per_min', 0):.1f} 次/分钟）",
        f"延迟：p50={f(m.get('latency_ms_p50'), 1)} ms，p95={f(m.get('latency_ms_p95'), 1)} ms，"
        f"均值={f(m.get('latency_ms_mean'), 1)} ms",
    ])
