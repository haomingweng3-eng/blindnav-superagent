"""Offline Last10m proposal metrics.  Synthetic fixtures only; no Entrances download."""
import json
import os
import subprocess
import sys

import pytest

from blindnav_pc.eval_last10m import (EvalCase, parse_json_cases, parse_yolo_cases, run_eval,
                                      score_cases, synthetic_cases)
from blindnav_pc.last10m import MAX_CANDIDATES, DoorCandidate

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def _door(distance, confidence=0.9, gt=True, cx=0.5):
    door = DoorCandidate(0, cx, 0.8, 0.1, 0.2, confidence=confidence, distance_m=distance)
    return door, gt


def _case(name, rows, gt_available=True):
    doors, flags = [], []
    for index, (distance, confidence, is_gt, cx) in enumerate(rows):
        door, flag = _door(distance, confidence, is_gt, cx=0.2 + 0.05 * index if cx is None else cx)
        doors.append(door)
        flags.append(flag)
    return EvalCase(name, tuple(doors), tuple(flags), gt_available)


def _assert_safety_report(metrics, summary):
    assert metrics["auto_go"] is False
    assert metrics["limit_enforced"] is True
    assert metrics["limit_violations"] == 0
    assert metrics["max_candidate_count"] <= MAX_CANDIDATES
    assert metrics["w1_curb_recall_gate"] == "not_claimed"
    assert metrics["w1_near_field_recall_gate"] == "not_claimed"
    assert metrics["require_distance"] is True
    assert "CROSS" not in summary
    assert "可以过街" not in summary
    assert "已经通过" not in summary
    assert "路缘" in summary and "尚未" in summary
    assert "拒答" in summary


def test_synthetic_fixture_enforces_limit_and_missing_distance(tmp_path):
    out = tmp_path / "out"
    metrics = run_eval("synthetic", str(out))
    summary = (out / "summary.md").read_text(encoding="utf-8")
    _assert_safety_report(metrics, summary)
    assert metrics["mode"] == "synthetic_fixture"
    assert metrics["fallback"] is False
    assert metrics["n_cases"] == len(synthetic_cases())
    assert metrics["max_candidate_count"] == MAX_CANDIDATES
    assert all(count <= MAX_CANDIDATES for count in metrics["candidate_count_after_filter"])
    assert metrics["missing_distance_refuse_correct_rate"] == pytest.approx(1.0)
    assert metrics["distance_bin_refuse_correct_rate"] == pytest.approx(1.0)
    assert metrics["distance_bins"]["missing"]["n"] == 2
    assert metrics["top_k_scored"] == 5
    assert metrics["top_k_hits"] == 4
    assert metrics["top_k_hit_rate"] == pytest.approx(0.8)
    assert metrics["refuse_rate"] == pytest.approx(6 / 11)
    by_name = {row["name"]: row for row in metrics["cases"]}
    assert by_name["missing_only"]["refused"] is True
    assert by_name["missing_only"]["reason"] == "distance_unknown"
    assert by_name["mixed_missing"]["reason"] == "distance_unknown"
    assert by_name["mixed_missing"]["correct"] is True
    assert by_name["six_clamp"]["candidate_count_after_filter"] == 3
    assert by_name["gt_beyond_cap"]["top_k_hit"] == 0
    assert by_name["five_near"]["top_k_hit"] == 1
    assert by_name["ambiguous_cutoff"]["reason"] == "ambiguous"
    assert by_name["too_far"]["reason"] == "out_of_range"
    assert by_name["empty"]["reason"] == "no_candidate"
    assert "门候选" in summary
    written = json.loads((out / "metrics.json").read_text(encoding="utf-8"))
    assert written["max_candidates_limit"] == 3


def test_nine_doors_stay_within_three_and_hit_the_nearest_gt():
    rows = [(float(d), 0.9, True, None) for d in range(1, 10)]
    scored = score_cases([_case("nine", rows)])
    assert scored["max_candidate_count"] == 3
    assert scored["limit_violations"] == 0
    assert scored["cases"][0]["candidate_count_after_filter"] == 3
    assert scored["cases"][0]["refused"] is False
    assert scored["top_k_hit_rate"] == pytest.approx(1.0)
    assert scored["cases"][0]["bin"] == "0_5m"


def test_missing_distance_must_refuse_even_with_a_near_door():
    case = _case("mixed", [
        (3.0, 0.95, True, 0.4),
        (None, 0.80, True, 0.6),
    ])
    scored = score_cases([case])
    row = scored["cases"][0]
    assert row["refused"] is True
    assert row["reason"] == "distance_unknown"
    assert row["candidate_count_after_filter"] == 0
    assert row["bin"] == "missing"
    assert row["correct"] is True
    assert scored["missing_distance_refuse_correct_rate"] == pytest.approx(1.0)
    assert scored["top_k_scored"] == 0
    assert scored["top_k_hit_rate"] is None


def test_distance_bins_for_far_near_and_low_confidence():
    cases = [
        _case("near", [(4.0, 0.9, True, 0.5)]),
        _case("mid", [(7.5, 0.9, True, 0.5)]),
        _case("far", [(11.0, 0.9, True, 0.5), (14.0, 0.8, True, 0.4)]),
        _case("low", [(2.0, 0.2, True, 0.5)]),
    ]
    scored = score_cases(cases)
    bins = {row["name"]: row["bin"] for row in scored["cases"]}
    assert bins == {"near": "0_5m", "mid": "5_10m", "far": "gt_10m", "low": "no_eligible"}
    assert scored["cases"][2]["reason"] == "out_of_range"
    assert scored["cases"][3]["reason"] == "low_confidence"
    assert scored["distance_bin_refuse_correct_rate"] == pytest.approx(1.0)
    assert scored["top_k_hits"] == 2
    assert scored["top_k_scored"] == 2


