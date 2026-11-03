"""Upper behaviour state machine:  CRUISE -> AVOID_LEFT / AVOID_RIGHT -> PASSING -> RECENTER -> CRUISE.

Layering
  * VFH planner      : geometry only - which headings are safe (never decides the state).
  * WalkingSafetyPolicy : debounced STOP / side commands (a single frame never changes anything).
  * BehaviorMachine  : *this module* - remembers that we are in the middle of a manoeuvre.
                       Owns the episode reference path (x = 0, psi_ref, via ``EgoTracker``).

RECENTER is a state, not a one-frame command: it starts after the obstacle has been passed
(PDR forward progress) while the lateral offset |x_hat| is large, aims at the goal
(0, z_hat + lookahead), and can only end when |x_hat| is small, the heading error is small and the
centre corridor has been free for N consecutive frames (or on timeout).  The planner only
decides *whether it is safe* to steer toward the goal - one 'bad' planner frame holds the state
(the output falls back to FORWARD) instead of cancelling it.  STOP from the safety policy is an
override in every state and does not discard the state.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional, Tuple

from .config import BehaviorConfig, Config, PlannerConfig
from .ego import EgoTracker
from .planner import PlanResult
from .policy import FORWARD, LEFT, OBSERVE, RECENTER, RIGHT, STOP, PolicyDecision, command_text

CRUISE, AVOID_LEFT, AVOID_RIGHT, PASSING = "CRUISE", "AVOID_LEFT", "AVOID_RIGHT", "PASSING"
STATES = (CRUISE, AVOID_LEFT, AVOID_RIGHT, PASSING, RECENTER)


@dataclass
class BehaviorDecision:
    command: str                       # FORWARD | LEFT | RIGHT | STOP | RECENTER | OBSERVE
    text: str
    state: str                         # one of STATES
    stop_reason: str = ""
    target_heading_deg: float = 0.0    # AXIS-frame heading to aim at (0 = along the reference path)
    recenter_dir: str = ""
    goal: Optional[Tuple[float, float]] = None
    x_hat_m: float = 0.0
    z_hat_m: float = 0.0
    note: str = ""


class BehaviorMachine:
    def __init__(self, cfg: Optional[Config] = None, ego: Optional[EgoTracker] = None):
        cfg = cfg or Config()
        self.cfg = cfg.behavior
        self.pcfg: PlannerConfig = cfg.planner
        self._fwd_m = cfg.bev.forward_m
        self.ego = ego or EgoTracker(cfg.ego)
        self.reset()

    def reset(self) -> None:
        self.ego.reset()
        self.state = CRUISE
        self._t_state = 0.0
        self._z_pass0 = 0.0
        self._z_obs = 0.0                # PDR z of the front of the obstacle last seen ahead
        self._center_free = 0
        self.history = []                # (t_ms, state) transitions

    # ---- helpers -----------------------------------------------------
    def _enter(self, state: str, now: float) -> None:
        if state != self.state:
            self.history.append((now, state))
        self.state = state
        self._t_state = now
        self._center_free = 0
        if state == PASSING:
            self._z_pass0 = max(self._z_obs, self.ego.z_hat)   # passing starts counting at the obstacle front

    @property
    def has_pdr(self) -> bool:
        return self.ego.has_orientation

    def yaw_deg(self) -> float:
        """Walker heading relative to the reference direction (+ right); 0 without an orientation sensor."""
        return math.degrees(self.ego.yaw_rel) if self.has_pdr else 0.0

    def goal(self) -> Optional[Tuple[float, float]]:
        if self.state != RECENTER or not self.has_pdr:
            return None
        return (0.0, self.ego.z_hat + self.cfg.lookahead_m)

    def target_axis_deg(self) -> float:
        """AXIS-frame bearing the planner should prefer *before* planning this frame."""
        g = self.goal()
        if g is None:
            return 0.0
        b = math.degrees(math.atan2(g[0] - self.ego.x_hat, g[1] - self.ego.z_hat))
        c = self.cfg.recenter_max_deg
        return max(-c, min(c, b))

    # ---- one tick -----------------------------------------------------
    def step(self, now_ms: float, pol: PolicyDecision, plan: PlanResult) -> BehaviorDecision:
        c = self.cfg
        e = self.ego
        yaw = self.yaw_deg()
        axis_rel = -yaw                                  # the reference direction in the walker frame
        center_clear = plan.clear_toward(axis_rel) >= self.pcfg.passable_clear_m and not plan.dead_end

        # ---- overrides: STOP / OBSERVE keep the state ----
        if pol.command in (STOP, OBSERVE):
            return self._out(pol.command, pol.stop_reason, "safety override" if pol.command == STOP else "observing")

        side_cmd = pol.command if pol.command in (LEFT, RIGHT) else ""
        if self.state not in (AVOID_LEFT, AVOID_RIGHT):    # while side-stepping the heading is skewed: keep the last value
            d = plan.clear_toward(axis_rel)               # obstacle front ahead (remember it for 'passed' logic)
            if d < self._fwd_m - 0.2:
                self._z_obs = e.z_hat + d

        # ---- new obstacle needing a side step: every state may enter AVOID ----
        if side_cmd:
            want = AVOID_LEFT if side_cmd == LEFT else AVOID_RIGHT
            if self.state != want:
                self._enter(want, now_ms)
            return self._out(side_cmd)

        # ---- the policy says FORWARD (no side step required) ----
        if self.state in (AVOID_LEFT, AVOID_RIGHT):
            self._enter(PASSING, now_ms)
            return self._out(FORWARD, note="obstacle side-stepped, passing")
        if self.state == PASSING:
            progressed = (e.z_hat - self._z_pass0) >= c.pass_len_m     # beyond the obstacle front + its depth + body
            timed_out = (now_ms - self._t_state) >= c.pass_timeout_ms
            if progressed or timed_out:
                if self.has_pdr and abs(e.x_hat) > c.recenter_thr_m:
                    self._enter(RECENTER, now_ms)
                else:
                    self._enter(CRUISE, now_ms)
            return self._out(FORWARD)
        if self.state == RECENTER:
            return self._recenter(now_ms, plan, center_clear, yaw)
        # CRUISE
        return self._out(FORWARD)

    def _recenter(self, now_ms: float, plan: PlanResult, center_clear: bool, yaw: float) -> BehaviorDecision:
        c, e = self.cfg, self.ego
        self._center_free = self._center_free + 1 if center_clear else 0
        done = (abs(e.x_hat) < c.recenter_done_m and abs(yaw) < c.heading_done_deg
                and self._center_free >= c.center_free_frames)
        if done:
            self._enter(CRUISE, now_ms)
            return self._out(FORWARD, note="recentered")
        if (now_ms - self._t_state) >= c.recenter_timeout_ms:
            e.reanchor()                                  # give up: accept the current line as the new reference
            self._enter(CRUISE, now_ms)
            return self._out(FORWARD, note="recenter timeout, re-anchored")
        tgt = self.target_axis_deg()
        rel = tgt - yaw                                   # same bearing in the walker frame
        # VFH only validates: steer toward the goal iff the swept corridor toward it is passable
        safe = plan.clear_toward(rel) >= self.pcfg.passable_clear_m
        if abs(e.x_hat) < c.recenter_done_m or not safe:
            return self._out(FORWARD, note="hold recenter: " + ("close, aligning" if safe else "path to goal not safe"))
        d = "right" if tgt > 0 else "left"
        return self._out(RECENTER, target=tgt, recenter_dir=d)

    def _out(self, command: str, stop_reason: str = "", note: str = "", target: float = 0.0,
             recenter_dir: str = "") -> BehaviorDecision:
        return BehaviorDecision(command, command_text(command, recenter_dir), self.state, stop_reason, target,
                                recenter_dir, self.goal(), self.ego.x_hat, self.ego.z_hat, note)
