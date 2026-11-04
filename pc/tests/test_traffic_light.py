"""Three-state traffic light stub tests: ImVisible label mapping and confirmation hysteresis."""
import pytest

from blindnav_pc.traffic_light import (GREEN, RED, UNCERTAIN, TrafficLightFilter,
                                       TrafficLightObservation, imvisible_mode_to_state,
                                       imvisible_row_to_observation, read_imvisible_rows)


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
