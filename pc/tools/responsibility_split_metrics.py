"""Rebuilt measurement for the Issue #18 responsibility split (pure functions).

The old PR #17 report measured "false STOP" against *any* GT ground danger frame, which counts
stops where an object or vegetation sat in the path as false even though blocking may be correct.
Issue #18 replaces that with separately-reported metrics, each with numerator, denominator and a
per-session grouping, and marks metrics ``NOT_EVALUABLE`` when the data cannot decide:

1. ``curb_road_miss``  -- GT curb / road surface inside the body swept path while the system
   emitted forward guidance (conservative view); split into any-forward-guidance / STRAIGHT-only /
   turn-guidance-not-evaluable (a single frame cannot decide whether a turn avoids the surface).
2. ``occupancy_miss``  -- GT 3D obstacle-class occupancy inside the body swept path while the
   system emitted forward guidance (same three views).
3. ``true_false_stop`` -- STOP only on a *confirmed clear path* (GT plane valid, GT swept path
   fully observed by walkable ground, no GT danger of any kind, no unlabelled GT ground).
4. ``guidance_coverage`` -- frames with usable forward guidance, over all frames and over
   confirmed-clear frames.
5. ``unknown_share``   -- UNKNOWN state share (plus the other state shares for context).
6. ``decision_flips`` and ``latency_to_first_hazard_warning`` -- per-session consecutive-frame
   flips and frames/ms from a GT hazard onset to the first non-forward warning.

Every metric is a count over frame rows plus a denominator.  This module has no torch, dataset or
model dependency so the definitions can be unit-tested directly.

Evidence level of any number produced from these functions: DESKTOP_REAL_DATA or
DESKTOP_SIMULATION depending on the caller; never device-verified, never a safety claim.
"""
from __future__ import annotations

import math
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

GUIDANCE_COMMANDS: Tuple[str, ...] = ("STRAIGHT", "LEFT", "RIGHT")
TURN_COMMANDS: Tuple[str, ...] = ("LEFT", "RIGHT")
WARNING_COMMANDS: Tuple[str, ...] = ("STOP", "OBSERVE", "UNKNOWN")

#: GT event threshold inherited from PR #15/#17 (measurement convention, NOT an algorithm rule).
MIN_EVENT_POINTS = 5

#: A metric with fewer than this many denominator frames is still reported but flagged
#: ``low_sample`` so it is never read as a strong result.
LOW_SAMPLE_MIN = 10

#: Latency horizon: 10 processed frames (~3.3 s at 0.33 s/frame on SANPO-Real).
HORIZON_FRAMES = 10

#: The GT hazard views that get a miss / blocking-recall pair.
GT_DANGER_VIEWS: Tuple[Tuple[str, str], ...] = (
    ("road", "gt_road_in_path"),
    ("curb", "gt_curb_in_path"),
    ("ground", "danger_ground"),
    ("occupancy", "gt_occupancy_in_path"),
    ("any", "danger_any"),
)


def command_flags(command: str, safety_state: str) -> Dict[str, bool]:
    """Boolean flags that depend only on the system output symbol."""
    return {
        "forward_guidance": command in GUIDANCE_COMMANDS,
        "warning": command in WARNING_COMMANDS,
        "is_stop": command == "STOP",
        "is_unknown": safety_state == "UNKNOWN",
        "is_observe": safety_state == "OBSERVE",
        "is_blocked": safety_state == "BLOCKED",
        "is_clear": safety_state == "CLEAR",
    }


def add_row_flags(row: Dict[str, object]) -> Dict[str, object]:
    """Add every derived system/GT flag to one per-frame row (mutates and returns the row)."""
    command = str(row["effective_command"])
    state = str(row["safety_state"])
    row.update(command_flags(command, state))
    for view, gt_key in GT_DANGER_VIEWS:
        danger = bool(row.get(gt_key, False))
        row[f"{view}_danger"] = danger
        row[f"{view}_in_path"] = danger
        row[f"{view}_miss"] = bool(danger and row["forward_guidance"])
        row[f"{view}_miss_straight"] = bool(danger and command == "STRAIGHT")
        row[f"{view}_turn_guidance_unevaluable"] = bool(danger and command in TURN_COMMANDS)
        row[f"danger_blocked_{view}"] = bool(danger and row["warning"])
    confirmed_clear = bool(row.get("confirmed_clear", False))
    weak_clear = bool(row.get("clear_weak", False))
    row["true_false_stop"] = bool(confirmed_clear and command == "STOP")
    row["unnecessary_observe"] = bool(confirmed_clear and state == "OBSERVE")
    row["unknown_on_confirmed_clear"] = bool(confirmed_clear and state == "UNKNOWN")
    row["guidance_on_confirmed_clear"] = bool(confirmed_clear and row["forward_guidance"])
    row["false_stop_on_weak_clear"] = bool(weak_clear and command == "STOP")
    return row


