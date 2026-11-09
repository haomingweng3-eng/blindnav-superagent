"""Tests for the rebuilt Issue #18 measurement definitions (no model or dataset dependency).

Pins: the miss / blocking / clear-path flag semantics, NOT_EVALUABLE status, low-sample flag,
flips, hazard-onset latency, per-session grouping and the metric CSV/markdown rendering.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "tools")))

import responsibility_split_metrics as rsm  # noqa: E402


def _row(command="STRAIGHT", state="CLEAR", session="s1", frame=0, *, road=False, curb=False,
         ground=False, occupancy=False, any_danger=False, confirmed_clear=False,
         clear_weak=False, hazard_event=False):
    return {
        "session": session, "frame": frame, "condition": "annotated", "arm": "B",
        "effective_command": command, "safety_state": state,
        "gt_road_in_path": road, "gt_curb_in_path": curb, "danger_ground": ground,
        "gt_occupancy_in_path": occupancy, "danger_any": any_danger,
        "confirmed_clear": confirmed_clear, "clear_weak": clear_weak,
        "gt_hazard_event": hazard_event,
    }


# ---------------------------------------------------------------------------
# Flag semantics
# ---------------------------------------------------------------------------
def test_forward_guidance_over_danger_is_a_conservative_miss_but_turn_is_not_evaluable():
    straight = rsm.add_row_flags(_row("STRAIGHT", "CLEAR", road=True))
    assert straight["road_miss"] and straight["road_miss_straight"]
    assert not straight["road_turn_guidance_unevaluable"]
    assert straight["road_in_path"]
    left = rsm.add_row_flags(_row("LEFT", "CLEAR", road=True))
    assert left["road_miss"] and left["road_turn_guidance_unevaluable"]
    assert not left["road_miss_straight"]


def test_warning_command_is_not_a_miss():
    stop = rsm.add_row_flags(_row("STOP", "BLOCKED", curb=True, ground=True))
    assert not stop["curb_miss"] and stop["danger_blocked_curb"]
    assert not stop["ground_miss"] and stop["danger_blocked_ground"]
    assert stop["warning"] and stop["is_blocked"]


def test_true_false_stop_requires_confirmed_clear_path():
    clear_stop = rsm.add_row_flags(_row("STOP", "BLOCKED", confirmed_clear=True))
    assert clear_stop["true_false_stop"]
    danger_stop = rsm.add_row_flags(_row("STOP", "BLOCKED", confirmed_clear=False))
    assert not danger_stop["true_false_stop"]


def test_observe_and_unknown_on_confirmed_clear_are_counted_separately():
    observe = rsm.add_row_flags(_row("OBSERVE", "OBSERVE", confirmed_clear=True))
    assert observe["unnecessary_observe"] and not observe["true_false_stop"]
    unknown = rsm.add_row_flags(_row("UNKNOWN", "UNKNOWN", confirmed_clear=True))
    assert unknown["unknown_on_confirmed_clear"]


# ---------------------------------------------------------------------------
# metric() status / low sample
# ---------------------------------------------------------------------------
def test_metric_marks_not_evaluable_with_no_denominator_frames():
    rows = [rsm.add_row_flags(_row())]
    value = rsm.metric(rows, rows, lambda r: True, lambda r: r["road_in_path"])
    assert value["status"] == "NOT_EVALUABLE"
    assert value["denominator"] == 0 and value["numerator"] == 0 and value["rate"] is None


def test_metric_low_sample_flag():
    rows = [rsm.add_row_flags(_row(confirmed_clear=True)) for _ in range(3)]
    value = rsm.metric(rows, rows, lambda r: r["true_false_stop"],
                       lambda r: r["confirmed_clear"])
    assert value["status"] == "EVALUABLE" and value["low_sample"]


def test_value_metric_status():
    assert rsm.value_metric(None, 0, "frames")["status"] == "NOT_EVALUABLE"
    value = rsm.value_metric(2.0, 5, "frames")
    assert value["status"] == "EVALUABLE" and value["value"] == 2.0
    assert value["low_sample"]


# ---------------------------------------------------------------------------
# flips
# ---------------------------------------------------------------------------
def test_flips_count_consecutive_command_changes_per_session():
    rows = [rsm.add_row_flags(r) for r in [
        _row("STRAIGHT", frame=0), _row("STOP", "BLOCKED", frame=1),
        _row("STOP", "BLOCKED", frame=2), _row("LEFT", frame=3)]]
    stats = rsm.summarize_rows(rows, {"s1": 0.33})
    flips = stats["metrics"]["flips"]
    assert flips["numerator"] == 2 and flips["denominator"] == 3
    assert stats["metrics"]["flips_per_minute"]["value"] > 0


def test_flips_do_not_cross_session_boundaries():
    rows = [rsm.add_row_flags(r) for r in [
        _row("STRAIGHT", frame=0, session="s1"),
        _row("STOP", "BLOCKED", frame=0, session="s2")]]
    stats = rsm.summarize_rows(rows, {"s1": 0.33, "s2": 0.33})
    assert stats["metrics"]["flips"]["denominator"] == 0
    assert stats["metrics"]["flips"]["status"] == "NOT_EVALUABLE"


# ---------------------------------------------------------------------------
# latency to first hazard warning
# ---------------------------------------------------------------------------
def test_latency_from_hazard_onset_to_first_warning():
    rows = [rsm.add_row_flags(r) for r in [
        _row("STRAIGHT", frame=0, hazard_event=False),
        _row("STRAIGHT", frame=1, hazard_event=True),     # onset, no warning
        _row("STOP", "BLOCKED", frame=2, hazard_event=True),  # warning 1 frame later
        _row("STRAIGHT", frame=3, hazard_event=False),
        _row("OBSERVE", "OBSERVE", frame=4, hazard_event=True),  # onset, warned at 0
    ]]
    stats = rsm.summarize_rows(rows, {"s1": 0.33})
    m = stats["metrics"]
    assert m["hazard_onset_events"]["value"] == 2
    assert m["warning_within_horizon"]["numerator"] == 2
    assert m["warning_at_onset"]["numerator"] == 1
    assert m["latency_p50_frames"]["value"] == pytest.approx(0.5)
    assert m["latency_p50_ms"]["value"] == pytest.approx(165.0)


def test_latency_not_evaluable_without_hazard_events():
    rows = [rsm.add_row_flags(_row(frame=i)) for i in range(4)]
    stats = rsm.summarize_rows(rows, {"s1": 0.33})
    assert stats["metrics"]["warning_within_horizon"]["status"] == "NOT_EVALUABLE"
    assert stats["metrics"]["latency_p50_frames"]["status"] == "NOT_EVALUABLE"


def test_warning_beyond_horizon_counts_as_unwarned():
    rows = [rsm.add_row_flags(r) for r in
            ([_row("STRAIGHT", frame=0, hazard_event=True)]
             + [_row("STRAIGHT", frame=i, hazard_event=True) for i in range(1, 15)]
             + [_row("STOP", "BLOCKED", frame=15, hazard_event=True)])]
    stats = rsm.summarize_rows(rows, {"s1": 0.33}, horizon_frames=5)
    assert stats["metrics"]["warning_within_horizon"]["numerator"] == 0


# ---------------------------------------------------------------------------
# aggregation / rendering
# ---------------------------------------------------------------------------
def test_per_session_grouping_and_csv_rendering():
    rows = [rsm.add_row_flags(_row(frame=i, session=sid, road=(i == 0)))
            for sid in ("s1", "s2") for i in range(3)]
    summary = rsm.summarize(rows, ["annotated"], ["A", "B"], {"s1": 0.33, "s2": 0.33})
    assert set(summary) == {"annotated/B"}
    block = summary["annotated/B"]
    assert set(block["per_session"]) == {"s1", "s2"}
    assert block["metrics"]["road_miss"]["denominator"] == 2
    csv_rows = rsm.metrics_csv_rows(summary, ["annotated"], ["B"])
    scopes = {(r["scope"]) for r in csv_rows}
    assert {"all", "s1", "s2"} <= scopes
    assert all("status" in r for r in csv_rows)


def test_markdown_marks_not_evaluable_metrics():
    rows = [rsm.add_row_flags(_row(frame=0))]
    summary = rsm.summarize(rows, ["annotated"], ["A"], {"s1": 0.33})
    md = rsm.render_summary_md(summary, {"frames": 1, "sessions": {"s1": 1},
                                         "arm_description": {"A": "a", "B": "b", "C": "c"}},
                               ["annotated"], ["A"])
    assert "NOT_EVALUABLE" in md
    assert "DESKTOP_REAL_DATA" in md
