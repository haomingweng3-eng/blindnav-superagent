#!/usr/bin/env python3
"""Report/analysis tool for the Path Keeper negative-evidence fusion ticket (Issue #16).

Reads the **frozen** output of ``pc/tools/corridor_evidence_eval.py`` (an eval output
directory containing ``per_frame.csv`` + ``summary.json``) and writes a pull-request-ready
evidence report — no model inference, no torch, no data edits:

* ``report_tables.md``  — per-condition A/B/C tables (safety and usability/regression),
  per-session breakdown, and the B-vs-A / C-vs-B / C-vs-A delta paragraph generated from
  the numbers only.
* ``report_tables.csv`` — the same cells as tidy rows ``condition, scope, arm, metric,
  numerator, denominator, rate, definition`` (so every percentage can be recomputed).
* ``report_body.md``    — PR skeleton with the required sections (Problem / Evidence
  Before / Change / Evidence After / Regression / Evidence Level / Remaining Risks),
  filled with the measured numbers; human judgement is left as ``TODO(Lead)`` markers.
* with ``--save-failures`` — >= 5 remaining failing frames of the final arms (B/C in both
  input conditions): raw RGB ``f<NN>_<kind>_<session8>_<frame>_raw.jpg``, an annotated
  overlay ``..._overlay.jpg`` (GT walkable tint, GT hazard/non-walkable tint, the corridor
  decision swept body outline, per-frame numbers + mechanical layer label), and
  ``failures.json``.  The ``f<NN>`` ordinal keeps the pairs unique when the same
  (kind, session, frame) fails in both input conditions.

The report tool never feeds the PR #15 23-frame set into any algorithm.  It is a reporting
label only: the fixed set is recovered from the eval output (annotated-condition arm-A rows
with ``pr15_event`` and walkable guidance) and applied as an evaluation slice to every
condition/arm.

Metric definitions (all with explicit numerator/denominator; fixed in the frozen spec, no
tuned entries):

* **two danger denominators (amendment v1.1; both from oracle geometry + raw SANPO ids in
  the decision swept body, 1.5-6 m, |lateral| <= 0.4 m):** the **primary**
  ``danger_ground`` = GT excluded/non-walkable GROUND points, raw ids {1 road, 2 curb,
  16 water, 18 inaccessible, 19 railway, 30 terrain} (>= 5 points); the **secondary**
  ``danger_any`` = danger_ground OR GT object occupancy (raw ids {4,7,8,9,10,11,12,13,14,
  15,20,21,22,23,24,25,26,28} at 0.15 < height <= 2.0 m) OR GT non-walkable-class ground
  (>= 5 points).  Both denominators are always shown side by side.
* **false-free (primary)** — ``danger_ground`` and hint in (STRAIGHT, LEFT, RIGHT) /
  ``danger_ground`` frames; **false-free (any)** uses ``danger_any`` / ``danger_any``
  frames.  The *PR #15 gate* variant restricts the primary numerator to PR #15
  road/curb-in-path event frames and uses those as denominator.
* **false-stop (primary)** — hint STOP, no ``danger_ground``, and >= 5 GT walkable swept
  points / frames with no ``danger_ground`` and >= 5 GT walkable swept points;
  **false-stop (any)** uses ``danger_any`` on both sides.
* **UNKNOWN** — hint UNKNOWN / all frames.
* **coverage >= min** — tracker ``coverage`` >= ``ShadowWalkConfig.min_coverage`` (0.5) /
  all frames; mean coverage is reported next to it.
* **guidance coverage** — hint in (STRAIGHT, LEFT, RIGHT) / all frames.
* **action flips vs A** — same (condition, session, frame) differs in hint from arm A /
  frames joined with arm A; pairs ``A->X`` are listed.
* **danger blocking recall** — (STOP + UNKNOWN) on GT danger frames / GT danger frames,
  reported for BOTH denominators; STOP and UNKNOWN are split out in the cell.
* **23-frame remain table** — fixed PR #15 set, denominator **23**, counts of
  walkable / STOP / UNKNOWN per condition/arm.
* **road/curb-in-path ignored** — PR #15 definitions: >= 5 GT road (1.5-6 m) or curb
  (2.5-6 m) ground points in the map-frame swept body (|lateral| <= 0.6 m) while the output
  is STRAIGHT; denominator = frames where that PR #15 event is present.

Failure selection is deterministic and data-driven: one representative per
(condition, session, frame), frame-level severity kinds (``false_free`` > ``false_free_any``
> ``false_stop`` > ``unknown_when_gt_walkable``, so both danger dimensions are illustrated),
then a round-robin over (condition, severity kind) buckets that always picks the least-used
session first (ties broken by session name / frame).
The overlay GT tint and the swept-body outline use the GT depth + GT ground plane (the
oracle geometry, diagnostic only); they are never a system input.

Layer labels in the failure overlays are mechanical, pipeline-ordered:

* ``geometry``    — calibration not ok / no metric depth, or GT walkable exists but not
  enough walkable points project (``n_points < min_points``);
* ``temporal``    — arm C only: the blocking evidence is present but still unconfirmed
  (``hazard_confirming`` / ``non_walkable_confirming`` / a positive streak below the
  temporal confirmation frame count);
* ``segmentation``— production condition only, GT danger in the swept body but the v5 mask
  supplies no negative-evidence points (annotated frames can never be segmentation: the
  annotation is right by construction);
* ``decision``    — negative evidence is projected in the swept body but is below the
  support/gating threshold (or the decision still emits walkable guidance / STOP without
  GT danger), or (annotated/oracle arm only) GT danger exists but the binned swept-body
  counters see none — e.g. GT danger only in the 5.5-6.0 m tail of the 1.5-6 m rule, which
  the inherited legacy integer-bin convention (``n_bins = round(4.5/1.0) = 4``) does not
  bin: the corridor decision layer's representation, not the mask and not the geometry.

CLI (from ``pc/``)::

    HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 .venv/bin/python tools/corridor_evidence_report.py \
        --out-dir out/corridor_evidence_full --save-failures

``--out`` defaults to the eval output directory; pass another directory (e.g. under /tmp)
to keep the official assets untouched.  ``--data-root`` is only needed for failure overlays
(falls back to ``summary.json`` meta ``data_root``).

Evidence level: **DESKTOP_REAL_DATA**.  Offline desktop analysis on SANPO-Real; not
device-verified, not user-tested, no safety claim.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

import cv2
import numpy as np

# Make ``blindnav_pc`` / ``eval`` / sibling tools importable when run as a script.
_HERE = os.path.dirname(os.path.abspath(__file__))
_PC_ROOT = os.path.abspath(os.path.join(_HERE, ".."))
for _p in (_HERE, _PC_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from blindnav_pc.bev import depth_to_level_points, level_frame  # noqa: E402

# ---------------------------------------------------------------------------
# Constants.  These mirror the FROZEN contract (spec §2, §4) and were not tuned here.
# The string command literals are deliberately not imported from ``shadowwalk`` so this
# reporting tool stays importable while the decision layer is edited in parallel; the
# values are the same as ``blindnav_pc.shadowwalk.LEFT/RIGHT/STRAIGHT/STOP/UNKNOWN``.
# ---------------------------------------------------------------------------
STRAIGHT, LEFT, RIGHT, STOP, UNKNOWN = "STRAIGHT", "LEFT", "RIGHT", "STOP", "UNKNOWN"
COMMANDS = (STRAIGHT, LEFT, RIGHT, STOP, UNKNOWN)
GUIDANCE_COMMANDS = (STRAIGHT, LEFT, RIGHT)

CONDITION_ORDER = ("annotated", "production")
ARM_ORDER = ("A", "B", "C")
FINAL_ARMS = ("B", "C")                 # "final arms" per spec §5.4
KIND_ORDER = ("false_free", "false_free_any", "false_stop", "unknown_when_gt_walkable")
KIND_LABEL = {
    "false_free": "false-free on GT excluded-ground danger (primary safety number)",
    "false_free_any": "false-free on GT any-negative danger (incl. object/vegetation occupancy)",
    "false_stop": "false-stop with no GT excluded-ground danger (primary)",
    "unknown_when_gt_walkable": "UNKNOWN while GT walkable ground is in the swept body",
}
LAYERS = ("segmentation", "geometry", "decision", "temporal")

MIN_EVENT_POINTS = 5          # PR #15 MIN_EVENT_POINTS: >=5 points make a road/curb event
MIN_COVERAGE = 0.5            # ShadowWalkConfig.min_coverage (frozen default)
MIN_POINTS = 50               # ShadowWalkConfig.min_points (frozen default)
NEAR_M, FAR_M = 1.5, 6.0      # ShadowWalkConfig.near_m / far_m (frozen default)
BIN_M = 1.0                   # ShadowWalkConfig.bin_m (frozen default)
PIXEL_STRIDE = 4              # ShadowWalkConfig.pixel_stride (frozen default)
MAX_HEIGHT_M = 0.15           # ShadowWalkConfig.max_height_m (frozen default)
SWEPT_HALF_WIDTH_M = 0.4      # cfg.swept_half_width_m = body_width_m/2 + body_margin_m
                              # = 0.5/2 + 0.15 (a priori design constants, not tuned)
OBSTACLE_MIN_M, OBSTACLE_MAX_M = 0.15, 2.0

# GT danger ground ids and obstacle ids exactly as fixed in the spec (amendment v1.1):
#   primary ``danger_ground``  = GT excluded/non-walkable ground ids below,
#   secondary ``danger_any``   = primary OR obstacle ids at 0.15 < height <= 2.0 m OR
#                                GT non-walkable-class ground.
# Used for the metric labels and for the cosmetic failure-overlay GT tint.
DANGER_GROUND_IDS = (1, 2, 16, 18, 19, 30)          # road, curb, water, inaccessible, rail, terrain
OBSTACLE_IDS = (4, 7, 8, 9, 10, 11, 12, 13, 14, 15, 20, 21, 22, 23, 24, 25, 26, 28)

EVIDENCE_FIELDS = ("walkable_swept_points", "hazard_points", "non_walkable_points",
                   "unknown_points", "hazard_bins", "non_walkable_bins", "unknown_bins",
                   "temporal_streak")

# Column aliases: keep the reader tolerant to the eval module's exact per-frame column
# names (the spec freezes summary.json keys but leaves per-row names to the eval tool).
ALIASES: Dict[str, Tuple[str, ...]] = {
    "condition": ("condition", "input_condition"),
    "arm": ("arm",),
    "session": ("session", "session_id", "session_name"),
    "frame": ("frame", "frame_index", "frame_id"),
    "hint": ("hint", "command", "output"),
    "reason": ("reason", "decision_reason"),
    "coverage": ("coverage", "coverage_value"),
    "n_points": ("n_points", "walkable_points", "walkable_n"),
    "coverage_bins_ok": ("coverage_bins_ok", "coverage_ok"),
    "calib_ok": ("calib_ok", "calibration_ok"),
    "calib_reason": ("calib_reason", "calibration_reason"),
    "yaw_err_deg": ("yaw_err_deg", "yaw_error_deg"),
    "walkable_offset_m": ("walkable_offset_m", "band_offset_m"),
    "gt_walk_pts_swept": ("gt_walk_pts_swept", "gt_walk_pts_band", "gt_walkable_pts_swept"),
    "danger_ground": ("danger_ground", "gt_danger_ground"),
    "danger_any": ("danger_any", "gt_danger_swept", "gt_danger"),
    "gt_danger_ground_pts": ("gt_danger_ground_pts",),
    "gt_danger_any_pts": ("gt_danger_any_pts", "gt_danger_pts_swept"),
    "pr15_road_path_band": ("pr15_road_path_band", "road_path_band", "pr15_road_in_path_band"),
    "pr15_curb_path_band": ("pr15_curb_path_band", "curb_path_band", "pr15_curb_in_path_band"),
    "pr15_event": ("pr15_event", "pr15_event_frame", "pr15_path_event"),
    "pr15_23": ("pr15_23", "in_pr15_23", "pr15_23_frame"),
    "false_free": ("false_free",),
    "false_free_any": ("false_free_any",),
    "correct_block": ("correct_block", "correct_block_ground"),
    "unknown_on_danger": ("unknown_on_danger", "unknown_on_danger_ground"),
    "false_stop": ("false_stop", "false_stop_ground"),
    "false_stop_any": ("false_stop_any",),
    "unknown_when_gt_walkable": ("unknown_when_gt_walkable",),
    "walkable_guidance": ("walkable_guidance",),
    "gt_hint_A_oracle": ("gt_hint_A_oracle", "gt_hint"),
    "walkable_swept_points": ("walkable_swept_points", "walkable_pts_swept"),
    "hazard_points": ("hazard_points", "hazard_pts"),
    "non_walkable_points": ("non_walkable_points", "non_walkable_pts"),
    "unknown_points": ("unknown_points", "unknown_pts"),
    "hazard_bins": ("hazard_bins",),
    "non_walkable_bins": ("non_walkable_bins",),
    "unknown_bins": ("unknown_bins",),
    "temporal_streak": ("temporal_streak", "block_streak"),
}

#: canonical per-row keys produced by :func:`normalize_row` (documented contract).
ROW_FIELDS = (
    "condition", "arm", "session", "frame", "hint", "reason", "coverage", "n_points",
    "coverage_bins_ok", "calib_ok", "calib_reason", "yaw_err_deg", "walkable_offset_m",
    "gt_walk_pts_swept", "danger_ground", "danger_any", "gt_danger_ground_pts",
    "gt_danger_any_pts", "pr15_road_path_band", "pr15_curb_path_band", "pr15_event",
    "pr15_23", "false_free", "false_free_any", "correct_block", "unknown_on_danger",
    "false_stop", "false_stop_any", "unknown_when_gt_walkable", "walkable_guidance",
    "gt_hint_A_oracle",
) + EVIDENCE_FIELDS


# ---------------------------------------------------------------------------
# Small parsing / formatting helpers
# ---------------------------------------------------------------------------
def _first(raw: MappingLike, keys: Sequence[str]) -> Any:
    """First non-empty value among ``keys`` (column aliases), else None."""
    for k in keys:
        if k in raw:
            v = raw[k]
            if v is None:
                continue
            if isinstance(v, str) and v.strip() == "":
                continue
            return v
    return None


MappingLike = Any  # dict-like: csv.DictReader row or normalized dict


def _as_bool(value: Any, default: Optional[bool] = None) -> Optional[bool]:
    if value is None:
        return default
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    s = str(value).strip().lower()
    if s in ("1", "true", "yes", "y", "t", "ok"):
        return True
    if s in ("0", "false", "no", "n", "f", ""):
        return False
    return default


def _as_float(value: Any, default: Optional[float] = None) -> Optional[float]:
    if value is None:
        return default
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return out if math.isfinite(out) else default


def _as_int(value: Any, default: Optional[int] = None) -> Optional[int]:
    if value is None:
        return default
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _rate(num: Optional[int], den: Optional[int]) -> Optional[float]:
    if num is None or den is None or den <= 0:
        return None
    return float(num) / float(den)


def _pct(rate: Optional[float]) -> str:
    return "n/a" if rate is None else f"{100.0 * rate:.1f}%"


def _ratio(num: Optional[int], den: Optional[int]) -> str:
    return f"{num if num is not None else '?'}/{den if den is not None else '?'}"


# ---------------------------------------------------------------------------
# Row normalisation: raw CSV row -> canonical row + derived flags
# ---------------------------------------------------------------------------
def normalize_row(raw: MappingLike) -> Dict[str, Any]:
    """Normalise one ``per_frame.csv`` row to the canonical :data:`ROW_FIELDS` keys.

    Flags that are not present as columns are *derived* from the GT counts and the hint
    exactly as the frozen spec defines them (so an eval tool that writes only raw GT counts
    is still consumable).  The derived values are what every metric below uses.
    """
    row: Dict[str, Any] = {}
    for key, aliases in ALIASES.items():
        row[key] = _first(raw, aliases)

    row["condition"] = str(row["condition"]) if row["condition"] is not None else None
    row["arm"] = str(row["arm"]).strip().upper() if row["arm"] is not None else None
    row["session"] = str(row["session"]) if row["session"] is not None else None
    row["frame"] = _as_int(row["frame"])
    row["hint"] = str(row["hint"]).strip().upper() if row["hint"] is not None else None
    row["reason"] = str(row["reason"]).strip() if row["reason"] is not None else None

    row["coverage"] = _as_float(row["coverage"])
    row["n_points"] = _as_int(row["n_points"])
    row["coverage_bins_ok"] = _as_bool(row["coverage_bins_ok"])
    row["calib_ok"] = _as_bool(row["calib_ok"])
    row["calib_reason"] = str(row["calib_reason"]).strip() if row["calib_reason"] is not None else None
    row["yaw_err_deg"] = _as_float(row["yaw_err_deg"])
    row["walkable_offset_m"] = _as_float(row["walkable_offset_m"])

    row["gt_walk_pts_swept"] = _as_int(row["gt_walk_pts_swept"])
    row["gt_danger_ground_pts"] = _as_int(row["gt_danger_ground_pts"])
    row["gt_danger_any_pts"] = _as_int(row["gt_danger_any_pts"])
    row["danger_ground"] = _as_bool(row["danger_ground"])
    row["danger_any"] = _as_bool(row["danger_any"])
    row["pr15_road_path_band"] = _as_int(row["pr15_road_path_band"])
    row["pr15_curb_path_band"] = _as_int(row["pr15_curb_path_band"])
    row["pr15_event"] = _as_bool(row["pr15_event"])
    row["pr15_23"] = _as_bool(row["pr15_23"])

    explicit = {}
    for key in ("false_free", "false_free_any", "correct_block", "unknown_on_danger",
                "false_stop", "false_stop_any", "unknown_when_gt_walkable",
                "walkable_guidance"):
        explicit[key] = _as_bool(row[key])

    for key in EVIDENCE_FIELDS:
        row[key] = _as_int(row[key])

    # Amendment v1.1: two danger denominators per row.
    #   danger_ground = GT excluded/non-walkable GROUND points in the swept body (PR #15 class)
    #   danger_any    = ground danger OR GT object occupancy OR GT non-walkable-class ground
    # Bool columns win; otherwise derive from the point counts.  When only the legacy
    # ``gt_danger_swept`` column exists it is treated as the primary ground denominator and
    # the secondary dimension falls back to it (surfaced as a contract warning).
    gt_walk = row["gt_walk_pts_swept"]
    if row["danger_ground"] is None and row["gt_danger_ground_pts"] is not None:
        row["danger_ground"] = row["gt_danger_ground_pts"] >= MIN_EVENT_POINTS
    if row["danger_any"] is None and row["gt_danger_any_pts"] is not None:
        row["danger_any"] = row["gt_danger_any_pts"] >= MIN_EVENT_POINTS
    row["_danger_any_derived_from_ground"] = False
    if row["danger_any"] is None and row["danger_ground"] is not None:
        row["danger_any"] = bool(row["danger_ground"])
        row["_danger_any_derived_from_ground"] = True
    danger_ground = row["danger_ground"]
    danger_any = row["danger_any"]

    hint = row["hint"]
    walkable_guidance = explicit["walkable_guidance"]
    if walkable_guidance is None and hint is not None:
        walkable_guidance = hint in GUIDANCE_COMMANDS
    row["walkable_guidance"] = bool(walkable_guidance)

    # Derived flags use the frozen definitions; explicit columns win when present.
    def _flag(name: str, derived: Optional[bool]) -> bool:
        if explicit[name] is not None:
            return bool(explicit[name])
        return bool(derived) if derived is not None else False

    row["false_free"] = _flag(
        "false_free",
        (danger_ground is True and hint in GUIDANCE_COMMANDS) if danger_ground is not None else None)
    row["false_free_any"] = _flag(
        "false_free_any",
        (danger_any is True and hint in GUIDANCE_COMMANDS) if danger_any is not None else None)
    row["correct_block"] = _flag(
        "correct_block",
        (danger_ground is True and hint == STOP) if danger_ground is not None else None)
    row["unknown_on_danger"] = _flag(
        "unknown_on_danger",
        (danger_ground is True and hint == UNKNOWN) if danger_ground is not None else None)
    row["false_stop"] = _flag(
        "false_stop",
        (hint == STOP and danger_ground is False and gt_walk is not None
         and gt_walk >= MIN_EVENT_POINTS)
        if (danger_ground is not None and gt_walk is not None) else None)
    row["false_stop_any"] = _flag(
        "false_stop_any",
        (hint == STOP and danger_any is False and gt_walk is not None
         and gt_walk >= MIN_EVENT_POINTS)
        if (danger_any is not None and gt_walk is not None) else None)
    row["unknown_when_gt_walkable"] = _flag(
        "unknown_when_gt_walkable",
        (hint == UNKNOWN and gt_walk is not None and gt_walk >= MIN_EVENT_POINTS)
        if gt_walk is not None else None)
    row["gt_hint_A_oracle"] = str(row["gt_hint_A_oracle"]) if row["gt_hint_A_oracle"] is not None else None
    return row


def normalize_rows(raw_rows: Iterable[MappingLike]) -> List[Dict[str, Any]]:
    rows = [normalize_row(r) for r in raw_rows]
    if not rows:
        return rows
    # ``mode`` fallback: a harness that kept PR #15's column name but carries the new
    # condition values is still readable.
    for r in rows:
        if r["condition"] is None:
            raise ValueError("per_frame.csv row without a 'condition' column/value "
                             f"(session={r.get('session')}, frame={r.get('frame')})")
        if r["arm"] is None or r["hint"] is None:
            raise ValueError("per_frame.csv row without 'arm'/'hint' "
                             f"(condition={r['condition']}, session={r.get('session')})")
    return rows


def ordered_conditions(rows: Sequence[Dict[str, Any]]) -> List[str]:
    present = {r["condition"] for r in rows}
    return _ordered(present, CONDITION_ORDER)


def ordered_arms(rows: Sequence[Dict[str, Any]]) -> List[str]:
    present = {r["arm"] for r in rows}
    return _ordered(present, ARM_ORDER)


def _ordered(present: Iterable[str], preferred: Sequence[str]) -> List[str]:
    present_set = set(present)
    out = [p for p in preferred if p in present_set]
    out.extend(sorted(present_set - set(out)))
    return out


# ---------------------------------------------------------------------------
# PR #15 fixed 23-frame set (reporting label only, never an algorithm input)
# ---------------------------------------------------------------------------
def pr15_23_frame_set(rows: Sequence[Dict[str, Any]]) -> List[Tuple[str, int]]:
    """Recover the fixed PR #15 23-frame regression set from the eval rows.

    Preferred source is the per-row ``pr15_23`` label written by the eval tool.  If that
    column is absent, the set is recomputed exactly as spec §4 defines it: annotated
    condition, arm A, ``pr15_event`` and walkable guidance.  Sorted for determinism.
    """
    explicit = {(r["session"], int(r["frame"])) for r in rows
                if r.get("pr15_23") and r.get("frame") is not None}
    if explicit:
        return sorted(explicit)
    derived = {(r["session"], int(r["frame"])) for r in rows
               if r["condition"] == "annotated" and r["arm"] == "A"
               and r.get("pr15_event") and r.get("walkable_guidance")
               and r.get("frame") is not None}
    return sorted(derived)


# ---------------------------------------------------------------------------
# Metric aggregation (pure functions over normalized rows)
# ---------------------------------------------------------------------------
def _coverage_ok(row: Dict[str, Any]) -> bool:
    if row.get("coverage_bins_ok") is not None:
        return bool(row["coverage_bins_ok"])
    cov = row.get("coverage")
    return cov is not None and cov >= MIN_COVERAGE


def summarize_subset(rows: Sequence[Dict[str, Any]],
                     frame23_keys: Sequence[Tuple[str, int]] = ()) -> Dict[str, Any]:
    """All table metrics for one (condition, arm[, session]) row subset.

    Returns plain dicts with explicit ``numerator``/``denominator``/``rate`` so the report
    writer (and tests) never formats a percentage the numbers cannot reproduce.
    """
    n = len(rows)
    hint_counts = Counter(str(r["hint"]) for r in rows)
    key_set = set(frame23_keys)

    # Amendment v1.1: two danger denominators, reported side by side.
    #   danger_ground = GT excluded/non-walkable GROUND points in the swept body (PR #15 class)
    #   danger_any    = danger_ground OR GT object occupancy OR GT non-walkable-class ground
    danger_ground_rows = [r for r in rows if r["danger_ground"]]
    danger_any_rows = [r for r in rows if r["danger_any"]]
    danger_n = len(danger_ground_rows)
    danger_any_n = len(danger_any_rows)
    danger_stop = sum(1 for r in danger_ground_rows if r["hint"] == STOP)
    danger_unknown = sum(1 for r in danger_ground_rows if r["hint"] == UNKNOWN)
    danger_walk = sum(1 for r in danger_ground_rows if r["hint"] in GUIDANCE_COMMANDS)
    danger_block = danger_stop + danger_unknown
    danger_any_stop = sum(1 for r in danger_any_rows if r["hint"] == STOP)
    danger_any_unknown = sum(1 for r in danger_any_rows if r["hint"] == UNKNOWN)
    danger_any_walk = sum(1 for r in danger_any_rows if r["hint"] in GUIDANCE_COMMANDS)
    danger_any_block = danger_any_stop + danger_any_unknown

    false_free = [r for r in rows if r["false_free"]]
    false_free_any = [r for r in rows if r["false_free_any"]]
    false_stop = [r for r in rows if r["false_stop"]]
    false_stop_any = [r for r in rows if r["false_stop_any"]]
    unknown_when = [r for r in rows if r["unknown_when_gt_walkable"]]
    guidance = [r for r in rows if r["walkable_guidance"]]
    pr15_events = [r for r in rows if r["pr15_event"]]
    false_free_pr15 = [r for r in false_free if r["pr15_event"]]

    cov_ok = sum(1 for r in rows if _coverage_ok(r))
    cov_vals = [float(r["coverage"]) for r in rows if r.get("coverage") is not None]
    mean_cov = float(np.mean(cov_vals)) if cov_vals else None

    def _num_den(num: int, den: int) -> Dict[str, Any]:
        return {"numerator": int(num), "denominator": int(den), "rate": _rate(num, den)}

    f23 = _num_den(len(false_free), danger_n)
    f23["denominator_is_danger_frames"] = True
    f23["denominator_absent_no_gt_danger"] = danger_n == 0
    ff_any = _num_den(len(false_free_any), danger_any_n)
    ff_any["denominator_absent_no_gt_danger"] = danger_any_n == 0

    road_events = [r for r in rows
                   if (r["pr15_road_path_band"] or 0) >= MIN_EVENT_POINTS]
    curb_events = [r for r in rows
                   if (r["pr15_curb_path_band"] or 0) >= MIN_EVENT_POINTS]
    road_ignored = [r for r in road_events if r["hint"] == STRAIGHT]
    curb_ignored = [r for r in curb_events if r["hint"] == STRAIGHT]

    # 23-frame remain table: fixed denominator, rows matched by (session, frame).
    matched = [r for r in rows if (r["session"], r["frame"]) in key_set]
    matched_keys = {(r["session"], r["frame"]) for r in matched}
    p23_walk = sum(1 for r in matched if r["hint"] in GUIDANCE_COMMANDS)
    p23_stop = sum(1 for r in matched if r["hint"] == STOP)
    p23_unknown = sum(1 for r in matched if r["hint"] == UNKNOWN)

    evidence_totals = {
        key: float(sum((r.get(key) or 0) for r in rows)) for key in EVIDENCE_FIELDS
    }

    out: Dict[str, Any] = {
        "frames": n,
        "hint_counts": {c: int(hint_counts.get(c, 0)) for c in COMMANDS},
        "false_free": f23,
        "false_free_any": ff_any,
        "false_free_pr15_gate": _num_den(len(false_free_pr15), len(pr15_events)),
        "false_stop": _num_den(
            len(false_stop),
            sum(1 for r in rows if (not r["danger_ground"])
                and (r["gt_walk_pts_swept"] or 0) >= MIN_EVENT_POINTS)),
        "false_stop_any": _num_den(
            len(false_stop_any),
            sum(1 for r in rows if (not r["danger_any"])
                and (r["gt_walk_pts_swept"] or 0) >= MIN_EVENT_POINTS)),
        "unknown": _num_den(int(hint_counts.get(UNKNOWN, 0)), n),
        "coverage": {
            "frames_ge_min_coverage": int(cov_ok),
            "frames": n,
            "rate": _rate(cov_ok, n),
            "mean_coverage": mean_cov,
            "min_coverage": MIN_COVERAGE,
        },
        "guidance_coverage": _num_den(len(guidance), n),
        "danger_handling": {
            "danger_frames": int(danger_n),
            "stop": int(danger_stop),
            "unknown": int(danger_unknown),
            "walkable": int(danger_walk),
            "blocking_recall": _rate(danger_block, danger_n),
            "stop_rate": _rate(danger_stop, danger_n),
            "unknown_rate": _rate(danger_unknown, danger_n),
        },
        "danger_handling_any": {
            "danger_frames": int(danger_any_n),
            "stop": int(danger_any_stop),
            "unknown": int(danger_any_unknown),
            "walkable": int(danger_any_walk),
            "blocking_recall": _rate(danger_any_block, danger_any_n),
            "stop_rate": _rate(danger_any_stop, danger_any_n),
            "unknown_rate": _rate(danger_any_unknown, danger_any_n),
        },
        "pr15_23": {
            "frames": len(key_set),
            "walkable": int(p23_walk),
            "stop": int(p23_stop),
            "unknown": int(p23_unknown),
            "matched": len(matched_keys),
            "missing": len(key_set) - len(matched_keys),
        },
        "road_in_path_ignored": _num_den(len(road_ignored), len(road_events)),
        "curb_in_path_ignored": _num_den(len(curb_ignored), len(curb_events)),
        "event_frames": {
            "pr15_event": len(pr15_events),
            "pr15_road_path_band": len(road_events),
            "pr15_curb_path_band": len(curb_events),
            "unknown_when_gt_walkable": len(unknown_when),
            "correct_block": sum(1 for r in rows if r["correct_block"]),
            "unknown_on_danger": sum(1 for r in rows if r["unknown_on_danger"]),
            "danger_ground_frames": len(danger_ground_rows),
            "danger_any_frames": len(danger_any_rows),
        },
        "evidence_totals": evidence_totals,
    }
    return out


def action_flips(arm_rows: Sequence[Dict[str, Any]],
                 baseline_rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Frame-matched hint agreement with the baseline arm (A) of the same condition.

    ``numerator`` = frames whose hint differs from arm A, ``denominator`` = frames joined
    on (session, frame) with arm A.  ``pairs`` maps ``"A->X"`` to a count.
    """
    base = {(r["session"], r["frame"]): r["hint"] for r in baseline_rows}
    joined = 0
    flips = 0
    pairs: Counter = Counter()
    for r in arm_rows:
        key = (r["session"], r["frame"])
        if key not in base:
            continue
        joined += 1
        if base[key] != r["hint"]:
            flips += 1
            pairs[f"{base[key]}->{r['hint']}"] += 1
    return {
        "numerator": int(flips),
        "denominator": int(joined),
        "rate": _rate(flips, joined),
        "pairs": dict(sorted(pairs.items())),
    }


