"""Three-state traffic light tests: ImVisible labels, HSV baseline, no crossing permit."""
import json

import numpy as np
import pytest

from blindnav_pc.eval_traffic_light import apply_green_gate, run_eval, score_states
from blindnav_pc.traffic_light import (GREEN, RED, STATES, TEXT, UNCERTAIN, GO_AHEAD_TOKENS,
                                       TrafficLightDecision, TrafficLightFilter,
                                       TrafficLightObservation, TrafficLightThreeState,
                                       imvisible_mode_to_state, imvisible_row_to_observation,
                                       label_to_state, parse_imvisible_csv, perception_output,
                                       read_imvisible_rows)


@pytest.mark.parametrize("mode,state", [(0, RED), (1, GREEN), (2, GREEN), (3, UNCERTAIN),
                                        (4, UNCERTAIN), (99, UNCERTAIN), ("1", GREEN), (None, UNCERTAIN)])
def test_imvisible_mode_mapping(mode, state):
    assert imvisible_mode_to_state(mode) == state


def test_observation_rejects_unknown_state_and_bad_confidence():
    with pytest.raises(ValueError):
        TrafficLightObservation("go")
    with pytest.raises(ValueError):
        TrafficLightObservation(GREEN, confidence=1.5)


def test_imvisible_row_observation():
    obs = imvisible_row_to_observation({"file": "x.jpg", "mode": "0", "block": "blocked"})
    assert obs.state == RED
    assert obs.source == "imvisible:x.jpg"


def test_single_observation_is_uncertain_until_confirmed():
    filt = TrafficLightFilter(min_confirm_frames=3)
    first = filt.update(TrafficLightObservation(GREEN))
    assert first.state == UNCERTAIN and not first.stable and first.reason == "confirming"
    second = filt.update(TrafficLightObservation(GREEN))
    assert second.state == UNCERTAIN
    third = filt.update(TrafficLightObservation(GREEN))
    assert third.state == GREEN and third.stable and third.reason == "confirmed"
    assert third.frames == 3


def test_green_never_auto_goes():
    filt = TrafficLightFilter(min_confirm_frames=1)
    decision = filt.update(TrafficLightObservation(GREEN))
    assert decision.state == GREEN
    assert decision.auto_go is False
    assert decision.requires_user_confirmation is True
    assert "过街" in decision.text


def test_disagreement_resets_confirmation():
    filt = TrafficLightFilter(min_confirm_frames=2)
    assert filt.update(TrafficLightObservation(GREEN)).state == UNCERTAIN
    assert filt.update(TrafficLightObservation(RED)).state == UNCERTAIN
    assert filt.update(TrafficLightObservation(RED)).state == RED


def test_low_confidence_and_uncertain_observations_never_confirm():
    filt = TrafficLightFilter(min_confirm_frames=2, min_confidence=0.8)
    filt.update(TrafficLightObservation(GREEN))
    low = filt.update(TrafficLightObservation(GREEN, confidence=0.5))
    assert low.state == UNCERTAIN and low.reason == "low_confidence"
    assert filt.update(TrafficLightObservation(GREEN)).reason == "confirming"
    assert filt.update(TrafficLightObservation(UNCERTAIN)).reason == "uncertain_observation"
    assert filt.update(None).reason == "no_observation"


def test_read_imvisible_rows(tmp_path):
    p = tmp_path / "labels.csv"
    p.write_text("﻿file,mode,x1,y1,x2,y2,block\n"
                 "a.jpg,1,10,20,30,40,not_blocked\n"
                 "b.jpg,4,1,2,3,4,blocked\n", encoding="utf-8")
    rows = read_imvisible_rows(str(p))
    assert [r["file"] for r in rows] == ["a.jpg", "b.jpg"]
    assert imvisible_row_to_observation(rows[0]).state == GREEN
    assert imvisible_row_to_observation(rows[1]).state == UNCERTAIN


@pytest.mark.parametrize("label,state", [
    ("red", RED), ("GREEN", GREEN), ("countdown_green", GREEN), ("countdown green", GREEN),
    ("countdown_blank", UNCERTAIN), ("countdown-blank", UNCERTAIN), ("none", UNCERTAIN),
    ("unknown", UNCERTAIN), ("occluded-heavy", UNCERTAIN), ("occluded_heavy", UNCERTAIN),
    ("0", RED), ("2", GREEN), ("4", UNCERTAIN), (2, GREEN), ("", UNCERTAIN),
])
def test_text_label_mapping(label, state):
    assert label_to_state(label) == state


