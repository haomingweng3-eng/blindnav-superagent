"""Tests for the independent phone-clip re-test tool's pure helpers (no video/model inference).

The key contract under test is honesty of the label-capability matrix: metrics that need
frame-level GT must be NOT_EVALUABLE on video-level annotations, and GT-free output statistics
must be reported as PROXY, never as safety metrics.
"""
import csv
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "tools")))

import phone_clip_retest as pcr  # noqa: E402


def _write_annotations(path, rows):
    fields = ["video_file", "status", "target", "approx_time_s", "notes"]
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def test_read_annotations_handles_bom_and_optional_onset(tmp_path):
    path = tmp_path / "ann.csv"
    _write_annotations(path, [
        {"video_file": "a.mp4", "status": "safe", "target": "person",
         "approx_time_s": "", "notes": "ok"},
        {"video_file": "b.mp4", "status": "near_miss", "target": "person",
         "approx_time_s": "2.5", "notes": "avoided"},
        {"video_file": "c.mp4", "status": "conflict", "target": "multiple",
         "approx_time_s": "3.0", "notes": "in path"},
    ])
    rows = pcr.read_annotations(str(path))
    assert [r["status"] for r in rows] == ["safe", "near_miss", "conflict"]
    assert rows[0]["approx_time_s"] is None
    assert rows[1]["approx_time_s"] == pytest.approx(2.5)


def test_read_annotations_rejects_unknown_status(tmp_path):
    path = tmp_path / "ann.csv"
    _write_annotations(path, [{"video_file": "a.mp4", "status": "unsure", "target": "",
                               "approx_time_s": "", "notes": ""}])
    with pytest.raises(ValueError):
        pcr.read_annotations(str(path))


def test_label_capability_marks_gt_metrics_not_evaluable():
    by_metric = {item["metric"]: item["status"] for item in pcr.LABEL_CAPABILITY}
    for metric in ("curb/road danger miss", "3D occupancy danger miss",
                   "true false-stop (confirmed clear path)",
                   "latency to first hazard warning"):
        assert by_metric[metric] == "NOT_EVALUABLE"
    for metric in ("effective guidance coverage", "UNKNOWN share", "decision flips"):
        assert by_metric[metric] == "PROXY"


def _rows():
    rows = []
    for arm in ("A", "B", "C"):
        rows.extend([
            {"clip": "near.mp4", "status": "near_miss", "target": "person",
             "approx_time_s": 2.5, "t_s": 0.0, "frame": 0, "arm": arm,
             "effective_command": "STRAIGHT", "safety_state": "CLEAR",
             "forward_guidance": True, "warning": False, "calib_ok": True},
            {"clip": "near.mp4", "status": "near_miss", "target": "person",
             "approx_time_s": 2.5, "t_s": 0.5, "frame": 1, "arm": arm,
             "effective_command": "STOP", "safety_state": "BLOCKED",
             "forward_guidance": False, "warning": True, "calib_ok": True},
            {"clip": "near.mp4", "status": "near_miss", "target": "person",
             "approx_time_s": 2.5, "t_s": 1.0, "frame": 2, "arm": arm,
             "effective_command": "UNCLEAR", "safety_state": "UNKNOWN",
             "forward_guidance": False, "warning": True, "calib_ok": False},
            {"clip": "safe.mp4", "status": "safe", "target": "",
             "approx_time_s": None, "t_s": 0.0, "frame": 0, "arm": arm,
             "effective_command": "STRAIGHT", "safety_state": "CLEAR",
             "forward_guidance": True, "warning": False, "calib_ok": True},
        ])
    return rows


def test_summarize_clips_reports_proxy_shares_per_arm():
    summary = pcr.summarize_clips(_rows(), ("A", "B", "C"), 0.5)
    b = summary["B"]
    assert b["frames"] == 4 and b["clips"] == 2
    assert b["guidance_coverage"]["numerator"] == 2
    assert b["guidance_coverage"]["denominator"] == 4
    assert b["warning_share"]["numerator"] == 2
    assert b["unknown_share"]["numerator"] == 1
    assert b["blocked_share"]["numerator"] == 1
    assert b["per_status"]["safe"]["frames"] == 1
    assert b["per_clip"]["near.mp4"]["first_warning_t_s"] == pytest.approx(0.5)


def test_descriptive_proxy_only_covers_near_miss_and_conflict():
    rows = _rows()
    proxy = pcr.descriptive_onset_proxy(rows, ("A", "B", "C"))
    assert {row["clip"] for row in proxy} == {"near.mp4"}
    assert all(row["note"].startswith("PROXY") for row in proxy)
    for row in proxy:
        assert row["first_warning_t_s"] == pytest.approx(0.5)
        assert row["warning_before_or_at_note"] is True


def test_render_md_states_capability_before_numbers():
    summary = pcr.summarize_clips(_rows(), ("A", "B", "C"), 0.5)
    md = pcr.render_md(summary, pcr.LABEL_CAPABILITY,
                       pcr.descriptive_onset_proxy(_rows(), ("A", "B", "C")),
                       {"clips": 2, "frames": 4, "sample_fps": 2.0, "safe_clips": 1,
                        "near_miss_clips": 1, "conflict_clips": 0}, ("A", "B", "C"))
    capability_pos = md.index("Label capability is stated before any number")
    proxy_table_pos = md.index("GT-free output statistics")
    assert capability_pos < proxy_table_pos
    assert "NOT_EVALUABLE" in md and "PROTOTYPE" in md
    assert "NOT a latency metric" in md