def aggregate(rows: Sequence[Dict[str, Any]],
              frame23_keys: Sequence[Tuple[str, int]] = ()) -> Dict[str, Any]:
    """Build overall + per-session metric tables from normalized rows."""
    conditions = ordered_conditions(rows)
    arms = ordered_arms(rows)
    overall: Dict[Tuple[str, str], Dict[str, Any]] = {}
    per_session: Dict[Tuple[str, str, str], Dict[str, Any]] = {}

    for cond in conditions:
        cond_rows = [r for r in rows if r["condition"] == cond]
        for arm in arms:
            sel = [r for r in cond_rows if r["arm"] == arm]
            if not sel:
                continue
            entry = summarize_subset(sel, frame23_keys)
            base = [r for r in cond_rows if r["arm"] == "A"]
            entry["action_flips"] = action_flips(sel, base) if base else {
                "numerator": 0, "denominator": 0, "rate": None, "pairs": {}}
            overall[(cond, arm)] = entry
            sessions = sorted({r["session"] for r in sel})
            for sid in sessions:
                sub = [r for r in sel if r["session"] == sid]
                s_entry = summarize_subset(sub, frame23_keys)
                s_entry["action_flips"] = (
                    action_flips(sub, [r for r in base if r["session"] == sid]) if base else
                    {"numerator": 0, "denominator": 0, "rate": None, "pairs": {}})
                per_session[(cond, arm, sid)] = s_entry

    for cond in conditions:
        arm_counts = {arm: len([r for r in rows if r["condition"] == cond and r["arm"] == arm])
                      for arm in arms}
        aligned = len({c for c in arm_counts.values() if c > 0}) <= 1
        for arm in arms:
            if (cond, arm) in overall:
                overall[(cond, arm)]["frame_counts_aligned"] = aligned

    return {
        "conditions": conditions,
        "arms": arms,
        "overall": overall,
        "per_session": per_session,
        "frame_counts_aligned": all(m.get("frame_counts_aligned", True)
                                    for m in overall.values()),
    }