def test_topk_ignores_cases_without_ground_truth():
    hidden = _case("no_gt", [(2.0, 0.9, True, 0.5), (4.0, 0.8, True, 0.4)], gt_available=False)
    scored = score_cases([hidden])
    assert scored["cases"][0]["refused"] is False
    assert scored["cases"][0]["candidate_count_after_filter"] == 2
    assert scored["top_k_scored"] == 0
    assert scored["top_k_hit_rate"] is None


def test_yolo_directory_and_json_file(tmp_path):
    labels = tmp_path / "labels"
    nested = labels / "val" / "labels"
    nested.mkdir(parents=True)
    (nested / "missing.txt").write_text(
        "0 0.167969 0.600260 0.050781 0.156250\n"
        "# comment\n"
        "bad\n"
        "0 0.5 0.7 0.1 0.2 0.4\n",
        encoding="utf-8")
    (nested / "near.txt").write_text(
        "0 0.50 0.80 0.10 0.20 0.95 1.5 1\n"
        "0 0.40 0.70 0.10 0.18 0.90 4.0 1\n"
        "0 0.30 0.60 0.08 0.12 0.80 8.0 0\n",
        encoding="utf-8")
    (labels / "extra.json").write_text(json.dumps({
        "name": "json_far",
        "candidates": [
            {"class_id": 0, "cx": 0.5, "cy": 0.5, "w": 0.1, "h": 0.1,
             "confidence": 0.9, "distance_m": 12.0, "gt": True}
        ],
    }), encoding="utf-8")

    metrics = run_eval(str(labels), str(tmp_path / "out"))
    assert metrics["mode"] == "labels_dir"
    assert metrics["fallback"] is False
    by_name = {row["name"]: row for row in metrics["cases"]}
    assert set(by_name) == {"missing", "near", "json_far"}
    assert by_name["missing"]["reason"] == "distance_unknown"
    assert by_name["missing"]["correct"] is True
    assert by_name["missing"]["top_k_hit"] is None
    assert by_name["near"]["candidate_count_after_filter"] == 3
    assert by_name["near"]["top_k_hit"] == 1
    assert by_name["near"]["bin"] == "0_5m"
    assert by_name["json_far"]["reason"] == "out_of_range"
    assert metrics["max_candidate_count"] <= 3
    assert metrics["missing_distance_refuse_correct_rate"] == pytest.approx(1.0)

    single = tmp_path / "one.txt"
    single.write_text("0 0.5 0.8 0.2 0.2\n", encoding="utf-8")
    one = run_eval(str(single), str(tmp_path / "one"))
    assert one["mode"] == "labels_file"
    assert one["n_cases"] == 1
    assert one["cases"][0]["reason"] == "distance_unknown"
    assert one["cases"][0]["candidate_count_after_filter"] == 0
    assert parse_yolo_cases(str(single))[0].gt_available is True

    no_gt = tmp_path / "no_gt.json"
    no_gt.write_text(json.dumps({
        "gt_available": False,
        "candidates": [
            {"cx": 0.5, "cy": 0.8, "w": 0.1, "h": 0.2, "confidence": 0.9, "distance_m": 2.0}
        ],
    }), encoding="utf-8")
    parsed = parse_json_cases(str(no_gt))
    assert parsed[0].gt_available is False
    hidden = run_eval(str(no_gt), str(tmp_path / "hidden"))
    assert hidden["top_k_hit_rate"] is None
    assert hidden["cases"][0]["candidate_count_after_filter"] == 1


def test_missing_labels_fall_back_to_synthetic_fixture(tmp_path):
    missing = tmp_path / "entrances" / "labels"
    out = tmp_path / "out"
    metrics = run_eval(str(missing), str(out))
    summary = (out / "summary.md").read_text(encoding="utf-8")
    assert metrics["mode"] == "synthetic_fallback"
    assert metrics["labels_absent"] is True
    assert metrics["n_cases"] == 11
    assert metrics["limit_enforced"] is True
    assert metrics["missing_distance_refuse_correct_rate"] == pytest.approx(1.0)
    assert "合成" in summary
    assert "Entrances" in summary
    assert (out / "metrics.json").is_file()
    _assert_safety_report(metrics, summary)


def test_empty_directory_writes_a_report(tmp_path):
    labels = tmp_path / "empty"
    labels.mkdir()
    metrics = run_eval(str(labels), str(tmp_path / "out"))
    assert metrics["mode"] == "labels_dir"
    assert metrics["n_cases"] == 0
    assert metrics["refuse_rate"] is None
    assert metrics["top_k_hit_rate"] is None
    assert metrics["max_candidate_count"] == 0
    assert metrics["limit_enforced"] is True
    summary = (tmp_path / "out" / "summary.md").read_text(encoding="utf-8")
    assert "没有读到" in summary


def test_cli_module_writes_report(tmp_path):
    out = tmp_path / "cli"
    proc = subprocess.run(
        [sys.executable, "-m", "blindnav_pc.eval_last10m",
         "--labels", "synthetic", "--out", str(out)],
        cwd=ROOT, check=False, capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert "max_candidates=3" in proc.stdout
    assert (out / "metrics.json").is_file()
    assert (out / "summary.md").is_file()
    bad = tmp_path / "bad.json"
    bad.write_text("{", encoding="utf-8")
    failed = subprocess.run(
        [sys.executable, "-m", "blindnav_pc.eval_last10m",
         "--labels", str(bad), "--out", str(tmp_path / "bad_out")],
        cwd=ROOT, check=False, capture_output=True, text=True,
    )
    assert failed.returncode == 2