def _rate(num: Optional[int], den: Optional[int]) -> Optional[float]:
    if num is None or den is None or den <= 0:
        return None
    return float(num) / float(den)


def metric(numerator: Iterable[Dict[str, object]], denominator: Iterable[Dict[str, object]],
           num_fn: Callable[[Dict[str, object]], bool],
           den_fn: Callable[[Dict[str, object]], bool],
           note: str = "") -> Dict[str, object]:
    """One numerator/denominator metric with an explicit evaluability status."""
    den_rows = [r for r in denominator if den_fn(r)]
    den_ids = {id(r) for r in den_rows}
    num_rows = [r for r in numerator if id(r) in den_ids and num_fn(r)]
    n, d = len(num_rows), len(den_rows)
    status = "EVALUABLE" if d > 0 else "NOT_EVALUABLE"
    return {"numerator": n, "denominator": d, "rate": _rate(n, d), "status": status,
            "low_sample": bool(0 < d < LOW_SAMPLE_MIN), "note": note}


def value_metric(value: Optional[float], denominator: int, unit: str,
                 note: str = "") -> Dict[str, object]:
    """A scalar (e.g. a latency percentile) with its event denominator and status."""
    status = "EVALUABLE" if (denominator > 0 and value is not None
                             and math.isfinite(float(value))) else "NOT_EVALUABLE"
    return {"numerator": None, "denominator": int(denominator),
            "rate": None if value is None else float(value), "value": None if value is None
            else float(value), "unit": unit, "status": status,
            "low_sample": bool(0 < denominator < LOW_SAMPLE_MIN), "note": note}


def _rows_by_session(rows: Sequence[Dict[str, object]]) -> Dict[str, List[Dict[str, object]]]:
    out: Dict[str, List[Dict[str, object]]] = {}
    for row in rows:
        out.setdefault(str(row["session"]), []).append(row)
    for session_rows in out.values():
        session_rows.sort(key=lambda r: int(r["frame"]))
    return out


def _flip_metrics(rows: Sequence[Dict[str, object]],
                  dt_by_session: Dict[str, float]) -> Dict[str, object]:
    transitions = flips = 0
    duration_s = 0.0
    pairs: Dict[str, int] = {}
    for sid, session_rows in _rows_by_session(rows).items():
        dt = float(dt_by_session.get(sid, 1.0 / 3.0))
        for prev, cur in zip(session_rows[:-1], session_rows[1:]):
            transitions += 1
            duration_s += dt
            if prev["effective_command"] != cur["effective_command"]:
                flips += 1
                pair = f"{prev['effective_command']}->{cur['effective_command']}"
                pairs[pair] = pairs.get(pair, 0) + 1
    flips_per_min = (flips / duration_s * 60.0) if duration_s > 0 else None
    return {
        "flips": {"numerator": flips, "denominator": transitions,
                  "rate": _rate(flips, transitions),
                  "status": "EVALUABLE" if transitions > 0 else "NOT_EVALUABLE",
                  "low_sample": False, "note": "consecutive-frame effective-command changes",
                  "pairs": pairs},
        "flips_per_minute": value_metric(flips_per_min, transitions, "flips/min",
                                         note="per minute of processed frames"),
    }


