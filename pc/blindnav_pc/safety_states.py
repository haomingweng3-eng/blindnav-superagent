"""Safety-state vocabulary shared by the Path Keeper / Obstacle Guardian / Safety Coordinator.

The Safety Coordinator (Issue #18) fuses the two independent responsibilities into exactly one
of four states:

``CLEAR``     both responsibilities report no risk and the path decision is usable.
``BLOCKED``   the Obstacle Guardian has confirmed a 3D object occupying the body swept path.
``UNKNOWN``   there is insufficient evidence to confirm either safety or danger (never a pass).
``OBSERVE``   evidence exists but is not a confirmed collision (path-surface risk, curb/road
              boundary, drift, or a not-yet-confirmed obstacle) -> hold and observe, no pass.

This module deliberately contains only string constants and a severity ordering so that the
three decision modules can be imported and unit-tested independently.  Unicode output text is
kept in :mod:`blindnav_pc.safety_coordinator` because it is presentation, not state.

These states are PC-side research states.  Nothing here changes the Android STOP Safety
Policy, MotionAuthority or ArrivalAuthority.
"""
from __future__ import annotations

from typing import Tuple

CLEAR = "CLEAR"
BLOCKED = "BLOCKED"
UNKNOWN = "UNKNOWN"
OBSERVE = "OBSERVE"

SAFETY_STATES: Tuple[str, ...] = (CLEAR, BLOCKED, UNKNOWN, OBSERVE)

#: Fusion severity: a higher value may never be downgraded by a lower one (no obstacle
#: suppression, and UNKNOWN is never converted into a safe pass).
SEVERITY = {CLEAR: 0, OBSERVE: 1, UNKNOWN: 2, BLOCKED: 3}


def most_severe(*states: str) -> str:
    """Return the most severe of ``states`` (fails loudly on an unknown state name)."""
    if not states:
        return CLEAR
    for state in states:
        if state not in SEVERITY:
            raise ValueError(f"unknown safety state {state!r}")
    return max(states, key=lambda s: SEVERITY[s])


def is_pass(state: str) -> bool:
    """True only for CLEAR: the only state that may carry forward guidance."""
    return state == CLEAR
