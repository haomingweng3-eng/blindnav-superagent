"""Temporal smoothing / command policy: port of the Android WalkingSafetyPolicy.kt debounce logic.

Kotlin logic ported (names in brackets): a single frame never stops [stopStreak>=2 and
stopPersistMs], a stop is held for a while [stopHoldMs], side changes need N frames AND a dwell
time [sideFrames/sideDwellMs], going back to straight needs [centerFrames], geometry lost for
[corridorLostMs] -> degraded -> re-observe.  OBSERVE/STOP when the ground calibration is degraded.
RECENTER is *not* decided here any more: it is a state of ``behavior.BehaviorMachine``.  Everything is driven by ``now_ms`` that the
caller passes in (no wall clock) so it is deterministic and testable.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .config import PolicyConfig

FORWARD, LEFT, RIGHT, STOP, RECENTER, OBSERVE = "FORWARD", "LEFT", "RIGHT", "STOP", "RECENTER", "OBSERVE"

TEXT = {FORWARD: "继续", LEFT: "稍向左", RIGHT: "稍向右", STOP: "停下", OBSERVE: "请稍停，正在观察"}


def command_text(command: str, recenter_dir: str = "") -> str:
    if command == RECENTER:
        return "向右回正" if recenter_dir == "right" else "向左回正"
    return TEXT[command]


@dataclass
class PolicyInput:
    now_ms: float
    ground_ok: bool = True
    corridor_blocked: bool = False
    corridor_min_dist_m: float = float("inf")
    dead_end: bool = False
    desired_side: str = "center"            # 'left' | 'center' | 'right' from the planner


@dataclass
class PolicyDecision:
    command: str
    text: str
    stop_reason: str
    stable_frames: int


class WalkingSafetyPolicy:
    def __init__(self, cfg: Optional[PolicyConfig] = None):
        self.cfg = cfg or PolicyConfig()
        self.reset()

    def reset(self) -> None:
        self._stop_since = 0.0
        self._stop_streak = 0
        self._last_stop_at = None
        self._last_stop = False
        self._side_key = ""
        self._side_streak = 0
        self._side_since = 0.0
        self._center_streak = 0
        self._cmd = FORWARD
        self._lost_since = None

    @property
    def command(self) -> str:
        return self._cmd

    def _decision(self, stop_reason: str = "", stable: int = 0) -> PolicyDecision:
        return PolicyDecision(self._cmd, command_text(self._cmd), stop_reason, stable)

    def evaluate(self, inp: PolicyInput) -> PolicyDecision:
        c = self.cfg
        now = inp.now_ms
        # ---- calibration / geometry lost -> hold, OBSERVE, STOP ----
        if not inp.ground_ok:
            if self._lost_since is None:
                self._lost_since = now
            lost = now - self._lost_since
            if lost >= c.observe_to_stop_ms:
                self._cmd = STOP
                return self._decision("ground_lost_timeout")
            if lost >= c.lost_ms:
                self._cmd = OBSERVE
                self._reset_side()
                return self._decision("ground_lost")
            return self._decision("")          # short loss: keep last command
        self._lost_since = None
        if self._cmd == OBSERVE:
            self._cmd = FORWARD

        # ---- stop evidence with Kotlin debounce ----
        evidence = inp.dead_end or (inp.corridor_blocked and inp.corridor_min_dist_m < c.imminent_dist_m)
        if evidence:
            if self._stop_streak == 0:
                self._stop_since = now
            self._stop_streak += 1
            self._last_stop_at = now
        else:
            self._stop_streak = 0
        fresh = evidence and self._stop_streak >= 2 and (now - self._stop_since) >= c.stop_persist_ms
        hold = self._last_stop_at is not None and (now - self._last_stop_at) < c.stop_hold_ms
        self._last_stop = bool(fresh or (hold and self._last_stop))
        if self._last_stop:
            self._reset_side()
            self._cmd = STOP
            return self._decision("dead_end" if inp.dead_end else "corridor_obstacle_persistent")
        if self._cmd == STOP:
            self._cmd = FORWARD

        side = inp.desired_side
        # ---- normal side/center debounce (Kotlin port) ----
        if side in (LEFT.lower(), RIGHT.lower()):
            self._center_streak = 0
            if side != self._side_key:
                self._side_key, self._side_streak, self._side_since = side, 1, now
            else:
                self._side_streak += 1
            if self._side_streak >= c.side_frames and (now - self._side_since) >= c.side_dwell_ms:
                self._cmd = LEFT if side == "left" else RIGHT
            return self._decision(stable=self._side_streak)

        self._side_key, self._side_streak = "", 0
        self._center_streak += 1
        if self._center_streak >= c.center_frames:
            if self._cmd in (LEFT, RIGHT):
                self._cmd = FORWARD
        return self._decision(stable=self._center_streak)

    def _reset_side(self) -> None:
        self._side_key, self._side_streak, self._center_streak = "", 0, 0