def _latency_metrics(rows: Sequence[Dict[str, object]],
                     dt_by_session: Dict[str, float],
                     horizon_frames: int = HORIZON_FRAMES) -> Dict[str, object]:
    latencies: List[float] = []
    latencies_ms: List[float] = []
    events = warned = warned_at_onset = 0
    per_session: Dict[str, int] = {}
    for sid, session_rows in _rows_by_session(rows).items():
        dt = float(dt_by_session.get(sid, 1.0 / 3.0))
        in_event = False
        for idx, row in enumerate(session_rows):
            hazard = bool(row.get("gt_hazard_event", False))
            if hazard and not in_event:
                events += 1
                per_session[sid] = per_session.get(sid, 0) + 1
                found: Optional[int] = None
                for j in range(idx, min(idx + horizon_frames, len(session_rows))):
                    if bool(session_rows[j].get("warning", False)):
                        found = j - idx
                        break
                if found is not None:
                    warned += 1
                    if found == 0:
                        warned_at_onset += 1
                    latencies.append(float(found))
                    latencies_ms.append(float(found) * dt * 1000.0)
            in_event = hazard

    def pct(values: Sequence[float], q: float) -> Optional[float]:
        if not values:
            return None
        arr = sorted(values)
        rank = (len(arr) - 1) * q
        lo, hi = int(math.floor(rank)), int(math.ceil(rank))
        if lo == hi:
            return float(arr[lo])
        return float(arr[lo] + (arr[hi] - arr[lo]) * (rank - lo))

    return {
        "hazard_onset_events": {**value_metric(None, events, "events",
                                               note="GT hazard onsets (confirm->hazard "
                                                    "transitions)"),
                                "value": events, "status": "EVALUABLE" if events
                                else "NOT_EVALUABLE", "pairs": per_session},
        "warning_within_horizon": {"numerator": warned, "denominator": events,
                                   "rate": _rate(warned, events),
                                   "status": "EVALUABLE" if events else "NOT_EVALUABLE",
                                   "low_sample": bool(0 < events < LOW_SAMPLE_MIN),
                                   "note": f"first non-forward warning within {horizon_frames} "
                                           f"frames of onset"},
        "warning_at_onset": {"numerator": warned_at_onset, "denominator": events,
                             "rate": _rate(warned_at_onset, events),
                             "status": "EVALUABLE" if events else "NOT_EVALUABLE",
                             "low_sample": False, "note": "warning already on the onset frame"},
        "latency_p50_frames": value_metric(pct(latencies, 0.50), len(latencies), "frames",
                                           note="latency to first hazard warning (detected)"),
        "latency_p95_frames": value_metric(pct(latencies, 0.95), len(latencies), "frames",
                                           note="latency to first hazard warning (detected)"),
        "latency_p50_ms": value_metric(pct(latencies_ms, 0.50), len(latencies_ms), "ms",
                                       note="desktop frame spacing, not phone latency"),
        "latency_p95_ms": value_metric(pct(latencies_ms, 0.95), len(latencies_ms), "ms",
                                       note="desktop frame spacing, not phone latency"),
    }