# ---------------------------------------------------------------------------
# Contract checks: missing columns / summary.json cross-check
# ---------------------------------------------------------------------------
def contract_warnings(rows: Sequence[Dict[str, Any]],
                      frame23_keys: Sequence[Tuple[str, int]]) -> List[str]:
    """Warnings about missing/per-frame contract fields and the 23-frame label count."""
    warnings: List[str] = []
    if not rows:
        warnings.append("per_frame.csv has no rows")
        return warnings

    def _all_none(field: str) -> bool:
        return all(r.get(field) is None for r in rows)

    for field in ("danger_ground", "gt_walk_pts_swept"):
        if _all_none(field):
            warnings.append(f"per_frame.csv has no `{field}` column/value: false-free, "
                            "false-stop and danger metrics cannot be verified and are 0")
    if any(r.get("_danger_any_derived_from_ground") for r in rows):
        warnings.append("per_frame.csv has no `danger_any` column/value (amendment v1.1): "
                        "the secondary any-negative-danger dimension falls back to the "
                        "primary ground-danger denominator; object/vegetation occupancy is "
                        "NOT separately illustrated in this run")
    elif all(r["danger_any"] == r["danger_ground"] for r in rows):
        warnings.append("`danger_any` equals `danger_ground` on every row: no object/"
                        "vegetation-occupancy frames in this run, so the secondary "
                        "any-danger table does not add information here")
    for field in ("pr15_road_path_band", "pr15_curb_path_band"):
        if _all_none(field):
            warnings.append(f"per_frame.csv has no `{field}` column/value: PR #15 "
                            "road/curb-in-path ignored denominators are 0")
    if _all_none("pr15_event"):
        warnings.append("per_frame.csv has no `pr15_event` column/value: the PR #15 gate "
                        "denominator and the 23-frame recovery fall back to label/rows")
    evidence_missing = [f for f in EVIDENCE_FIELDS if _all_none(f)]
    if evidence_missing:
        warnings.append("per_frame.csv lacks evidence channel fields "
                        f"{evidence_missing}: failure-overlay channel counts show 0; "
                        "layer classification falls back to coarser rules")
    for field in ("calib_ok", "calib_reason", "n_points"):
        if _all_none(field):
            warnings.append(f"per_frame.csv has no `{field}` column/value: geometry/temporal "
                            "layer attribution is less specific")
    n23 = len(frame23_keys)
    if n23 != 23:
        warnings.append(
            f"PR #15 23-frame set recovered **{n23}** frames (expected 23). The set is a "
            "reporting label only; verify the eval's annotated/A pr15_event rows before "
            "quoting the 'of 23' table.")
    if len(ordered_conditions(rows)) < 2:
        warnings.append(f"only {ordered_conditions(rows)} condition(s) present; the frozen "
                        "contract expects annotated AND production")
    for arm in FINAL_ARMS:
        if not any(r["arm"] == arm for r in rows):
            warnings.append(f"final arm {arm} has no rows")
    return warnings


def _cmp_value(warnings: List[str], label: str, ours: Any, theirs: Any,
               is_float: bool = False) -> None:
    if theirs is None or ours is None:
        return
    if is_float:
        if abs(float(ours) - float(theirs)) > 2e-3:
            warnings.append(f"summary.json mismatch {label}: report recomputes {ours}, "
                            f"summary.json says {theirs}")
    elif ours != theirs:
        warnings.append(f"summary.json mismatch {label}: report recomputes {ours}, "
                        f"summary.json says {theirs}")


def _cmp_metric(warnings: List[str], label: str, ours: Dict[str, Any],
                theirs: Any) -> None:
    if not isinstance(theirs, dict):
        return
    for field in ("numerator", "denominator"):
        if field in theirs and field in ours:
            _cmp_value(warnings, f"{label}.{field}", ours[field], theirs[field])
    for field in ("rate", "mean_coverage", "blocking_recall", "frames_ge_min_coverage",
                  "danger_frames", "stop", "unknown", "walkable", "matched", "missing"):
        if field in theirs and field in ours:
            _cmp_value(warnings, f"{label}.{field}", ours[field], theirs[field],
                       is_float=isinstance(ours[field], float))