@pytest.mark.parametrize("label", list(GO_AHEAD_TOKENS) + ["CROSS", "Go-Ahead", "可以过街", "现在走"])
def test_go_ahead_labels_collapse_to_uncertain(label):
    assert label_to_state(label) == UNCERTAIN
    assert label_to_state(label) in STATES


def test_parse_imvisible_csv_numeric_and_text(tmp_path):
    numeric = tmp_path / "modes.csv"
    numeric.write_text("file,mode,x1,y1,x2,y2,block\n"
                       "series/a.jpg,0,1,2,3,4,blocked\n"
                       "series/b.jpg,2,1,2,3,4,not_blocked\n"
                       "series/c.jpg,3,1,2,3,4,blocked\n", encoding="utf-8")
    rows = parse_imvisible_csv(str(numeric))
    assert [(r.image_id, r.path, r.gt_state, r.raw_label) for r in rows] == [
        ("a.jpg", "series/a.jpg", RED, "0"),
        ("b.jpg", "series/b.jpg", GREEN, "2"),
        ("c.jpg", "series/c.jpg", UNCERTAIN, "3"),
    ]
    text = tmp_path / "labels.csv"
    text.write_text("image_id,path,label\n"
                    "id1,frame1.jpg,countdown_green\n"
                    "id2,frame2.jpg,none\n"
                    "id3,frame3.jpg,red\n"
                    "id4,frame4.jpg,CROSS\n", encoding="utf-8")
    parsed = parse_imvisible_csv(str(text))
    assert [r.gt_state for r in parsed] == [GREEN, UNCERTAIN, RED, UNCERTAIN]
    assert parsed[0].image_id == "id1" and parsed[0].path == "frame1.jpg"
    assert all(r.gt_state in STATES for r in parsed)


def test_disagreeing_label_and_mode_is_uncertain(tmp_path):
    path = tmp_path / "both.csv"
    path.write_text("file,label,mode\nframe.jpg,green,0\n", encoding="utf-8")
    record = parse_imvisible_csv(str(path))[0]
    assert record.raw_label == "green"
    assert record.gt_state == UNCERTAIN


def _solid(color, size=24):
    frame = np.zeros((size, size, 3), dtype=np.uint8)
    frame[:] = color
    return frame


def test_hsv_baseline_colors_and_abstention():
    model = TrafficLightThreeState()
    red_state, red_conf = model.predict(_solid((0, 0, 255)))
    green_state, green_conf = model.predict(_solid((0, 255, 0)))
    assert red_state == RED and red_conf >= 0.9
    assert green_state == GREEN and green_conf >= 0.9
    assert model.predict(_solid((255, 0, 0)))[0] == UNCERTAIN          # blue
    assert model.predict(_solid((0, 255, 255)))[0] == UNCERTAIN        # yellow
    assert model.predict(_solid((30, 30, 30)))[0] == UNCERTAIN         # gray
    assert model.predict(None) == (UNCERTAIN, 0.0)
    mixed = np.zeros((24, 24, 3), dtype=np.uint8)
    mixed[:, :12] = (0, 0, 255)
    mixed[:, 12:] = (0, 255, 0)
    assert model.predict(mixed)[0] == UNCERTAIN
    speck = np.zeros((24, 24, 3), dtype=np.uint8)
    speck[0, 0] = (0, 0, 255)
    assert model.predict(speck)[0] == UNCERTAIN


def test_cross_and_go_ahead_cannot_be_emitted():
    model = TrafficLightThreeState()
    assert model.AUTO_GO is False and model.EMITS_CROSS is False
    frames = [_solid((0, 0, 255)), _solid((0, 255, 0)), _solid((255, 0, 0)),
              _solid((0, 0, 0)), None]
    forbidden = {token.lower() for token in GO_AHEAD_TOKENS} | {"cross", "go-ahead", "可以过街"}
    for frame in frames:
        result = model.predict(frame)
        assert isinstance(result, tuple) and len(result) == 2
        state, confidence = result
        assert state in STATES
        assert state.lower() not in forbidden
        assert state not in ("CROSS", "cross", "go", "go-ahead", "可以过街")
        assert 0.0 <= confidence <= 1.0
        perception_output(state, confidence)
    with pytest.raises(ValueError):
        perception_output("CROSS", 1.0)
    with pytest.raises(ValueError):
        perception_output("可以过街", 1.0)
    with pytest.raises(ValueError):
        TrafficLightDecision(GREEN, TEXT[GREEN], True, 1, "confirmed", auto_go=True)
    with pytest.raises(ValueError):
        TrafficLightDecision("CROSS", "CROSS now", True, 1, "no")
    for text in TEXT.values():
        assert "可以过街" not in text
        assert "CROSS" not in text.upper()
    decision = TrafficLightFilter(min_confirm_frames=1).update(TrafficLightObservation(GREEN))
    assert decision.auto_go is False
    assert decision.state != "CROSS"