#: (metric key, numerator predicate, denominator predicate, note)
_RATIO_METRICS: Tuple[Tuple[str, Callable, Callable, str], ...] = (
    # 1. curb / road danger miss -------------------------------------------------
    ("road_miss", lambda r: r["road_miss"], lambda r: r["road_in_path"],
     "GT road surface in body sweep -> forward guidance (conservative view)"),
    ("road_miss_straight", lambda r: r["road_miss_straight"], lambda r: r["road_in_path"],
     "GT road in body sweep -> STRAIGHT guidance (definite miss)"),
    ("road_turn_guidance_not_evaluable",
     lambda r: r["road_turn_guidance_unevaluable"], lambda r: r["road_in_path"],
     "GT road in body sweep -> LEFT/RIGHT guidance; one frame cannot decide if the turn avoids it"),
    ("road_danger_blocked_recall", lambda r: r["danger_blocked_road"],
     lambda r: r["road_in_path"], "GT road in body sweep NOT given forward guidance"),
    ("curb_miss", lambda r: r["curb_miss"], lambda r: r["curb_in_path"],
     "GT curb in body sweep -> forward guidance (conservative view)"),
    ("curb_miss_straight", lambda r: r["curb_miss_straight"], lambda r: r["curb_in_path"],
     "GT curb in body sweep -> STRAIGHT guidance (definite miss)"),
    ("curb_turn_guidance_not_evaluable",
     lambda r: r["curb_turn_guidance_unevaluable"], lambda r: r["curb_in_path"],
     "GT curb in body sweep -> LEFT/RIGHT guidance; single frame cannot decide"),
    ("curb_danger_blocked_recall", lambda r: r["danger_blocked_curb"],
     lambda r: r["curb_in_path"], "GT curb in body sweep NOT given forward guidance"),
    ("ground_miss", lambda r: r["ground_miss"], lambda r: r["ground_danger"],
     "GT non-walkable ground in body sweep -> forward guidance (PR #15-class view)"),
    ("ground_miss_straight", lambda r: r["ground_miss_straight"], lambda r: r["ground_danger"],
     "GT non-walkable ground in body sweep -> STRAIGHT guidance"),
    ("ground_danger_blocked_recall", lambda r: r["danger_blocked_ground"],
     lambda r: r["ground_danger"], "GT non-walkable ground in body sweep NOT forward-guided"),
    # 2. 3D occupancy danger miss ------------------------------------------------
    ("occupancy_miss", lambda r: r["occupancy_miss"], lambda r: r["occupancy_in_path"],
     "GT 3D object occupancy in body sweep -> forward guidance (conservative view)"),
    ("occupancy_miss_straight", lambda r: r["occupancy_miss_straight"],
     lambda r: r["occupancy_in_path"],
     "GT 3D occupancy in body sweep -> STRAIGHT guidance (definite miss)"),
    ("occupancy_turn_guidance_not_evaluable",
     lambda r: r["occupancy_turn_guidance_unevaluable"], lambda r: r["occupancy_in_path"],
     "GT 3D occupancy in body sweep -> LEFT/RIGHT guidance; single frame cannot decide"),
    ("occupancy_blocked_recall", lambda r: r["danger_blocked_occupancy"],
     lambda r: r["occupancy_in_path"], "GT 3D occupancy in body sweep NOT forward-guided"),
    # any-danger secondary view --------------------------------------------------
    ("any_miss", lambda r: r["any_miss"], lambda r: r["danger_any"],
     "any GT danger in body sweep -> forward guidance (secondary, not one-sided)"),
    ("any_danger_blocked_recall", lambda r: r["danger_blocked_any"], lambda r: r["danger_any"],
     "any GT danger in body sweep NOT forward-guided"),
    # 3. true false stop ---------------------------------------------------------
    ("true_false_stop", lambda r: r["true_false_stop"], lambda r: r["confirmed_clear"],
     "STOP on a GT confirmed clear path"),
    ("unnecessary_observe", lambda r: r["unnecessary_observe"], lambda r: r["confirmed_clear"],
     "OBSERVE on a GT confirmed clear path (hold without a confirmed obstacle)"),
    ("unknown_on_confirmed_clear", lambda r: r["unknown_on_confirmed_clear"],
     lambda r: r["confirmed_clear"], "UNKNOWN on a GT confirmed clear path"),
    ("guidance_on_confirmed_clear", lambda r: r["guidance_on_confirmed_clear"],
     lambda r: r["confirmed_clear"], "usable forward guidance on a GT confirmed clear path"),
    ("false_stop_on_weak_clear", lambda r: r["false_stop_on_weak_clear"],
     lambda r: r["clear_weak"],
     "STOP on a weaker clear view (no GT danger + >=5 walkable points in body sweep)"),
    # 4. guidance coverage -------------------------------------------------------
    ("guidance_coverage_all", lambda r: r["forward_guidance"], lambda r: True,
     "frames with usable forward guidance / all frames"),
    ("guidance_coverage_non_danger", lambda r: r["forward_guidance"] and not r["danger_any"],
     lambda r: not r["danger_any"], "usable guidance / frames without any GT danger"),
    # 5. state shares ------------------------------------------------------------
    ("unknown_share", lambda r: r["is_unknown"], lambda r: True, "UNKNOWN state frames / all"),
    ("observe_share", lambda r: r["is_observe"], lambda r: True, "OBSERVE state frames / all"),
    ("blocked_share", lambda r: r["is_blocked"], lambda r: True, "BLOCKED state frames / all"),
    ("clear_share", lambda r: r["is_clear"], lambda r: True, "CLEAR state frames / all"),
    ("stop_share", lambda r: r["is_stop"], lambda r: True, "STOP command frames / all"),
)