def cross_check(agg: Dict[str, Any],
                summary_json: Optional[Dict[str, Any]]) -> List[str]:
    """Compare recomputed metrics with the eval's ``summary.json`` (warn on mismatch).

    The report tables are always recomputed from ``per_frame.csv`` (single source of truth,
    every percentage reproducible); ``summary.json`` is the frozen contract the eval tool
    promises, so any disagreement is surfaced as a warning rather than silently resolved.
    """
    warnings: List[str] = []
    if not summary_json:
        warnings.append("summary.json missing: tables are computed from per_frame.csv only "
                        "(the frozen eval contract expects both files)")
        return warnings
    summ = summary_json.get("summary") or {}
    for key, theirs in sorted(summ.items()):
        cond, sep, arm = key.rpartition("/")
        if not sep:
            continue
        ours = agg["overall"].get((cond, arm.upper()))
        if ours is None:
            warnings.append(f"summary.json has `{key}` but per_frame.csv has no rows for it")
            continue
        for metric in ("false_free", "false_free_any", "false_free_pr15_gate",
                       "false_stop", "false_stop_any", "unknown", "guidance_coverage"):
            _cmp_metric(warnings, f"{key}.{metric}", ours[metric], theirs.get(metric))
        _cmp_metric(warnings, f"{key}.coverage", ours["coverage"], theirs.get("coverage"))
        _cmp_metric(warnings, f"{key}.danger_handling", ours["danger_handling"],
                    theirs.get("danger_handling"))
        _cmp_metric(warnings, f"{key}.danger_handling_any", ours["danger_handling_any"],
                    theirs.get("danger_handling_any"))
        _cmp_metric(warnings, f"{key}.pr15_23", ours["pr15_23"], theirs.get("pr15_23"))
        their_23 = theirs.get("pr15_23")
        if isinstance(their_23, dict) and "walkable_guidance" in their_23:
            # summary.json names the 23-frame walkable count `walkable_guidance`.
            _cmp_value(warnings, f"{key}.pr15_23.walkable",
                       ours["pr15_23"]["walkable"], their_23["walkable_guidance"])
        flips = theirs.get("action_flips")
        if isinstance(flips, dict):
            if "rate" in flips:
                _cmp_value(warnings, f"{key}.action_flips.rate", ours["action_flips"]["rate"],
                           flips.get("rate"), is_float=True)
            if "frames" in flips:
                their_frames = flips["frames"]
                if their_frames not in (ours["action_flips"]["denominator"],
                                        ours["action_flips"]["numerator"]):
                    warnings.append(
                        f"summary.json mismatch {key}.action_flips.frames: report joined "
                        f"{ours['action_flips']['denominator']} frames "
                        f"({ours['action_flips']['numerator']} flips), summary says {their_frames}")
            if "pairs" in flips and flips["pairs"] != ours["action_flips"]["pairs"]:
                warnings.append(f"summary.json mismatch {key}.action_flips.pairs: "
                                f"report recomputes {ours['action_flips']['pairs']}, "
                                f"summary says {flips['pairs']}")
    per_session = summary_json.get("per_session") or {}
    for key, theirs in sorted(per_session.items()):
        parts = key.split("/")
        if len(parts) != 3:
            continue
        cond, arm, sid = parts
        ours = agg["per_session"].get((cond, arm.upper(), sid))
        if ours is None:
            continue
        if isinstance(theirs, dict) and "frames" in theirs:
            _cmp_value(warnings, f"{key}.frames", ours["frames"], theirs["frames"])
        if isinstance(theirs, dict) and "unknown_rate" in theirs:
            _cmp_value(warnings, f"{key}.unknown_rate", ours["unknown"]["rate"],
                       theirs["unknown_rate"], is_float=True)
    return warnings


# ---------------------------------------------------------------------------
# Delta paragraph (numbers only; no hand-tuned claims)
# ---------------------------------------------------------------------------
def _nd_pct(metric: Dict[str, Any]) -> str:
    return f"{_ratio(metric['numerator'], metric['denominator'])} ({_pct(metric['rate'])})"


def _nd_raw(num: Optional[int], den: Optional[int], rate: Optional[float]) -> str:
    return f"{_ratio(num, den)} ({_pct(rate)})"


def _change(label: str, a: Dict[str, Any], b: Dict[str, Any]) -> str:
    da = (b["numerator"] or 0) - (a["numerator"] or 0)
    text = f"{label} {_nd_pct(a)} -> {_nd_pct(b)} (n {da:+d}"
    if a["rate"] is not None and b["rate"] is not None:
        text += f", {100.0 * (b['rate'] - a['rate']):+.1f} pp"
    return text + ")"


def _change_danger(label: str, a: Dict[str, Any], b: Dict[str, Any]) -> str:
    na = a["stop"] + a["unknown"]
    nb = b["stop"] + b["unknown"]
    da = nb - na
    text = (f"{label} {_nd_raw(na, a['danger_frames'], a['blocking_recall'])} -> "
            f"{_nd_raw(nb, b['danger_frames'], b['blocking_recall'])} (n {da:+d}")
    if a["blocking_recall"] is not None and b["blocking_recall"] is not None:
        text += f", {100.0 * (b['blocking_recall'] - a['blocking_recall']):+.1f} pp"
    text += f"; STOP {a['stop']} -> {b['stop']}, UNKNOWN {a['unknown']} -> {b['unknown']})"
    return text


def build_delta_paragraphs(agg: Dict[str, Any]) -> List[str]:
    """A4-sized comparative paragraph: B vs A, C vs B, C vs A per condition."""
    lines: List[str] = ["## 3. B vs A / C vs B / C vs A (generated from the numbers above)", ""]
    for cond in agg["conditions"]:
        a = agg["overall"].get((cond, "A"))
        b = agg["overall"].get((cond, "B"))
        c = agg["overall"].get((cond, "C"))
        if a is None:
            continue
        lines.append(f"**{cond}**")
        lines.append("")
        for name, base, new in (("B vs A", a, b), ("C vs B", b, c), ("C vs A", a, c)):
            if base is None or new is None:
                lines.append(f"- {name}: missing arm rows")
                continue
            cov_a = base["coverage"]
            cov_b = new["coverage"]
            parts = [
                _change("false-free [ground danger]", base["false_free"], new["false_free"]),
                _change("false-free [any-negative danger]", base["false_free_any"],
                        new["false_free_any"]),
                _change("false-stop [ground danger]", base["false_stop"],
                        new["false_stop"]),
                _change("false-stop [any-negative danger]", base["false_stop_any"],
                        new["false_stop_any"]),
                _change("UNKNOWN", base["unknown"], new["unknown"]),
                (f"coverage>=min "
                 f"{_nd_raw(cov_a['frames_ge_min_coverage'], cov_a['frames'], cov_a['rate'])}"
                 f" -> {_nd_raw(cov_b['frames_ge_min_coverage'], cov_b['frames'], cov_b['rate'])}"
                 f" (mean {_fmt_opt(cov_a['mean_coverage'], '{:.2f}')} -> "
                 f"{_fmt_opt(cov_b['mean_coverage'], '{:.2f}')})"),
                _change("guidance coverage", base["guidance_coverage"],
                        new["guidance_coverage"]),
                _change_danger("danger blocking recall [ground danger]",
                               base["danger_handling"], new["danger_handling"]),
                _change_danger("danger blocking recall [any-negative danger]",
                               base["danger_handling_any"], new["danger_handling_any"]),
            ]
            p23 = new["pr15_23"]
            parts.append(f"23-frame walkable guidance {p23['walkable']}/{p23['frames']} "
                         f"(of 23; STOP {p23['stop']}, UNKNOWN {p23['unknown']})")
            flips = new["action_flips"]
            parts.append(f"action flips vs A {_nd_raw(flips['numerator'], flips['denominator'], flips['rate'])}")
            lines.append(f"- **{name}**: " + "; ".join(parts) + ".")
        lines.append("")
    lines.append("These sentences are formatted mechanically from the tables above; the "
                 "interpretation (which change is acceptable) is a human decision and is left "
                 "as `TODO(Lead)`.")
    lines.append("")
    return lines


# ---------------------------------------------------------------------------
# Markdown / CSV tables
# ---------------------------------------------------------------------------
def _tables_for_condition(agg: Dict[str, Any], cond: str) -> Dict[str, Any]:
    return {arm: agg["overall"][(cond, arm)] for arm in agg["arms"]
            if (cond, arm) in agg["overall"]}


def _recall_cell(dh: Dict[str, Any]) -> str:
    num = dh["stop"] + dh["unknown"]
    return (f"{_nd_raw(num, dh['danger_frames'], dh['blocking_recall'])} "
            f"(STOP {dh['stop']}, UNK {dh['unknown']})")


def _safety_row(arm: str, m: Dict[str, Any]) -> str:
    """One A/B/C safety row: both danger denominators side by side (never one-sided)."""
    p23 = m["pr15_23"]
    return (
        f"| {arm} | {m['frames']} | {_nd_pct(m['false_free'])} | "
        f"{_nd_pct(m['false_free_any'])} | {_nd_pct(m['false_stop'])} | "
        f"{_nd_pct(m['false_stop_any'])} | {_nd_pct(m['false_free_pr15_gate'])} | "
        f"{_recall_cell(m['danger_handling'])} | "
        f"{_recall_cell(m['danger_handling_any'])} | "
        f"{p23['walkable']}/{p23['stop']}/{p23['unknown']} of {p23['frames']} |"
    )


def _usability_row(arm: str, m: Dict[str, Any]) -> str:
    cov = m["coverage"]
    mean_cov = "n/a" if cov["mean_coverage"] is None else f"{cov['mean_coverage']:.2f}"
    flips = m["action_flips"]
    return (
        f"| {arm} | {m['frames']} | {_nd_pct(m['unknown'])} | {mean_cov} | "
        f"{_nd_raw(cov['frames_ge_min_coverage'], cov['frames'], cov['rate'])} | "
        f"{_nd_pct(m['guidance_coverage'])} | "
        f"{_nd_pct(flips)} | {_nd_pct(m['road_in_path_ignored'])} | "
        f"{_nd_pct(m['curb_in_path_ignored'])} |"
    )


def _session_row(sid: str, arm: str, m: Dict[str, Any]) -> str:
    p23 = m["pr15_23"]
    return (
        f"| `{sid[:8]}` | {arm} | {m['frames']} | {_nd_pct(m['false_free'])} | "
        f"{_nd_pct(m['false_free_any'])} | {_nd_pct(m['false_stop'])} | "
        f"{_nd_pct(m['false_stop_any'])} | {_nd_pct(m['unknown'])} | "
        f"{_nd_raw(m['coverage']['frames_ge_min_coverage'], m['coverage']['frames'], m['coverage']['rate'])} | "
        f"{_nd_pct(m['guidance_coverage'])} | "
        f"{_recall_cell(m['danger_handling'])} | "
        f"{_recall_cell(m['danger_handling_any'])} | "
        f"{p23['walkable']}/{p23['stop']}/{p23['unknown']} of {p23['frames']} |"
    )


