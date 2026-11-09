"""Safety Coordinator: fuses Path Keeper and Obstacle Guardian into one safety state.

Issue #18, third part.  The Coordinator never re-derives perception: it only combines the two
responsibility outputs into exactly one of ``CLEAR`` / ``BLOCKED`` / ``UNKNOWN`` / ``OBSERVE``
(:mod:`blindnav_pc.safety_states`) and attaches the Path Keeper's lateral guidance only when the
state is ``CLEAR``.

Fusion rules (frozen by the Issue #18 hard rules):

1. A confirmed 3D obstacle in the body swept path (Obstacle Guardian ``BLOCKED``) is final:
   a path-surface decision can never suppress or downgrade it ("do not suppress real obstacles
   to reduce stops").
2. ``UNKNOWN`` from either responsibility means the evidence cannot confirm safety ->
   ``UNKNOWN`` (never converted into a safe pass).
3. ``OBSERVE`` from either responsibility (path-surface risk: road/curb/boundary/drift, or a
   not-yet-confirmed obstacle) -> ``OBSERVE``; no forward guidance is emitted.
4. Only when both responsibilities are ``CLEAR`` is the state ``CLEAR`` and the Path Keeper
   guidance (STRAIGHT / LEFT / RIGHT) allowed through.

Consequences (checked as invariants by the tests):

* ``NON_WALKABLE`` / curb / road alone cannot produce ``BLOCKED`` -- it is a Path Keeper
  ``OBSERVE`` -- so "NON_WALKABLE alone must never equal STOP" holds by construction.
* ``BLOCKED`` can only come from the Obstacle Guardian's confirmed 3D occupancy.
* The fusion is monotone: adding a more severe responsibility state can only make the result
  more severe (no severity downgrades), which rules out obstacle suppression.

Nothing here changes the Android STOP Safety Policy, MotionAuthority or ArrivalAuthority.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .obstacle_guardian import ObstacleRisk
from .path_keeper import PathState
from .safety_states import BLOCKED, CLEAR, OBSERVE, UNKNOWN, most_severe

LEFT, RIGHT, STRAIGHT = "LEFT", "RIGHT", "STRAIGHT"

STATE_TEXT = {
    BLOCKED: "前方有障碍，请停下",
    UNKNOWN: "前方情况无法确认，请暂停并使用盲杖探查",
    OBSERVE: "请稍停观察，确认脚下与前方路面",
    CLEAR: "前方可通行",
}


@dataclass(frozen=True)
class SafetyDecision:
    """One fused per-frame decision: safety state + (only when CLEAR) usable guidance."""

    state: str
    guidance: Optional[str]
    text: str
    reason: str
    path: PathState
    obstacle: ObstacleRisk

    def effective_command(self) -> str:
        """Single-symbol command for measurement / logging.

        ``BLOCKED`` maps to STOP; ``UNKNOWN``/``OBSERVE`` are their own non-pass symbols; a
        ``CLEAR`` state carries STRAIGHT / LEFT / RIGHT guidance.
        """
        if self.state == BLOCKED:
            return "STOP"
        if self.state in (UNKNOWN, OBSERVE):
            return self.state
        return self.guidance or UNKNOWN

    def is_pass(self) -> bool:
        return self.state == CLEAR and self.guidance in (STRAIGHT, LEFT, RIGHT)


def fuse(path: PathState, obstacle: ObstacleRisk) -> SafetyDecision:
    """Fuse the two responsibility outputs into one :class:`SafetyDecision`."""
    if obstacle.state == BLOCKED:
        state = BLOCKED
        reason = f"obstacle:{obstacle.reason}"
    elif path.status == UNKNOWN or obstacle.state == UNKNOWN:
        state = UNKNOWN
        reason = (f"path:{path.reason}" if path.status == UNKNOWN
                  else f"obstacle:{obstacle.reason}")
    elif path.status == OBSERVE or obstacle.state == OBSERVE:
        state = OBSERVE
        reason = (f"path:{path.reason}" if path.status == OBSERVE
                  else f"obstacle:{obstacle.reason}")
    else:
        state = CLEAR
        reason = f"path:{path.reason}"
    guidance = path.guidance if state == CLEAR else None
    return SafetyDecision(state=state, guidance=guidance, text=STATE_TEXT[state],
                          reason=reason, path=path, obstacle=obstacle)


class SafetyCoordinator:
    """Stateless fusion stage; one instance may be shared across sessions."""

    def decide(self, path: PathState, obstacle: ObstacleRisk) -> SafetyDecision:
        decision = fuse(path, obstacle)
        # Defensive invariant: the fusion must be at least as severe as either input.
        expected = most_severe(path.status, obstacle.state)
        if decision.state != expected:
            raise AssertionError(
                f"fusion severity regression: path={path.status} obstacle={obstacle.state} "
                f"fused={decision.state}")
        return decision