def summarize_rows(rows: Sequence[Dict[str, object]],
                   dt_by_session: Dict[str, float],
                   horizon_frames: int = HORIZON_FRAMES) -> Dict[str, object]:
    """Full metric block (counts + ratios + flips + latency) for one arm's rows."""
    rows = [add_row_flags(dict(r)) if "forward_guidance" not in r else r for r in rows]
    metrics: Dict[str, object] = {}
    for key, num_fn, den_fn, note in _RATIO_METRICS:
        metrics[key] = metric(rows, rows, num_fn, den_fn, note)
    metrics.update(_flip_metrics(rows, dt_by_session))
    metrics.update(_latency_metrics(rows, dt_by_session, horizon_frames))
    return {"frames": len(rows), "metrics": metrics,
            "hazard_views": {
                "road_in_path_frames": sum(1 for r in rows if r["road_in_path"]),
                "curb_in_path_frames": sum(1 for r in rows if r["curb_in_path"]),
                "ground_danger_frames": sum(1 for r in rows if r["ground_danger"]),
                "occupancy_in_path_frames": sum(1 for r in rows if r["occupancy_in_path"]),
                "any_danger_frames": sum(1 for r in rows if r["danger_any"]),
                "confirmed_clear_frames": sum(1 for r in rows if r["confirmed_clear"]),
                "clear_weak_frames": sum(1 for r in rows if r["clear_weak"]),
            }}


def summarize(all_rows: Sequence[Dict[str, object]], conditions: Sequence[str],
              arms: Sequence[str], dt_by_session: Dict[str, float]) -> Dict[str, object]:
    """Per (condition, arm) summary plus per-session groupings."""
    out: Dict[str, object] = {}
    for condition in conditions:
        for arm in arms:
            rows = [r for r in all_rows
                    if r["condition"] == condition and r["arm"] == arm]
            if not rows:
                continue
            block = summarize_rows(rows, dt_by_session)
            block["per_session"] = {
                sid: summarize_rows([r for r in rows if r["session"] == sid], dt_by_session)
                for sid in sorted({str(r["session"]) for r in rows})}
            out[f"{condition}/{arm}"] = block
    return out


def metrics_csv_rows(summary: Dict[str, object],
                     conditions: Sequence[str], arms: Sequence[str]) -> List[Dict[str, object]]:
    """Tidy rows: one row per (condition, arm, scope, metric)."""
    rows: List[Dict[str, object]] = []
    for key, block in summary.items():
        condition, arm = key.split("/", 1)
        scopes: List[Tuple[str, Dict[str, object]]] = [("all", block)]
        for sid, session_block in block.get("per_session", {}).items():
            scopes.append((sid, session_block))
        for scope, scope_block in scopes:
            for metric_key, value in scope_block["metrics"].items():
                if not isinstance(value, dict) or "status" not in value:
                    continue
                rows.append({
                    "condition": condition, "arm": arm, "scope": scope,
                    "metric": metric_key,
                    "numerator": value.get("numerator"),
                    "denominator": value.get("denominator"),
                    "rate": value.get("rate"),
                    "value": value.get("value"),
                    "unit": value.get("unit", ""),
                    "status": value.get("status"),
                    "low_sample": bool(value.get("low_sample", False)),
                    "note": value.get("note", ""),
                })
    return rows


def _pct(value: Optional[float]) -> str:
    return "n/a" if value is None else f"{100.0 * value:.1f} %"


def _ratio(num, den) -> str:
    return f"{num}/{den}" if den is not None else f"{num}/n/a"


def _metric_cell(value: Dict[str, object]) -> str:
    status = str(value.get("status"))
    if status == "NOT_EVALUABLE":
        return "**NOT_EVALUABLE**"
    low = " (low n)" if value.get("low_sample") else ""
    if value.get("denominator") is not None and value.get("numerator") is not None:
        return f"{_ratio(value.get('numerator'), value.get('denominator'))} ({_pct(value.get('rate'))}){low}"
    unit = value.get("unit") or ""
    return f"{value.get('value')} {unit}{low}".strip()


