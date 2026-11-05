"""Tests for the dangerous-pass layer attribution helpers (no dataset or model inference)."""
import csv
import os
import sys
from collections import Counter

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "tools")))

import responsibility_split_attribution as rsa  # noqa: E402

PER_FRAME_FIELDS = ["condition", "arm", "session", "frame", "effective_command",
                    "safety_state", "reason", "forward_guidance", "gt_road_in_path",
                    "gt_curb_in_path", "gt_occupancy_in_path"]


def _write(path, rows):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=PER_FRAME_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _row(arm="C", condition="production", session="s1", frame=0, forward="True",
         road="False", curb="False", occ="False"):
    return {"condition": condition, "arm": arm, "session": session, "frame": str(frame),
            "effective_command": "STRAIGHT", "safety_state": "CLEAR", "reason": "centred",
            "forward_guidance": forward, "gt_road_in_path": road, "gt_curb_in_path": curb,
            "gt_occupancy_in_path": occ}


def test_read_miss_frames_selects_only_forward_guidance_over_danger(tmp_path):
    path = tmp_path / "pf.csv"
    _write(path, [
        _row(frame=0, occ="True"),                       # miss
        _row(frame=1, occ="True", forward="False"),      # already blocked -> not a miss
        _row(frame=2, road="True", curb="True"),         # miss, both kinds
        _row(frame=3),                                   # no danger -> not a miss
        _row(frame=4, occ="True", arm="A"),              # other arm
        _row(frame=5, occ="True", condition="annotated"),  # other condition
    ])
    miss = rsa.read_miss_frames(str(path), "C", "production")
    assert set(miss) == {("s1", 0), ("s1", 2)}
    assert miss[("s1", 0)]["kinds"] == {"occupancy"}
    assert miss[("s1", 2)]["kinds"] == {"road", "curb"}


def test_aggregate_layer_shares():
    per_frame = [
        {"attribution": Counter({"occupancy:segmentation": 3, "occupancy:geometry": 1}),
         "class_at_gt": Counter({"occupancy:safe": 3, "occupancy:non": 1}),
         "n_gt_pixels": Counter({"occupancy": 4}), "frame": 0, "session": "s1"},
        {"skipped": "no_gt_plane"},
        {"attribution": Counter({"curb:decision": 2}), "class_at_gt": Counter(),
         "n_gt_pixels": Counter({"curb": 2}), "frame": 2, "session": "s1"},
    ]
    agg = rsa.aggregate(per_frame, [("s1", 0), ("s1", 1), ("s1", 2)])
    assert agg["frames_requested"] == 3
    assert agg["frames_attributed"] == 2 and agg["frames_skipped"] == 1
    assert agg["occupancy_layer_share"]["segmentation"] == pytest.approx(0.75)
    assert agg["occupancy_layer_share"]["geometry"] == pytest.approx(0.25)
    assert agg["curb_layer_share"]["decision"] == pytest.approx(1.0)


def test_render_md_marks_unavailable_kind_not_evaluable():
    agg = rsa.aggregate([{"attribution": Counter({"occupancy:decision": 1}),
                          "class_at_gt": Counter(), "n_gt_pixels": Counter({"occupancy": 1}),
                          "frame": 0, "session": "s1"}], [("s1", 0)])
    md = rsa.render_md(agg, "C")
    assert "occupancy | 1" in md
    assert "NOT_EVALUABLE" in md          # road/curb have no pixels here
    assert "decision layer still passed" in md