def test_uncertain_is_never_scored_as_green():
    scored = score_states(
        [GREEN, RED, UNCERTAIN, GREEN],
        [UNCERTAIN, GREEN, UNCERTAIN, GREEN],
    )
    assert scored["per_class"][GREEN]["recall"] == 0.5
    assert scored["per_class"][GREEN]["predicted"] == 2
    assert scored["confusion"][GREEN][UNCERTAIN] == 1
    assert scored["confusion"][GREEN][GREEN] == 1
    assert scored["unsafe_green"] == 1
    assert scored["uncertain_treated_as_green"] == 0
    assert scored["per_class"][UNCERTAIN]["true_positive"] == 1
    closed = score_states([GREEN, RED, UNCERTAIN], ["CROSS", "go-ahead", "可以过街"])
    assert closed["per_class"][GREEN]["predicted"] == 0
    assert closed["per_class"][GREEN]["recall"] == 0.0
    assert closed["unsafe_green"] == 0
    assert closed["illegal_outputs"] == 3
    assert closed["uncertain_treated_as_green"] == 0
    assert closed["confusion"][RED][GREEN] == 0
    assert apply_green_gate(GREEN, 0.2, 0.6) == UNCERTAIN
    assert apply_green_gate("CROSS", 1.0, 0.6) == UNCERTAIN
    assert apply_green_gate(GREEN, 0.95, 0.6) == GREEN


def test_eval_annotation_only_when_images_missing(tmp_path):
    ann = tmp_path / "ann.csv"
    ann.write_text("file,label\n"
                   "a.jpg,red\n"
                   "b.jpg,countdown_green\n"
                   "c.jpg,none\n"
                   "d.jpg,countdown_blank\n", encoding="utf-8")
    out = tmp_path / "report"
    metrics = run_eval(str(ann), str(tmp_path / "missing_or.tar"), str(out))
    assert metrics["mode"] == "annotation_only"
    assert metrics["class_counts"] == {RED: 1, GREEN: 1, UNCERTAIN: 2}
    assert metrics["images_found"] == 0 and metrics["images_missing"] == 4
    assert metrics["missing_images_counted_as_green"] == 0
    assert metrics["uncertain_treated_as_green"] == 0
    assert metrics["auto_go"] is False
    assert metrics["per_class"][GREEN]["precision"] is None
    assert metrics["confusion"][RED][GREEN] is None
    written = json.loads((out / "metrics.json").read_text(encoding="utf-8"))
    assert written["uncertain_treated_as_green"] == 0
    summary = (out / "summary.md").read_text(encoding="utf-8")
    assert "不确定" in summary and "绿灯" in summary
    assert "把不确定记成绿灯的次数：0" in summary


def test_eval_baseline_on_synthetic_frames(tmp_path):
    import cv2

    images = tmp_path / "images"
    images.mkdir()
    cv2.imwrite(str(images / "red.png"), _solid((0, 0, 255), 32))
    cv2.imwrite(str(images / "green.png"), _solid((0, 255, 0), 32))
    cv2.imwrite(str(images / "none.png"), _solid((20, 20, 20), 32))
    ann = tmp_path / "ann.csv"
    ann.write_text("image_id,path,label\n"
                   "red,red.png,red\n"
                   "green,green.png,green\n"
                   "none,none.png,none\n"
                   "absent,missing.png,green\n", encoding="utf-8")
    out = tmp_path / "out"
    metrics = run_eval(str(ann), str(images), str(out), min_green_confidence=0.6)
    assert metrics["mode"] == "baseline"
    assert metrics["images_found"] == 3 and metrics["images_missing"] == 1
    assert metrics["missing_images_counted_as_green"] == 0
    assert metrics["uncertain_treated_as_green"] == 0
    assert metrics["per_class"][RED]["recall"] == 1.0
    assert metrics["per_class"][GREEN]["recall"] == 1.0
    assert metrics["unsafe_green"] == 0
    assert set(metrics["prediction_states"]) <= set(STATES)
    assert "CROSS" not in metrics["prediction_states"]
    summary = (out / "summary.md").read_text(encoding="utf-8")
    assert "HSV/OpenCV" in summary
    assert "不是过街许可" in summary