def render_summary_md(summary: Dict[str, object], meta: Dict[str, object],
                      conditions: Sequence[str], arms: Sequence[str]) -> str:
    """Markdown report body: one safety/usability table per condition."""
    lines: List[str] = []
    lines.append("# Responsibility split A/B/C — rebuilt measurement\n")
    lines.append(f"Frames: {meta.get('frames')} across sessions "
                 f"{', '.join(f'{k} ({v})' for k, v in meta.get('sessions', {}).items())}.")
    lines.append(f"Arms: A = {meta.get('arm_description', {}).get('A')}; "
                 f"B = {meta.get('arm_description', {}).get('B')}; "
                 f"C = {meta.get('arm_description', {}).get('C')}.")
    lines.append("")
    lines.append("Evidence level: **DESKTOP_REAL_DATA** (offline SANPO-Real analysis). "
                 "Not device-verified, not user-tested, no safety claim.")
    lines.append("")
    lines.append("`NOT_EVALUABLE` = the GT data cannot decide that metric for this arm/scope. "
                 "`low n` = denominator below the small-sample floor. A LEFT/RIGHT guidance "
                 "while a GT danger lies in the current body sweep is reported as "
                 "*not-evaluable* rather than counted as a miss: a single frame cannot establish "
                 "whether the guided turn avoids it (the conservative any-guidance view is kept "
                 "next to it).")
    lines.append("")
    metric_rows = [
        ("curb/road danger miss", ["road_miss", "road_miss_straight",
                                   "road_turn_guidance_not_evaluable",
                                   "curb_miss", "curb_miss_straight",
                                   "curb_turn_guidance_not_evaluable", "ground_miss",
                                   "ground_miss_straight"]),
        ("3D occupancy danger miss", ["occupancy_miss", "occupancy_miss_straight",
                                      "occupancy_turn_guidance_not_evaluable", "any_miss"]),
        ("true false-stop (confirmed clear path)", ["true_false_stop", "unnecessary_observe",
                                                    "unknown_on_confirmed_clear",
                                                    "guidance_on_confirmed_clear",
                                                    "false_stop_on_weak_clear"]),
        ("guidance coverage", ["guidance_coverage_all", "guidance_coverage_non_danger"]),
        ("UNKNOWN / state shares", ["unknown_share", "observe_share", "blocked_share",
                                    "clear_share", "stop_share"]),
        ("decision flips", ["flips", "flips_per_minute"]),
        ("latency to first hazard warning", ["hazard_onset_events", "warning_within_horizon",
                                             "warning_at_onset", "latency_p50_frames",
                                             "latency_p95_frames", "latency_p50_ms",
                                             "latency_p95_ms"]),
    ]
    for condition in conditions:
        keys = [f"{condition}/{arm}" for arm in arms if f"{condition}/{arm}" in summary]
        if not keys:
            continue
        lines.append(f"## Condition `{condition}`\n")
        header = "| metric | " + " | ".join(f"arm {arm}" for arm in arms) + " |"
        lines.append(header)
        lines.append("|---|" + "---:|" * len(arms))
        for group, metric_keys in metric_rows:
            for metric_key in metric_keys:
                cells = []
                for arm in arms:
                    block = summary.get(f"{condition}/{arm}")
                    if block is None:
                        cells.append("n/a")
                        continue
                    value = block["metrics"].get(metric_key)
                    cells.append(_metric_cell(value) if value else "n/a")
                lines.append(f"| {group}: `{metric_key}` | " + " | ".join(cells) + " |")
        lines.append("")
        lines.append(f"GT counts (`{condition}`): " + ", ".join(
            f"{k}={v}" for k, v in (summary[f"{condition}/{arms[0]}"]["hazard_views"].items())))
        lines.append("")
        # per-session tables for the headline metrics
        lines.append(f"### Per-session grouping (`{condition}`)\n")
        headline = ["road_miss", "curb_miss", "occupancy_miss", "true_false_stop",
                    "guidance_coverage_all", "unknown_share", "flips",
                    "warning_within_horizon"]
        sessions = sorted({sid for key in keys
                           for sid in summary[key].get("per_session", {})})
        lines.append("| session | arm | " + " | ".join(f"`{m}`" for m in headline) + " |")
        lines.append("|---|---|" + "---:|" * len(headline))
        for sid in sessions:
            for arm in arms:
                block = summary.get(f"{condition}/{arm}")
                if block is None:
                    continue
                session_block = block.get("per_session", {}).get(sid)
                if session_block is None:
                    continue
                cells = []
                for metric_key in headline:
                    value = session_block["metrics"].get(metric_key)
                    cells.append(_metric_cell(value) if value else "n/a")
                lines.append(f"| {sid} | {arm} | " + " | ".join(cells) + " |")
        lines.append("")
    return "\n".join(lines) + "\n"
