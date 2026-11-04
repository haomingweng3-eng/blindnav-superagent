"""Last10m door proposal stub: YOLO label parsing, range estimate, selection and refusal."""
import math

import numpy as np
import pytest

from blindnav_pc.last10m import (MAX_CANDIDATES, DoorCandidate, estimate_distance_m,
                                 load_entrances_labels, propose_doors)


def _door(distance=None, confidence=1.0, cx=0.5, cy=0.8, w=0.1, h=0.1):
    return DoorCandidate(0, cx, cy, w, h, confidence=confidence, distance_m=distance)


def _camera(w=320, h=240, fx=240.0):
    return np.array([[fx, 0.0, w / 2.0], [0.0, fx, h / 2.0], [0.0, 0.0, 1.0]])


def _up(pitch_deg=15.0):
    p = math.radians(pitch_deg)
    return np.array([0.0, -math.cos(p), -math.sin(p)])


def test_load_entrances_labels(tmp_path):
    p = tmp_path / "labels.txt"
    p.write_text("0 0.167969 0.600260 0.050781 0.156250\n"
                 "0 0.5 0.7 0.1 0.2\n"
                 "bad line\n", encoding="utf-8")
    doors = load_entrances_labels(str(p))
    assert len(doors) == 2
    assert doors[0].class_id == 0
    assert doors[0].cx == pytest.approx(0.167969)
    assert doors[1].area == pytest.approx(0.02)


def test_estimate_distance_is_monotonic_and_rejects_sky():
    K, up = _camera(), _up()
    low = DoorCandidate(0, 0.5, 0.8, 0.2, 0.1)     # bottom row is lower -> nearer
    high = DoorCandidate(0, 0.5, 0.6, 0.2, 0.1)    # higher in the image -> farther
    d_low = estimate_distance_m(low, K, (320, 240), up, 1.2)
    d_high = estimate_distance_m(high, K, (320, 240), up, 1.2)
    assert d_low is not None and d_high is not None
    assert 0.0 < d_low < d_high
    assert estimate_distance_m(DoorCandidate(0, 0.5, 0.1, 0.1, 0.1), K, (320, 240), up, 1.2) is None


def test_no_candidates_refuses():
    p = propose_doors([])
    assert p.refused and p.reason == "no_candidate" and p.candidates == ()


def test_low_confidence_refuses():
    p = propose_doors([_door(1.0, confidence=0.2), _door(2.0, confidence=0.3)])
    assert p.refused and p.reason == "low_confidence"


def test_out_of_range_refuses():
    p = propose_doors([_door(12.0), _door(15.0)])
    assert p.refused and p.reason == "out_of_range" and p.candidate_count == 0


def test_picks_at_most_three_nearest():
    p = propose_doors([_door(float(d)) for d in (5.0, 1.0, 4.0, 2.0, 3.0)])
    assert not p.refused and p.reason == "ok"
    assert p.candidate_count == 5 and p.total_count == 5
    assert len(p.candidates) == MAX_CANDIDATES
    assert [c.distance_m for c in p.candidates] == [1.0, 2.0, 3.0]


def test_max_candidates_is_clamped_to_three():
    p = propose_doors([_door(float(d)) for d in (1.0, 2.0, 3.0, 4.0)], max_candidates=9)
    assert len(p.candidates) == MAX_CANDIDATES


def test_near_tie_at_cutoff_refuses_as_ambiguous():
    p = propose_doors([_door(d) for d in (1.0, 1.1, 1.2, 1.25)], ambiguity_margin_m=0.5)
    assert p.refused and p.reason == "ambiguous" and p.candidates == ()


def test_distance_is_required_when_requested():
    doors = [_door(1.0), DoorCandidate(0, 0.5, 0.7, 0.1, 0.1, distance_m=None)]
    assert propose_doors(doors, require_distance=True).reason == "distance_unknown"
    p = propose_doors(doors, require_distance=False)
    assert not p.refused and p.candidates[0].distance_m == 1.0