def build_tables_md(agg: Dict[str, Any], meta: Dict[str, Any],
                    warnings: Sequence[str],
                    frame23_keys: Sequence[Tuple[str, int]]) -> str:
    """The human-readable report tables (spec §5.1-§5.3)."""
    conditions = agg["conditions"]
    sessions = sorted({sid for (_, _, sid) in agg["per_session"]})
    lines: List[str] = [
        "# P1 Path Keeper — negative-evidence fusion: A/B/C report tables",
        "",
        f"Generated: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        f"- Eval output dir: `{meta.get('eval_dir', 'n/a')}`",
        f"- Frames: {meta.get('frames', 'n/a')} across sessions: "
        f"{', '.join(f'`{s[:8]}`={n}' for s, n in (meta.get('sessions') or {}).items()) or 'n/a'}",
        f"- Conditions: {', '.join(conditions)}; arms: {', '.join(agg['arms'])}",
        f"- Evidence level: **DESKTOP_REAL_DATA** (offline desktop analysis on SANPO-Real; "
        f"not device-verified, not user-tested, no safety claim)",
        f"- PR #15 fixed 23-frame set recovered: {len(frame23_keys)} frames (reporting label "
        f"only, never fed to the algorithm)",
        "",
    ]
    if warnings:
        lines += ["## Contract checks (warnings)", ""]
        lines += [f"- {w}" for w in warnings]
        lines += [""]
    lines += [
        "## Definitions (frozen; explicit numerator/denominator, no tuned entries)",
        "",
        "- **Two danger denominators (amendment v1.1, both from oracle geometry + raw "
        "SANPO ids in the decision swept body, 1.5-6 m, |lateral| <= 0.4 m):**",
        "  - **GT excluded-ground danger (PR #15 class), primary** — GT non-walkable GROUND "
        "points with raw ids {1 road, 2 curb, 16 water, 18 inaccessible, 19 railway, 30 "
        "terrain}, >=5 points in the swept body (`danger_ground`).",
        "  - **GT any-negative danger (incl. object/vegetation occupancy), secondary** — "
        "`danger_ground` OR GT object occupancy (raw ids {4,7,8,9,10,11,12,13,14,15,20,21,"
        "22,23,24,25,26,28} at 0.15 < height <= 2.0 m) OR GT non-walkable-class ground "
        "points, >=5 points in the swept body (`danger_any`).",
        "- **false-free (primary)** = `danger_ground` and hint in (STRAIGHT, LEFT, RIGHT) / "
        "`danger_ground` frames; **false-free (any)** = `danger_any` and walkable guidance / "
        "`danger_any` frames. Primary flags stay on the GT-ground denominator; the PR #15 "
        "gate variant restricts the primary numerator to PR #15 road/curb-in-path event "
        "frames and uses those frames as denominator.",
        "- **false-stop (primary)** = hint STOP, no `danger_ground` and >=5 GT walkable "
        "swept points / frames with no `danger_ground` and >=5 GT walkable swept points; "
        "**false-stop (any)** uses `danger_any` for both the flag and the denominator.",
        "- **UNKNOWN**: hint UNKNOWN / all frames.",
        "- **coverage>=min**: tracker coverage >= `min_coverage` 0.5 / all frames; mean "
        "coverage is over all frames with a recorded coverage.",
        "- **guidance coverage**: hint in (STRAIGHT, LEFT, RIGHT) / all frames.",
        "- **action flips vs A**: hint differs from arm A on the same (condition, session, "
        "frame) / frames joined with arm A; `A->X` pairs are in report_tables.csv.",
        "- **danger blocking recall**: (STOP + UNKNOWN) on GT danger frames / GT danger "
        "frames, reported for BOTH denominators with the STOP/UNKNOWN split in the cell.",
        "- **23-frame remain table**: fixed PR #15 set (annotated/A rows with pr15_event and "
        "walkable guidance), denominator **23**, applied unchanged to every condition/arm.",
        "- **road/curb-in-path ignored**: PR #15 definitions (>=5 GT road points 1.5-6 m or "
        "curb points 2.5-6 m inside the map-frame swept body |lateral| <= 0.6 m while the "
        "output is STRAIGHT) / frames where that PR #15 event is present.",
        "",
    ]

    for cond in conditions:
        table = _tables_for_condition(agg, cond)
        lines += [f"## 1. Condition `{cond}`", "", "### 1a. Safety / negative-evidence",
                  "",
                  "Both danger denominators are shown side by side (primary = GT excluded-"
                  "ground danger / PR #15 class; secondary = GT any-negative danger incl. "
                  "object/vegetation occupancy).",
                  "",
                  "| arm | frames | false-free ground n/den (%) | false-free any n/den (%) | "
                  "false-stop ground n/den (%) | false-stop any n/den (%) | "
                  "false-free PR15-gate n/den (%) | danger recall ground n/den "
                  "(STOP/UNK) | danger recall any n/den (STOP/UNK) | "
                  "23-frame walkable/STOP/UNKNOWN (of 23) |",
                  "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
        lines += [_safety_row(arm, m) for arm, m in table.items()]
        lines += ["", "### 1b. Usability / coverage / regression events", "",
                  "| arm | frames | UNKNOWN n/den (%) | mean coverage | coverage>=min n/den (%) | "
                  "guidance coverage n/den (%) | action flips vs A n/den (%) | "
                  "road-in-path ignored n/den (%) | curb-in-path ignored n/den (%) |",
                  "|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
        lines += [_usability_row(arm, m) for arm, m in table.items()]
        lines += [""]

    lines += ["## 2. Per-session breakdown", ""]
    for cond in conditions:
        lines += [f"### Condition `{cond}`", "",
                  "| session | arm | frames | false-free ground | false-free any | "
                  "false-stop ground | false-stop any | UNKNOWN | coverage>=min | "
                  "guidance coverage | danger recall ground (STOP/UNK) | "
                  "danger recall any (STOP/UNK) | 23-frame W/S/U (of 23) |",
                  "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
        for sid in sessions:
            group = [(arm, agg["per_session"][(cond, arm, sid)]) for arm in agg["arms"]
                     if (cond, arm, sid) in agg["per_session"]]
            if not group:
                continue
            for arm, m in group:
                lines.append(_session_row(sid, arm, m))
        lines += [""]

    lines += build_delta_paragraphs(agg)
    return "\n".join(lines).rstrip() + "\n"


def build_tables_csv_rows(agg: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Tidy metric rows: condition, scope, arm, metric, numerator, denominator, rate."""
    out: List[Dict[str, Any]] = []

    def add(cond: str, scope: str, arm: str, metric: str,
            numerator: Optional[int], denominator: Optional[int],
            rate: Optional[float], definition: str) -> None:
        out.append({
            "condition": cond, "scope": scope, "arm": arm, "metric": metric,
            "numerator": "" if numerator is None else int(numerator),
            "denominator": "" if denominator is None else int(denominator),
            "rate": "" if rate is None else f"{float(rate):.6f}",
            "definition": definition,
        })

    def _emit(cond: str, scope: str, arm: str, m: Dict[str, Any]) -> None:
        dh = m["danger_handling"]
        dh_any = m["danger_handling_any"]
        add(cond, scope, arm, "frames", m["frames"], m["frames"], None, "row count")
        for metric, definition in (
            ("false_free", "danger_ground admitted as guidance / danger_ground frames "
                           "(PR #15 class, primary)"),
            ("false_free_any", "danger_any admitted as guidance / danger_any frames "
                               "(incl. object/vegetation occupancy)"),
            ("false_free_pr15_gate", "primary false-free on PR #15 road/curb path event frames"),
            ("false_stop", "STOP without danger_ground / no-ground-danger + GT walkable "
                           "swept frames (primary)"),
            ("false_stop_any", "STOP without danger_any / no-any-danger + GT walkable "
                               "swept frames (secondary)"),
            ("unknown", "hint UNKNOWN / all frames"),
            ("guidance_coverage", "walkable guidance / all frames"),
            ("road_in_path_ignored", "PR #15 road-in-path ignored / road event frames"),
            ("curb_in_path_ignored", "PR #15 curb-in-path ignored / curb event frames"),
        ):
            mv = m[metric]
            add(cond, scope, arm, metric, mv["numerator"], mv["denominator"], mv["rate"], definition)
        event_frames = m["event_frames"]
        add(cond, scope, arm, "danger_ground_frames", event_frames["danger_ground_frames"],
            m["frames"], _rate(event_frames["danger_ground_frames"], m["frames"]),
            "frames with GT excluded-ground danger in the swept body")
        add(cond, scope, arm, "danger_any_frames", event_frames["danger_any_frames"],
            m["frames"], _rate(event_frames["danger_any_frames"], m["frames"]),
            "frames with GT any-negative danger in the swept body")
        cov = m["coverage"]
        add(cond, scope, arm, "coverage_mean", None, cov["frames"], cov["mean_coverage"],
            "mean tracker coverage / frames with coverage")
        add(cond, scope, arm, "coverage_ge_min", cov["frames_ge_min_coverage"],
            cov["frames"], cov["rate"], "coverage >= min_coverage / all frames")
        flips = m["action_flips"]
        add(cond, scope, arm, "action_flips_vs_A", flips["numerator"],
            flips["denominator"], flips["rate"], "hint != arm A on joined frames")
        for pair, count in flips.get("pairs", {}).items():
            add(cond, scope, arm, f"action_flip_pair_{pair.replace('->', '_to_')}",
                count, flips["denominator"], _rate(count, flips["denominator"]),
                f"frames with hint pair {pair}")
        add(cond, scope, arm, "danger_blocking_recall", dh["stop"] + dh["unknown"],
            dh["danger_frames"], dh["blocking_recall"],
            "(STOP+UNKNOWN)/GT excluded-ground danger frames (primary)")
        add(cond, scope, arm, "danger_blocking_recall_any", dh_any["stop"] + dh_any["unknown"],
            dh_any["danger_frames"], dh_any["blocking_recall"],
            "(STOP+UNKNOWN)/GT any-negative danger frames (secondary)")
        add(cond, scope, arm, "danger_stop", dh["stop"], dh["danger_frames"],
            dh["stop_rate"], "STOP / GT excluded-ground danger frames (primary)")
        add(cond, scope, arm, "danger_stop_any", dh_any["stop"], dh_any["danger_frames"],
            dh_any["stop_rate"], "STOP / GT any-negative danger frames (secondary)")
        add(cond, scope, arm, "danger_unknown", dh["unknown"], dh["danger_frames"],
            dh["unknown_rate"], "UNKNOWN / GT excluded-ground danger frames (primary)")
        add(cond, scope, arm, "danger_unknown_any", dh_any["unknown"], dh_any["danger_frames"],
            dh_any["unknown_rate"], "UNKNOWN / GT any-negative danger frames (secondary)")
        p23 = m["pr15_23"]
        add(cond, scope, arm, "pr15_23_walkable", p23["walkable"], p23["frames"],
            _rate(p23["walkable"], p23["frames"]), "23-frame set walkable guidance")
        add(cond, scope, arm, "pr15_23_stop", p23["stop"], p23["frames"],
            _rate(p23["stop"], p23["frames"]), "23-frame set STOP")
        add(cond, scope, arm, "pr15_23_unknown", p23["unknown"], p23["frames"],
            _rate(p23["unknown"], p23["frames"]), "23-frame set UNKNOWN")

    for (cond, arm), m in sorted(agg["overall"].items()):
        _emit(cond, "overall", arm, m)
    for (cond, arm, sid), m in sorted(agg["per_session"].items()):
        _emit(cond, f"session:{sid}", arm, m)
    return out


# ---------------------------------------------------------------------------
# Failure-frame layer classification + deterministic selection
# ---------------------------------------------------------------------------
def classify_failure_layer(row: Dict[str, Any]) -> str:
    """Mechanical pipeline-ordered layer label for one failing row.

    Order: geometry -> temporal -> decision/segmentation (see module docstring).
    Annotated frames can never be ``segmentation`` (the annotation is right by
    construction).  Uses only recorded per-frame fields; no tuned thresholds.
    """
    condition = row.get("condition")
    hint = row.get("hint")
    reason = row.get("reason") or ""
    calib_reason = row.get("calib_reason") or ""
    calib_ok = row.get("calib_ok")
    n_points = row.get("n_points")
    gt_walk = row.get("gt_walk_pts_swept") or 0
    gt_danger = bool(row.get("danger_ground"))
    neg = int(row.get("hazard_points") or 0) + int(row.get("non_walkable_points") or 0)
    streak = int(row.get("temporal_streak") or 0)

    # 1) geometry: no metric depth / calibration not ok / GT walkable not projected.
    if calib_ok is False:
        return "geometry"
    if reason in ("no_metric_depth", "no_gt_plane") or \
            calib_reason in ("no_metric_depth", "no_gt_plane"):
        return "geometry"
    if hint == UNKNOWN and n_points is not None and n_points < MIN_POINTS \
            and gt_walk >= MIN_EVENT_POINTS:
        return "geometry"

    # 2) temporal: arm C blocking evidence present but not yet confirmed.  The eval's
    # confirming reason codes are exact; a positive streak with UNKNOWN can only mean the
    # streak is below the confirmation frame count (otherwise the arm would have returned
    # STOP), so it is also temporal without knowing the configured frame count.
    if reason in ("hazard_confirming", "non_walkable_confirming"):
        return "temporal"
    if row.get("arm") == "C" and 0 < streak and hint == UNKNOWN:
        return "temporal"

    # 3) decision vs segmentation: is the negative evidence in the projected swept body?
    # The union danger set decides whether a mask/geometry layer demonstrably dropped the
    # evidence; the primary ground-danger flags stay the primary safety numbers elsewhere.
    danger_union = gt_danger or bool(row.get("danger_any"))
    if neg > 0:
        # Evidence is present but below support/gating or was overridden by the decision.
        return "decision"
    if condition == "production" and danger_union:
        # v5 mask supplies no negative-evidence points for GT danger -> mask missed it.
        return "segmentation"
    if danger_union:
        # Annotated mask/geometry are right by construction, yet the binned swept-body
        # counters see no negative evidence: the decision layer's representation is the
        # first loss (e.g. GT danger only in the 5.5-6.0 m tail of the 1.5-6 m rule, which
        # the inherited legacy integer-bin convention does not bin; or below per-bin support).
        return "decision"
    # No GT danger, no negative evidence: the corridor decision itself still fired.
    return "decision"


def failure_kinds_of_row(row: Dict[str, Any]) -> List[str]:
    """Severity-ordered failure kinds flagged on one row (may be empty)."""
    present = {
        "false_free": bool(row.get("false_free")),
        "false_free_any": bool(row.get("false_free_any")),
        "false_stop": bool(row.get("false_stop")),
        "unknown_when_gt_walkable": bool(row.get("unknown_when_gt_walkable")),
    }
    return [kind for kind in KIND_ORDER if present[kind]]


def select_failure_frames(rows: Sequence[Dict[str, Any]], max_total: int = 10,
                          arms: Sequence[str] = FINAL_ARMS) -> List[Dict[str, Any]]:
    """Deterministically pick representative remaining failing frames of the final arms.

    One representative per (condition, session, frame) — the three failure kinds are
    mutually exclusive on ``hint`` so a frame has one severity kind (if B and C disagree on
    the same frame the more severe kind wins and both kinds are kept in ``kinds``).  Picks
    are then round-robined over (condition, kind) buckets; inside a bucket the least-used
    session wins (ties: session name, then frame) so sessions are balanced.  No randomness,
    no synthetic frames — only rows present in ``per_frame.csv``.
    """
    arms = tuple(str(a).upper() for a in arms)
    frame_entries: Dict[Tuple[str, str, int], Dict[str, Any]] = {}
    for r in rows:
        if r.get("arm") not in arms:
            continue
        kinds = failure_kinds_of_row(r)
        if not kinds:
            continue
        key = (r["condition"], r["session"], int(r["frame"]))
        entry = frame_entries.setdefault(key, {
            "condition": r["condition"], "session": r["session"],
            "frame": int(r["frame"]), "rows": {}, "kinds": set(),
        })
        entry["rows"][r["arm"]] = r
        entry["kinds"].update(kinds)

    entries: List[Dict[str, Any]] = []
    for key in sorted(frame_entries):
        entry = frame_entries[key]
        rep = None
        for arm in arms:
            if arm in entry["rows"]:
                rep = entry["rows"][arm]
                break
        kinds = [k for k in KIND_ORDER if k in entry["kinds"]]
        entries.append({
            "condition": entry["condition"], "session": entry["session"],
            "frame": entry["frame"], "row": rep, "kind": kinds[0], "kinds": kinds,
        })
    if not entries or max_total <= 0:
        return []

    conditions = _ordered({e["condition"] for e in entries}, CONDITION_ORDER)
    buckets: Dict[Tuple[str, str], Dict[str, List[Dict[str, Any]]]] = defaultdict(
        lambda: defaultdict(list))
    for entry in entries:
        buckets[(entry["condition"], entry["kind"])][entry["session"]].append(entry)
    for pair in buckets:
        for sid in buckets[pair]:
            buckets[pair][sid].sort(key=lambda e: e["frame"])

    pairs: List[Tuple[str, str]] = [(cond, kind) for kind in KIND_ORDER
                                    for cond in conditions if buckets.get((cond, kind))]
    session_use: Counter = Counter()
    picked: List[Dict[str, Any]] = []
    picked_keys: Set[Tuple[str, str, int]] = set()

    def _pick_balanced(pair: Tuple[str, str]) -> Optional[Dict[str, Any]]:
        queues = buckets.get(pair, {})
        available = [sid for sid, q in queues.items() if q]
        if not available:
            return None
        sid = min(available, key=lambda s: (session_use[s], s))
        entry = queues[sid].pop(0)
        session_use[sid] += 1
        return entry

    while len(picked) < max_total:
        progressed = False
        for pair in pairs:
            entry = _pick_balanced(pair)
            if entry is None:
                continue
            progressed = True
            key = (entry["condition"], entry["session"], entry["frame"])
            if key in picked_keys:
                continue
            picked.append(entry)
            picked_keys.add(key)
            if len(picked) >= max_total:
                break
        if not progressed:
            break

    # Coverage pass: if a severity kind exists but was never picked as the primary kind
    # (possible when it only appears as a secondary kind on a merged frame), append such a
    # frame if there is room, otherwise replace the last pick whose kind is still covered.
    def _missing_kinds() -> List[str]:
        picked_primary = {p["kind"] for p in picked}
        return [k for k in KIND_ORDER if k not in picked_primary
                and any(k in e["kinds"] for e in entries if
                        (e["condition"], e["session"], e["frame"]) not in picked_keys)]

    for kind in _missing_kinds():
        pool = [e for e in entries if kind in e["kinds"]
                and (e["condition"], e["session"], e["frame"]) not in picked_keys]
        if not pool:
            continue
        pool.sort(key=lambda e: (session_use[e["session"]], e["condition"],
                                 e["session"], e["frame"]))
        cand = pool[0]
        if len(picked) < max_total:
            picked.append(cand)
        else:
            replaceable = [i for i, p in enumerate(picked)
                           if sum(1 for q in picked if q["kind"] == p["kind"]) > 1]
            if not replaceable:
                continue
            picked.pop(replaceable[-1])
            # keep chronological/deterministic order for the manifest
            picked.append(cand)
        picked_keys.add((cand["condition"], cand["session"], cand["frame"]))
        session_use[cand["session"]] += 1

    picked.sort(key=lambda e: (KIND_ORDER.index(e["kind"]),
                               CONDITION_ORDER.index(e["condition"])
                               if e["condition"] in CONDITION_ORDER else 99,
                               e["session"], e["frame"]))
    return picked


# ---------------------------------------------------------------------------
# Failure overlays (GT depth + GT ground plane; no model inference)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class _Pose:
    """Duck-typed stand-in for ``shadowwalk.Pose2D`` (only x_m/z_m/yaw_rad are read)."""
    x_m: float
    z_m: float
    yaw_rad: float = 0.0


_CTE: Any = None


def _cte():
    """Lazily import the frozen PR #15 helper module (keeps pure functions importable)."""
    global _CTE
    if _CTE is None:
        import corridor_truth_eval as cte  # type: ignore  # noqa: F401
        _CTE = cte
    return _CTE


def project_level_point_to_pixel(x_lat: float, z_fwd: float, K: np.ndarray,
                                 up: np.ndarray, h: float) -> Tuple[float, float]:
    """Project a level-frame ground point (height 0) to image pixel ``(u, v)``.

    Level frame from :func:`blindnav_pc.bev.level_frame`; the camera is ``h`` metres above
    the ground plane with normal ``up``.  Returns NaNs when the point is not in front.
    """
    f, right = level_frame(np.asarray(up, dtype=np.float64))
    X = (float(x_lat) * right + float(z_fwd) * f
         - float(h) * np.asarray(up, dtype=np.float64))
    z = float(X[2])
    if not math.isfinite(z) or z <= 1e-6:
        return float("nan"), float("nan")
    uvw = np.asarray(K, dtype=np.float64) @ X
    return float(uvw[0] / uvw[2]), float(uvw[1] / uvw[2])


def swept_body_outline_pixels(K: np.ndarray, up: np.ndarray, h: float,
                              near_m: float = NEAR_M, far_m: float = FAR_M,
                              half_m: float = SWEPT_HALF_WIDTH_M,
                              n_steps: int = 9) -> np.ndarray:
    """Closed pixel ring of the decision swept body (near..far m, +/- half width m).

    The ring is the decision body in the body/level frame (the per-frame pose yaw is the
    camera heading), projected through the GT ground geometry.  Diagnostic overlay only.
    """
    near_m = float(near_m)
    far_m = float(max(far_m, near_m + 1e-3))
    fwd = np.linspace(near_m, far_m, max(2, int(n_steps)))
    lat = np.linspace(-half_m, half_m, max(2, int(n_steps)))
    # Near edge left->right, right edge near->far, far edge right->left, left edge far->near.
    ring = ([(x, near_m) for x in lat]
            + [(half_m, d) for d in fwd]
            + [(x, far_m) for x in lat[::-1]]
            + [(-half_m, d) for d in fwd[::-1]])
    pts: List[Tuple[float, float]] = []
    for x_lat, z_fwd in ring:
        u, v = project_level_point_to_pixel(x_lat, z_fwd, K, up, h)
        if math.isfinite(u) and math.isfinite(v):
            pts.append((u, v))
    return np.asarray(pts, dtype=np.float64).reshape(-1, 2)


def build_gt_point_masks(gt: Any, K: np.ndarray, up: np.ndarray, h: float,
                         pixel_stride: int = PIXEL_STRIDE) -> Tuple[np.ndarray, np.ndarray]:
    """GT walkable / GT hazard+non-walkable pixel masks from GT depth + GT plane.

    ``gt`` is a SANPO ``SanpoGT`` (``depth_m``, ``seg``).  The masks scatter stride-sampled
    ground points back to pixels and dilate by the stride so the tint is readable.
    """
    cte = _cte()
    H, W = gt.seg.shape[:2]
    walk_mask = np.zeros((H, W), dtype=np.uint8)
    hazard_mask = np.zeros((H, W), dtype=np.uint8)
    x_lat, z_fwd, height, (vs, us) = depth_to_level_points(
        gt.depth_m, K, up, h, stride=pixel_stride)
    valid = np.isfinite(x_lat) & np.isfinite(z_fwd)
    raw = gt.seg[vs, us].astype(np.int64)
    ground = np.abs(height) <= MAX_HEIGHT_M
    walk_sel = valid & ground & np.isin(raw, cte.SAFE_IDS)
    danger_sel = valid & ground & np.isin(raw, DANGER_GROUND_IDS)
    obstacle_sel = (valid & (height > OBSTACLE_MIN_M) & (height <= OBSTACLE_MAX_M)
                    & np.isin(raw, OBSTACLE_IDS))
    walk_mask[vs[walk_sel], us[walk_sel]] = 1
    hazard_mask[vs[danger_sel], us[danger_sel]] = 1
    hazard_mask[vs[obstacle_sel], us[obstacle_sel]] = 1
    k = np.ones((pixel_stride + 1, pixel_stride + 1), np.uint8)
    walk_mask = cv2.dilate(walk_mask, k)
    hazard_mask = cv2.dilate(hazard_mask, k)
    return walk_mask.astype(bool), hazard_mask.astype(bool)


def _put_text_lines(img: np.ndarray, lines: Sequence[str], x: int = 6, y: int = 14,
                    dy: int = 14, scale: float = 0.4) -> None:
    """White text with a black outline, so overlays stay readable on any RGB frame."""
    for k, text in enumerate(lines):
        pos = (int(x), int(y + dy * k))
        cv2.putText(img, text, pos, cv2.FONT_HERSHEY_SIMPLEX, scale, (0, 0, 0), 3,
                    cv2.LINE_AA)
        cv2.putText(img, text, pos, cv2.FONT_HERSHEY_SIMPLEX, scale, (255, 255, 255), 1,
                    cv2.LINE_AA)


def _fmt_opt(value: Any, fmt: str = "{}") -> str:
    if value is None:
        return "n/a"
    try:
        return fmt.format(value)
    except (TypeError, ValueError):
        return str(value)


def render_failure_overlay(img: np.ndarray, row: Dict[str, Any], layer: str,
                           kinds: Sequence[str], gt: Any, K: np.ndarray,
                           up: Optional[np.ndarray], h: Optional[float]) -> np.ndarray:
    """BGR overlay: GT walkable tint, GT hazard/non-walkable tint, swept-body outline, text."""
    out = img.copy()
    if up is not None and h is not None:
        walk_pts, hazard_pts = build_gt_point_masks(gt, K, up, h)
        overlay = out.copy()
        overlay[walk_pts] = (0, 185, 0)          # BGR green: GT walkable
        overlay[hazard_pts] = (0, 0, 215)        # BGR red: GT hazard / non-walkable (danger set)
        out = cv2.addWeighted(overlay, 0.40, out, 0.60, 0)
        ring = swept_body_outline_pixels(K, up, h)
        if len(ring) >= 2:
            cv2.polylines(out, [np.round(ring).astype(np.int32)], isClosed=True,
                          color=(0, 255, 255), thickness=2, lineType=cv2.LINE_AA)
            # near/far labels so the reader can orient the swept body
            u_n, v_n = project_level_point_to_pixel(0.0, NEAR_M, K, up, h)
            u_f, v_f = project_level_point_to_pixel(0.0, FAR_M, K, up, h)
            for text, (u, v) in (("1.5 m", (u_n, v_n)), ("6.0 m", (u_f, v_f))):
                if math.isfinite(u) and math.isfinite(v):
                    cv2.putText(out, text, (int(u) + 4, int(v) - 2),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.35, (0, 255, 255), 1,
                                cv2.LINE_AA)
    else:
        cv2.putText(out, "GT plane unavailable", (6, out.shape[0] - 8),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 0, 255), 1, cv2.LINE_AA)

    dh = row.get("danger_handling") if isinstance(row.get("danger_handling"), dict) else {}
    lines = [
        (f"{row.get('condition')}/{row.get('arm')} s={str(row.get('session'))[:8]} "
         f"f={row.get('frame')} -> {row.get('hint')} ({row.get('reason')})"),
        (f"cover={_fmt_opt(row.get('coverage'), '{:.2f}')} n={row.get('n_points')} "
         f"calib_ok={row.get('calib_ok')} ({row.get('calib_reason')})"),
        (f"ev walk={row.get('walkable_swept_points')} haz={row.get('hazard_points')}"
         f"/b{row.get('hazard_bins')} nonw={row.get('non_walkable_points')}"
         f"/b{row.get('non_walkable_bins')} unk={row.get('unknown_points')}"
         f"/b{row.get('unknown_bins')} streak={row.get('temporal_streak')}"),
        (f"GT walk_swept={row.get('gt_walk_pts_swept')} "
         f"danger_ground={int(bool(row.get('danger_ground')))} "
         f"danger_any={int(bool(row.get('danger_any')))} "
         f"road15={row.get('pr15_road_path_band')} curb15={row.get('pr15_curb_path_band')} "
         f"pr15_23={int(bool(row.get('pr15_23')))}"),
        f"layer={layer} kinds={','.join(kinds)}",
        "tint: green=GT walkable red=GT hazard/non-walkable yellow=swept body 1.5-6m +-0.4m",
    ]
    _put_text_lines(out, lines)
    return out


def save_failures(rows: Sequence[Dict[str, Any]],
                  failures_dir: str,
                  data_root: str,
                  camera: str = "camera_chest",
                  max_width: int = 640,
                  max_total: int = 10) -> List[Dict[str, Any]]:
    """Render + save >= 5 deterministic remaining failing frames and ``failures.json``.

    Uses the eval output rows (no model inference) and the local SANPO-Real sessions for
    the raw RGB + GT depth/segmentation.  Raises ``SystemExit(2)`` with an explicit
    ``BLOCKED_MISSING_DATA`` message when a session directory is absent (never fabricates
    frames).
    """
    from eval.sanpo import SanpoSession

    selected = select_failure_frames(rows, max_total=max_total)
    if not selected:
        print("warning: no remaining failing frames of the final arms B/C to render")
        return []
    if not data_root or not os.path.isdir(data_root):
        raise SystemExit(f"BLOCKED_MISSING_DATA: data root {data_root!r} not found; failure "
                         "frames need the local SANPO-Real sessions (pass --data-root)")
    os.makedirs(failures_dir, exist_ok=True)
    sessions: Dict[str, Any] = {}
    manifest: List[Dict[str, Any]] = []
    for idx, cand in enumerate(selected, start=1):
        session_name = str(cand["session"])
        frame = int(cand["frame"])
        row = cand["row"]
        session_dir = os.path.join(data_root, session_name)
        if session_name not in sessions:
            if not os.path.isdir(session_dir):
                raise SystemExit(f"BLOCKED_MISSING_DATA: session {session_dir!r} not found "
                                 "(pass --data-root pointing at the SANPO-Real root)")
            sessions[session_name] = SanpoSession(session_dir, camera, max_width=max_width)
        session = sessions[session_name]
        gt = session.gt(frame)
        yaw = math.radians(float(row.get("yaw_err_deg") or 0.0))
        pose = _Pose(0.0, 0.0, yaw)
        plane = _cte().oracle_plane(session, frame, gt)
        if plane is None:
            up, hgt = None, None
            layer = "geometry"
        else:
            up, hgt = plane
            layer = classify_failure_layer(row)
        img = session.inputs(frame).bgr
        overlay = render_failure_overlay(img, row, layer, cand["kinds"], gt, session.K,
                                         up, hgt)
        # Deterministic id prefix: the same (kind, session, frame) can fail in both input
        # conditions, so ``<kind>_<session8>_<frame>`` alone would collide; the PR #15-style
        # ordinal keeps every raw/overlay pair unique and keeps filenames relocatable.
        stem = f"f{idx:02d}_{cand['kind']}_{session_name[:8]}_{frame:06d}"
        raw_name = stem + "_raw.jpg"
        overlay_name = stem + "_overlay.jpg"
        raw_src = os.path.join(session.ldir, "video_frames", f"{frame:06d}.png")
        raw = cv2.imread(raw_src, cv2.IMREAD_COLOR)
        if raw is not None:
            cv2.imwrite(os.path.join(failures_dir, raw_name), raw,
                        [cv2.IMWRITE_JPEG_QUALITY, 92])
        cv2.imwrite(os.path.join(failures_dir, overlay_name), overlay,
                    [cv2.IMWRITE_JPEG_QUALITY, 92])
        manifest.append({
            "id": f"f{idx:02d}",
            "kind": cand["kind"],
            "kinds": list(cand["kinds"]),
            "condition": cand["condition"],
            "arm": row.get("arm"),
            "session": session_name,
            "frame": frame,
            "hint": row.get("hint"),
            "reason": row.get("reason"),
            "layer": layer,
            "raw": raw_name if raw is not None else "",
            "overlay": overlay_name,
            "gt": {
                "walk_pts_swept": row.get("gt_walk_pts_swept"),
                "danger_ground": bool(row.get("danger_ground")),
                "danger_any": bool(row.get("danger_any")),
                "gt_danger_ground_pts": row.get("gt_danger_ground_pts"),
                "gt_danger_any_pts": row.get("gt_danger_any_pts"),
                "pr15_road_path_band": row.get("pr15_road_path_band"),
                "pr15_curb_path_band": row.get("pr15_curb_path_band"),
                "pr15_event": bool(row.get("pr15_event")),
                "pr15_23": bool(row.get("pr15_23")),
            },
            "evidence": {field: row.get(field) for field in EVIDENCE_FIELDS},
        })
    with open(os.path.join(failures_dir, "failures.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2, ensure_ascii=False)
    print(f"wrote {os.path.join(failures_dir, 'failures.json')} "
          f"({len(manifest)} failure frames)")
    return manifest


# ---------------------------------------------------------------------------
# PR-body skeleton (numbers filled; human judgement left as TODO(Lead))
# ---------------------------------------------------------------------------
def _before_bullet(cond: str, m: Dict[str, Any]) -> str:
    dh = m["danger_handling"]
    dh_any = m["danger_handling_any"]
    return (f"- `{cond}` arm A: false-free ground {_nd_pct(m['false_free'])} / any "
            f"{_nd_pct(m['false_free_any'])}; false-stop ground {_nd_pct(m['false_stop'])} / "
            f"any {_nd_pct(m['false_stop_any'])}; UNKNOWN {_nd_pct(m['unknown'])}; "
            f"coverage>=min {_nd_raw(m['coverage']['frames_ge_min_coverage'], m['coverage']['frames'], m['coverage']['rate'])}; "
            f"danger blocking recall ground "
            f"{_nd_raw(dh['stop'] + dh['unknown'], dh['danger_frames'], dh['blocking_recall'])} "
            f"/ any {_nd_raw(dh_any['stop'] + dh_any['unknown'], dh_any['danger_frames'], dh_any['blocking_recall'])}; "
            f"23-frame walkable {m['pr15_23']['walkable']}/{m['pr15_23']['frames']}.")


def _after_bullet(cond: str, arm: str, m: Dict[str, Any]) -> str:
    dh = m["danger_handling"]
    dh_any = m["danger_handling_any"]
    p23 = m["pr15_23"]
    flips = m["action_flips"]
    return (f"- `{cond}` arm {arm}: false-free ground {_nd_pct(m['false_free'])} / any "
            f"{_nd_pct(m['false_free_any'])}; false-stop ground {_nd_pct(m['false_stop'])} / "
            f"any {_nd_pct(m['false_stop_any'])}; UNKNOWN {_nd_pct(m['unknown'])}; "
            f"coverage>=min {_nd_raw(m['coverage']['frames_ge_min_coverage'], m['coverage']['frames'], m['coverage']['rate'])}; "
            f"danger blocking recall ground "
            f"{_nd_raw(dh['stop'] + dh['unknown'], dh['danger_frames'], dh['blocking_recall'])} "
            f"(STOP {dh['stop']}, UNKNOWN {dh['unknown']}) / any "
            f"{_nd_raw(dh_any['stop'] + dh_any['unknown'], dh_any['danger_frames'], dh_any['blocking_recall'])} "
            f"(STOP {dh_any['stop']}, UNKNOWN {dh_any['unknown']}); 23-frame "
            f"walkable {p23['walkable']}/{p23['frames']}; action flips vs A "
            f"{_nd_pct(flips)}.")


def _regression_bullets(agg: Dict[str, Any]) -> List[str]:
    bullets: List[str] = []
    for cond in agg["conditions"]:
        a = agg["overall"].get((cond, "A"))
        if a is None:
            continue
        for arm in FINAL_ARMS:
            new = agg["overall"].get((cond, arm))
            if new is None:
                continue
            for label, key, metric in (
                ("UNKNOWN", "unknown", new["unknown"]),
                ("coverage>=min", "coverage", new["coverage"]),
                ("guidance coverage", "guidance_coverage", new["guidance_coverage"]),
            ):
                base = a[key]
                if key == "coverage":
                    bullets.append(
                        f"- `{cond}` arm {arm} vs A: coverage>=min "
                        f"{_nd_raw(base['frames_ge_min_coverage'], base['frames'], base['rate'])}"
                        f" -> {_nd_raw(metric['frames_ge_min_coverage'], metric['frames'], metric['rate'])}; "
                        f"mean coverage "
                        f"{_fmt_opt(base['mean_coverage'], '{:.2f}')} -> "
                        f"{_fmt_opt(metric['mean_coverage'], '{:.2f}')}.")
                else:
                    bullets.append(f"- `{cond}` arm {arm} vs A: {label} "
                                   f"{_nd_pct(base)} -> {_nd_pct(metric)}.")
            bullets.append(f"- `{cond}` arm {arm} vs A: "
                           f"{_change('false-stop [ground danger]', a['false_stop'],
                                     new['false_stop'])}.")
            bullets.append(f"- `{cond}` arm {arm} vs A: "
                           f"{_change('false-stop [any-negative danger]', a['false_stop_any'],
                                     new['false_stop_any'])}.")
            bullets.append(f"- `{cond}` arm {arm} vs A: "
                           f"{_change('false-free [ground danger]', a['false_free'],
                                     new['false_free'])}.")
            bullets.append(f"- `{cond}` arm {arm} vs A: "
                           f"{_change('false-free [any-negative danger]', a['false_free_any'],
                                     new['false_free_any'])}.")
    return bullets


def build_report_body(agg: Dict[str, Any], meta: Dict[str, Any],
                      warnings: Sequence[str],
                      failures_manifest: Optional[Sequence[Dict[str, Any]]] = None,
                      frame23_keys: Sequence[Tuple[str, int]] = ()) -> str:
    """Pull-request-ready body skeleton with the measured numbers filled in."""
    conditions = agg["conditions"]
    arms = agg["arms"]
    frames = meta.get("frames", "n/a")
    sessions = meta.get("sessions") or {}
    n_sessions = len(sessions) if isinstance(sessions, dict) else "n/a"
    failures_manifest = list(failures_manifest or [])
    any_annotated = "annotated" in conditions
    a_ann = agg["overall"].get(("annotated", "A"))
    lines: List[str] = [
        "# P1 Path Keeper: implement negative-evidence fusion in the corridor decision layer",
        "",
        "> Evidence level: **DESKTOP_REAL_DATA** — offline desktop analysis on SANPO-Real "
        "(chest-mounted VIO pose used as an IMU proxy). Not device-verified, not user-tested, "
        "no safety claim; BlindNav is not a certified mobility aid.",
        f">",
        f"> Generated by `pc/tools/corridor_evidence_report.py` from "
        f"`{meta.get('eval_dir', 'n/a')}`: {frames} frames / {n_sessions} sessions / "
        f"conditions {conditions}.",
        "",
        "## Problem",
        "",
        "The corridor decision layer (ShadowWalk) consumes walkable points only: a surface "
        "that the segmentation correctly labels as non-walkable contributes nothing to the "
        "decision, so a road/curb/obstacle inside the swept body cannot raise STOP or "
        "UNKNOWN. PR #15 measured the loss with the annotated mask and oracle geometry "
        "(the best case the segmenter can give); this ticket adds explicit negative-evidence "
        "channels to the corridor decision and measures arms A (baseline), B (negative "
        "evidence, no temporal confirmation) and C (B + 3-frame temporal confirmation).",
        "",
    ]
    if a_ann is not None:
        lines.append(
            f"Baseline (annotated arm A): the corridor emitted walkable guidance on "
            f"{a_ann['pr15_23']['walkable']}/{a_ann['pr15_23']['frames']} frames of the "
            f"fixed PR #15 regression set (by construction of that set), and on this run the "
            f"false-free rate is {_nd_pct(a_ann['false_free'])} "
            f"({a_ann['danger_handling']['danger_frames']} GT danger frames). TODO(Lead): "
            f"cite `docs/CORRIDOR_TRUTH_LOSS_20261106.md` §6.1 here.")
    lines += [
        "",
        "## Evidence Before",
        "",
        "Same frozen eval set (all frames of the three local SANPO-Real sessions, every 5th "
        "source frame, identical per-frame corridor/pose setup), baseline arm A only:",
        "",
    ]
    for cond in conditions:
        a = agg["overall"].get((cond, "A"))
        if a is not None:
            lines.append(_before_bullet(cond, a))
    lines += [
        "",
        "TODO(Lead): keep the PR #15 numbers as the historical baseline reference; the "
        "numbers above are the A arm re-measured by the frozen eval harness for this change.",
        "",
        "## Change",
        "",
        "TODO(Lead): point at the code (`pc/blindnav_pc/corridor_evidence.py`, "
        "`ShadowWalkTracker.update_evidence` in `pc/blindnav_pc/shadowwalk.py`, "
        "`pc/tools/corridor_evidence_eval.py`) and state that the existing walkable-only "
        "path is unchanged (`update(points, pose) == update_evidence(walkable_only, pose)`).",
        "",
        f"- Arms measured on identical frames within each input condition: "
        f"{', '.join(f'A={m}' for m in ['baseline (walkable only)'])}; B=negative-evidence "
        f"channels (hazard / non-walkable / unknown) with `temporal_confirm_frames=0`; "
        f"C=B + temporal confirmation (frames: "
        f"`{meta.get('temporal_confirm_frames', 'n/a')}`).",
        f"- Evidence role map: the same class taxonomy in both conditions (0 ignore, 1 SAFE, "
        f"2 BOUNDARY, 3 CONDITIONAL, 4 NON -> ignore/walkable/hazard/non-walkable/"
        f"non-walkable); no special-casing per condition.",
        f"- Reporting (amendment v1.1, reporting-only): every safety table shows TWO GT "
        f"danger denominators side by side — primary **GT excluded-ground danger (PR #15 "
        f"class)** and secondary **GT any-negative danger (incl. object/vegetation "
        f"occupancy)**. Primary flags stay on the ground denominator; the decision rule/API "
        f"is unchanged.",
        f"- Frozen constants reused, not tuned: `points_per_bin`=5 support threshold, "
        f"near/far = {NEAR_M}-{FAR_M} m, bin {BIN_M} m, swept half width "
        f"{SWEPT_HALF_WIDTH_M} m (= body_width 0.5/2 + margin 0.15), pixel stride "
        f"{PIXEL_STRIDE}.",
        f"- Hard constraints honoured (to be confirmed by the Lead): no segmentation "
        f"retraining, no DAV2 replacement, no band widening, no synthetic acceptance data, "
        f"no STOP Safety Policy / MotionAuthority / ArrivalAuthority changes. "
        f"TODO(Lead): confirm and link the diff.",
        "",
        "## Evidence After",
        "",
    ]
    for cond in conditions:
        for arm in FINAL_ARMS:
            new = agg["overall"].get((cond, arm))
            if new is not None:
                lines.append(_after_bullet(cond, arm, new))
    lines += [""]

    # 23-frame remain table (denominator 23)
    lines += ["### PR #15 fixed 23-frame set (denominator 23; reporting label only)", "",
              "| condition | arm | walkable | STOP | UNKNOWN |", "|---|---|---:|---:|---:|"]
    for cond in conditions:
        for arm in arms:
            m = agg["overall"].get((cond, arm))
            if m is None:
                continue
            p23 = m["pr15_23"]
            lines.append(f"| `{cond}` | {arm} | {p23['walkable']} | {p23['stop']} | "
                         f"{p23['unknown']} |")
    lines += ["", "### Comparative deltas (generated from the numbers above)", ""]
    for line in build_delta_paragraphs(agg):
        if line.startswith("##"):
            continue
        if line:
            lines.append(line)
        else:
            lines.append("")
    lines += [""]

    lines += [
        "### Failure frames saved with `--save-failures`",
        "",
    ]
    if failures_manifest:
        kinds = Counter(f["kind"] for f in failures_manifest)
        conds = Counter(f["condition"] for f in failures_manifest)
        sess = Counter(f["session"] for f in failures_manifest)
        layers = Counter(f["layer"] for f in failures_manifest)
        lines += [f"- {len(failures_manifest)} frames saved "
                  f"(both conditions then severity kinds, sessions balanced; see "
                  f"`failures/failures.json`).",
                  f"- kinds: {dict(kinds)}; conditions: {dict(conds)}; layers: {dict(layers)}.",
                  f"- sessions: {dict((k[:8], v) for k, v in sess.items())}.",
                  ""]
    else:
        lines += ["- Not rendered in this run: re-run with `--save-failures` to write "
                  ">=5 raw RGB + overlay pairs and `failures/failures.json`.", ""]

    lines += [
        "## Regression",
        "",
        "- Measurement only in this PR body skeleton: the change is the corridor decision "
        "layer (negative-evidence channels), not segmentation/depth/band width. TODO(Lead): "
        "state the pytest commands and the full `pc/tests/` result.",
        "- No segmentation retraining, no DAV2 replacement, no band widening, no synthetic "
        "acceptance data, no STOP Safety Policy / MotionAuthority / ArrivalAuthority edit; "
        "the walkable-only `update()` path must stay byte-for-byte equivalent. TODO(Lead): "
        "confirm against the diff.",
        "- Measured trade-offs (same frozen eval set) — these are the regression numbers to "
        "argue about honestly:",
        "",
    ]
    lines += _regression_bullets(agg)
    lines += [""]

    lines += [
        "## Evidence Level",
        "",
        f"**DESKTOP_REAL_DATA** — offline PC run on SANPO-Real ({n_sessions} local "
        f"session(s), chest-mounted camera, VIO pose used as an IMU proxy). Not device-verified, not "
        "user-tested, not a safety claim. No synthetic acceptance data and no model "
        "retraining were used for these numbers.",
        "",
        "## Remaining Risks",
        "",
    ]
    lines += [f"- Contract check: {w}" for w in warnings]
    if not warnings:
        lines.append("- Contract checks: none (per_frame.csv and summary.json agree).")
    lines += [
        "- NOT_EVALUABLE — device/user safety: no phone, no device IMU, no blind-user "
        "session; all numbers are desktop SANPO-Real.",
        "- NOT_EVALUABLE — Android wiring: the `app/` tree is `NOT_CONNECTED` for this "
        "change (out of scope for this ticket); the PC decision layer is measured, the "
        "device path is not.",
        "- The eval uses the recorded VIO route as the corridor centreline and a per-frame "
        "pose = camera heading minus route tangent; walker drifts and real route-following "
        "are not measurable from this harness.",
        "- The 23-frame set is a reporting label recovered from annotated/A rows; it is "
        "never fed into the algorithm or used for tuning.",
        "- The GT-negative-evidence tints in the failure overlays are drawn from GT depth + "
        "GT ground plane (diagnostic geometry), not from the production input chain.",
        "- TODO(Lead): add any human judgement (acceptability of the trade-off, rollout "
        "decision, PR links) and the two-round-rule decision trail.",
        "",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# IO + CLI
# ---------------------------------------------------------------------------
def load_eval_dir(eval_dir: str) -> Tuple[List[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """Read ``per_frame.csv`` + ``summary.json`` from the eval output directory."""
    csv_path = os.path.join(eval_dir, "per_frame.csv")
    if not os.path.isfile(csv_path):
        raise SystemExit(f"BLOCKED_MISSING_INPUT: {csv_path} not found (expected the "
                         "corridor_evidence_eval.py output dir)")
    with open(csv_path, "r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        raw_rows = [dict(row) for row in reader]
    rows = normalize_rows(raw_rows)
    if not rows:
        raise SystemExit(f"BLOCKED_EMPTY_INPUT: {csv_path} has no data rows")
    summary: Optional[Dict[str, Any]] = None
    summary_path = os.path.join(eval_dir, "summary.json")
    if os.path.isfile(summary_path):
        with open(summary_path, "r", encoding="utf-8") as fh:
            summary = json.load(fh)
    return rows, summary


def build_meta(rows: Sequence[Dict[str, Any]], summary: Optional[Dict[str, Any]],
               eval_dir: str) -> Dict[str, Any]:
    """Meta dict for the report; ``summary.json`` meta wins over per_frame-derived values."""
    meta: Dict[str, Any] = {}
    if summary and isinstance(summary.get("meta"), dict):
        meta.update(summary["meta"])
    meta.setdefault("eval_dir", os.path.abspath(eval_dir))
    meta["eval_dir"] = os.path.abspath(eval_dir)
    sessions = Counter(r["session"] for r in rows)
    meta.setdefault("sessions", dict(sorted(sessions.items())))
    meta.setdefault("frames", sum(1 for _ in rows))
    meta.setdefault("conditions", ordered_conditions(rows))
    meta.setdefault("arms", ordered_arms(rows))
    meta.setdefault("temporal_confirm_frames",
                    ((summary or {}).get("meta") or {}).get("temporal_confirm_frames"))
    meta.setdefault("evidence_level", "DESKTOP_REAL_DATA")
    return meta


def write_tables(out_dir: str, agg: Dict[str, Any], meta: Dict[str, Any],
                 warnings: Sequence[str],
                 frame23_keys: Sequence[Tuple[str, int]]) -> Tuple[str, str]:
    """Write ``report_tables.md`` and ``report_tables.csv``; return both paths."""
    md_path = os.path.join(out_dir, "report_tables.md")
    csv_path = os.path.join(out_dir, "report_tables.csv")
    with open(md_path, "w", encoding="utf-8") as fh:
        fh.write(build_tables_md(agg, meta, warnings, frame23_keys))
    rows = build_tables_csv_rows(agg)
    fields = ["condition", "scope", "arm", "metric", "numerator", "denominator", "rate",
              "definition"]
    with open(csv_path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    return md_path, csv_path


def write_report_body(out_dir: str, agg: Dict[str, Any], meta: Dict[str, Any],
                      warnings: Sequence[str],
                      failures_manifest: Optional[Sequence[Dict[str, Any]]],
                      frame23_keys: Sequence[Tuple[str, int]]) -> str:
    path = os.path.join(out_dir, "report_body.md")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(build_report_body(agg, meta, warnings, failures_manifest, frame23_keys))
    return path


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        description="Report tables + PR body + failure overlays for the negative-evidence "
                    "fusion eval (reads per_frame.csv + summary.json; no model inference).",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out-dir", required=True,
                    help="eval output dir produced by corridor_evidence_eval.py "
                         "(per_frame.csv + summary.json)")
    ap.add_argument("--out", default=None,
                    help="report output dir (default: the eval output dir)")
    ap.add_argument("--save-failures", action="store_true",
                    help="render >=5 remaining failing frames (raw RGB + overlay + manifest)")
    ap.add_argument("--failures-max", type=int, default=10,
                    help="maximum failure frames to save (default 10, >=5)")
    ap.add_argument("--failures-dir", default=None,
                    help="failure output dir (default: <out>/failures)")
    ap.add_argument("--data-root", default=None,
                    help="SANPO-Real root for failure frames (default: summary.json meta)")
    ap.add_argument("--camera", default="camera_chest")
    ap.add_argument("--max-width", type=int, default=640,
                    help="SANPO image width used by the eval harness (default 640)")
    ap.add_argument("--max-failures-mismatch", type=int, default=2,
                    help="reserved; kept for CLI symmetry with the eval tool")
    return ap.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    eval_dir = os.path.abspath(args.out_dir)
    out_dir = os.path.abspath(args.out or args.out_dir)
    rows, summary = load_eval_dir(eval_dir)
    meta = build_meta(rows, summary, eval_dir)
    frame23_keys = pr15_23_frame_set(rows)
    agg = aggregate(rows, frame23_keys)
    warnings = contract_warnings(rows, frame23_keys) + cross_check(agg, summary)

    os.makedirs(out_dir, exist_ok=True)
    md_path, csv_path = write_tables(out_dir, agg, meta, warnings, frame23_keys)

    failures_manifest: List[Dict[str, Any]] = []
    if args.save_failures:
        data_root = args.data_root or meta.get("data_root")
        if not data_root:
            # documented local fallback; relative to the pc/ root
            data_root = os.path.join(_PC_ROOT, "data", "sanpo_real", "sanpo-real")
        failures_dir = os.path.abspath(args.failures_dir or os.path.join(out_dir, "failures"))
        failures_manifest = save_failures(
            rows, failures_dir, str(data_root), camera=args.camera,
            max_width=args.max_width, max_total=int(args.failures_max))

    body_path = write_report_body(out_dir, agg, meta, warnings, failures_manifest,
                                  frame23_keys)

    checks = "OK" if not warnings else f"{len(warnings)} warning(s)"
    print(f"frames={len(rows)} conditions={agg['conditions']} arms={agg['arms']} "
          f"pr15_23_set={len(frame23_keys)} contract_checks={checks}")
    for cond in agg["conditions"]:
        parts = []
        for arm in agg["arms"]:
            m = agg["overall"].get((cond, arm))
            if m is None:
                continue
            dh = m["danger_handling"]
            parts.append(f"{arm}: false-free {_nd_pct(m['false_free'])} "
                         f"false-stop {_nd_pct(m['false_stop'])} "
                         f"UNKNOWN {_nd_pct(m['unknown'])} "
                         f"danger-recall {_pct(dh['blocking_recall'])}")
        print(f"  {cond}: " + " | ".join(parts))
    for warning in warnings:
        print(f"  warning: {warning}")
    print(f"wrote {md_path}")
    print(f"wrote {csv_path}")
    if failures_manifest:
        print(f"wrote {len(failures_manifest)} failure frames under "
              f"{os.path.abspath(args.failures_dir or os.path.join(out_dir, 'failures'))}")
    print(f"wrote {body_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
