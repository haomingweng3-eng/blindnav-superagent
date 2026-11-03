"""P0 navigation safety state machine: CLEAR / BLOCKED / UNKNOWN.

UNKNOWN means the system cannot vouch for the space in front of the walker (stale input,
unreliable ground calibration, attitude/visual conflict, or an unseen corridor).  It is never
safe-to-pass: callers must map it to OBSERVE/STOP, never FORWARD.  Leaving UNKNOWN requires
``unknown_recovery_frames`` consecutive reliable frames, so a single good frame cannot clear it.
BLOCKED is a *known* obstacle in the forward corridor (planner occupancy, or a head/body/low
height-layer obstacle / confirmed drop risk from ``layers.py``); a turn away from it remains
allowed.  Unconfirmed drop evidence and a ground edge with no depth beyond are UNKNOWN.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Optional

import numpy as np

from .config import BEVConfig, SafetyConfig, UNKNOWN as CELL_UNKNOWN
from .ground import GroundFit
from .layers import BLOCK_REASONS, UNKNOWN_REASONS

CLEAR, BLOCKED, UNKNOWN = "CLEAR", "BLOCKED", "UNKNOWN"

UNKNOWN_TEXT = "前方情况无法确认，请暂停并使用盲杖探查"
BLOCKED_TEXT = "前方有障碍，请停下"
BLOCKED_LEFT_TEXT = "前方有障碍，稍向左"
BLOCKED_RIGHT_TEXT = "前方有障碍，稍向右"

logger = logging.getLogger(__name__)


def safety_text(state: str, command: str = "") -> str:
    if state == UNKNOWN:
        return UNKNOWN_TEXT
    if state == BLOCKED:
        if command == "LEFT":
            return BLOCKED_LEFT_TEXT
        if command == "RIGHT":
            return BLOCKED_RIGHT_TEXT
        return BLOCKED_TEXT
    return ""


@dataclass
class SafetyDecision:
    state: str = CLEAR
    reasons: tuple = ()
    reliable: bool = True
    corridor_coverage: float = 1.0
    stale_ms: float = 0.0


class SafetyStateMachine:
    def __init__(self, cfg: Optional[SafetyConfig] = None, bev: Optional[BEVConfig] = None):
        self.cfg = cfg or SafetyConfig()
        self.bev = bev or BEVConfig()
        self.reset()

    def reset(self) -> None:
        self.state = CLEAR
        self._last_t: Optional[float] = None
        self._reliable_streak = 0
        self.transitions: list = []          # (t_ms, old_state, new_state, reasons)

    # ---- corridor coverage ------------------------------------------------
    def corridor_coverage(self, states: Optional[np.ndarray]) -> float:
        """Fraction of the forward corridor strip whose BEV cells are known (FREE or OCCUPIED)."""
        if states is None or states.size == 0:
            return 0.0
        c, b = self.cfg, self.bev
        nz = min(states.shape[0], max(1, int(round(c.corridor_len_m / b.cell_m))))
        x0 = int(np.floor((-c.corridor_half_width_m + b.lateral_half_m) / b.cell_m + 1e-9))
        x1 = int(np.ceil((c.corridor_half_width_m + b.lateral_half_m) / b.cell_m - 1e-9))
        x0, x1 = max(0, x0), min(states.shape[1], x1)
        if x1 <= x0:
            return 0.0
        sub = states[:nz, x0:x1]
        return float((sub != CELL_UNKNOWN).mean())

    # ---- reliability checks ----------------------------------------------
    def _unknown_reasons(self, now_ms: float, fit: Optional[GroundFit],
                         states: Optional[np.ndarray], prev_t: Optional[float]) -> tuple:
        c = self.cfg
        reasons = []
        stale_ms = 0.0
        if prev_t is not None:
            dt = now_ms - prev_t
            if dt < 0:
                stale_ms = abs(dt)
                reasons.append("timestamp_regression")
            elif dt > c.input_timeout_ms:
                stale_ms = dt
                reasons.append("stale_frame")
        if fit is None:
            reasons.append("no_ground_fit")
        else:
            if not fit.ok:
                reasons.append("ground_not_ok")
            if not math.isfinite(fit.confidence) or fit.confidence < c.min_confidence:
                reasons.append("low_ground_confidence")
            if not math.isfinite(fit.residual) or fit.residual > c.max_residual:
                reasons.append("high_ground_residual")
            angle = fit.attitude_visual_deg
            if fit.visual_fallback or (np.isfinite(angle) and angle > c.max_attitude_visual_deg):
                reasons.append("attitude_visual_conflict")
        coverage = self.corridor_coverage(states)
        if coverage < c.min_corridor_coverage:
            reasons.append("corridor_unseen")
        return tuple(dict.fromkeys(reasons)), stale_ms, coverage

    def update(self, now_ms: float, fit: Optional[GroundFit] = None,
               states: Optional[np.ndarray] = None, plan=None, hazards=None) -> SafetyDecision:
        reasons, stale_ms, coverage = self._unknown_reasons(now_ms, fit, states, self._last_t)
        self._last_t = now_ms
        hazard_reasons = tuple(getattr(hazards, "reasons", ()) or ())
        hazard_unknown = tuple(r for r in hazard_reasons if r in UNKNOWN_REASONS)
        hazard_blocked = tuple(r for r in hazard_reasons if r in BLOCK_REASONS)
        unknown_reasons = reasons + hazard_unknown
        if unknown_reasons:
            state, self._reliable_streak = UNKNOWN, 0
        elif hazard_blocked or (plan is not None and (plan.dead_end or plan.corridor_blocked)):
            state, self._reliable_streak = BLOCKED, 0
        elif self.state == UNKNOWN:
            self._reliable_streak += 1
            state = CLEAR if self._reliable_streak >= self.cfg.unknown_recovery_frames else UNKNOWN
        else:
            state, self._reliable_streak = CLEAR, 0
        blocked = plan is not None and (plan.dead_end or plan.corridor_blocked)
        log_reasons = unknown_reasons + hazard_blocked + (
            ("dead_end",) if blocked and plan.dead_end else
            ("corridor_blocked",) if blocked else ())
        if state != self.state:
            logger.info("safety transition %s -> %s at t_ms=%.1f reasons=%s",
                        self.state, state, float(now_ms), ",".join(log_reasons) or "ok")
            self.transitions.append((float(now_ms), self.state, state, log_reasons))
            self.state = state
        return SafetyDecision(state, tuple(log_reasons), not unknown_reasons, coverage, stale_ms)
