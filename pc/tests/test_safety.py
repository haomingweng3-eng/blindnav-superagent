"""P0 safety state machine: UNKNOWN is never safe-to-pass and clears only after hysteresis."""
import logging
from types import SimpleNamespace

import numpy as np
import pytest

from blindnav_pc.config import BEVConfig, SafetyConfig
from blindnav_pc.ground import GroundFit
from blindnav_pc.safety import (BLOCKED, CLEAR, UNKNOWN, BLOCKED_TEXT, UNKNOWN_TEXT,
                                SafetyStateMachine, safety_text)

B = BEVConfig()


def fit(ok=True, confidence=0.9, residual=0.01, angle=0.0, fallback=False):
    return GroundFit(ok=ok, confidence=confidence, residual=residual,
                     attitude_visual_deg=angle, visual_fallback=fallback)


def states(known=True):
    return np.full((B.nz, B.nx), 1 if known else 0, np.uint8)       # 1 = FREE, 0 = UNKNOWN


def plan(dead_end=False, blocked=False):
    return SimpleNamespace(dead_end=dead_end, corridor_blocked=blocked)


def test_texts_distinguish_obstacle_from_unconfirmed():
    assert UNKNOWN_TEXT != BLOCKED_TEXT
    assert "无法确认" in UNKNOWN_TEXT and "盲杖" in UNKNOWN_TEXT
    assert "障碍" in BLOCKED_TEXT
    assert safety_text(UNKNOWN, "LEFT") == UNKNOWN_TEXT               # a side hint is not safe either
    assert "障碍" in safety_text(BLOCKED, "RIGHT")
    assert safety_text(CLEAR) == ""


def test_clear_baseline_then_blocked():
    m = SafetyStateMachine()
    assert m.update(0, fit(), states()).state == CLEAR
    d = m.update(100, fit(), states(), plan(dead_end=True))
    assert d.state == BLOCKED and "dead_end" in d.reasons
    assert m.transitions[-1][1:3] == (CLEAR, BLOCKED)


def test_stale_or_dropped_frame_is_unknown():
    m = SafetyStateMachine()
    assert m.update(0, fit(), states()).state == CLEAR
    d = m.update(1000, fit(), states())                               # 1 s gap > 500 ms timeout
    assert d.state == UNKNOWN and "stale_frame" in d.reasons and d.stale_ms == pytest.approx(1000.0)
    d = m.update(900, fit(), states())                                # timestamp regression
    assert d.state == UNKNOWN and "timestamp_regression" in d.reasons


@pytest.mark.parametrize("kwargs,reason", [
    ({"ok": False}, "ground_not_ok"),
    ({"confidence": 0.1}, "low_ground_confidence"),
    ({"residual": 0.5}, "high_ground_residual"),
    ({"angle": 30.0}, "attitude_visual_conflict"),
    ({"angle": 11.0, "fallback": True}, "attitude_visual_conflict"),
])
def test_unreliable_geometry_is_unknown(kwargs, reason):
    d = SafetyStateMachine().update(0, fit(**kwargs), states())
    assert d.state == UNKNOWN and reason in d.reasons
    assert not d.reliable


def test_unseen_corridor_is_unknown():
    d = SafetyStateMachine().update(0, fit(), states(known=False))
    assert d.state == UNKNOWN and "corridor_unseen" in d.reasons
    assert d.corridor_coverage == 0.0


def test_unknown_wins_over_blocked_evidence():
    d = SafetyStateMachine().update(0, fit(ok=False), states(), plan(dead_end=True))
    assert d.state == UNKNOWN                                          # cannot trust the block evidence either


def test_unknown_to_clear_needs_n_consecutive_reliable_frames():
    m = SafetyStateMachine(SafetyConfig(unknown_recovery_frames=3))
    assert m.update(0, fit(ok=False), states()).state == UNKNOWN
    for t in (100, 200):
        assert m.update(t, fit(), states()).state == UNKNOWN           # one good frame is not enough
    assert m.update(300, fit(), states()).state == CLEAR
    # a fresh failure restarts the streak
    assert m.update(400, fit(confidence=0.1), states()).state == UNKNOWN
    assert m.update(500, fit(), states()).state == UNKNOWN
    assert m.update(600, fit(), states()).state == UNKNOWN
    assert m.update(700, fit(), states()).state == CLEAR


def test_hazard_reasons_feed_the_state_machine():
    hazards = SimpleNamespace(reasons=("body_obstacle",))
    d = SafetyStateMachine().update(0, fit(), states(), plan(), hazards)
    assert d.state == BLOCKED and "body_obstacle" in d.reasons and d.reliable
    hazards = SimpleNamespace(reasons=("drop_edge_unobserved",))       # unreliable evidence
    d = SafetyStateMachine().update(100, fit(), states(), plan(), hazards)
    assert d.state == UNKNOWN and not d.reliable
    assert "drop_edge_unobserved" in d.reasons


def test_confirmed_drop_risk_blocks_and_unseen_edge_wins():
    blocked = SimpleNamespace(reasons=("drop_risk",))
    assert SafetyStateMachine().update(0, fit(), states(), plan(), blocked).state == BLOCKED
    mixed = SimpleNamespace(reasons=("drop_risk", "drop_unconfirmed"))
    assert SafetyStateMachine().update(100, fit(), states(), plan(), mixed).state == UNKNOWN


def test_transitions_are_logged_with_timestamps(caplog):
    m = SafetyStateMachine()
    with caplog.at_level(logging.INFO, logger="blindnav_pc.safety"):
        m.update(1000, fit(ok=False), states(known=False))
    msgs = [r.message for r in caplog.records if r.name == "blindnav_pc.safety"]
    assert any("CLEAR -> UNKNOWN" in s and "t_ms=1000.0" in s for s in msgs)
    assert m.transitions[0][0] == 1000.0
