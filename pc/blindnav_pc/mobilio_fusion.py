"""Monotone fusion for the experimental surface and occupancy routes."""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

from .mobilio_occupancy import OccupancyRoute
from .mobilio_surface import SurfaceRoute
from .safety_states import BLOCKED, CLEAR, OBSERVE, UNKNOWN

LEFT, RIGHT, STRAIGHT = "LEFT", "RIGHT", "STRAIGHT"
_SEVERITY = {CLEAR: 0, OBSERVE: 1, UNKNOWN: 2, BLOCKED: 3}


@dataclass(frozen=True)
class MobilioDecision:
    state: str
    angle_rad: Optional[float]
    guidance: Optional[str]
    reason: str
    surface_state: str
    occupancy_state: str


def _guidance(angle_rad: Optional[float]) -> Optional[str]:
    if angle_rad is None or not math.isfinite(float(angle_rad)):
        return None
    deg = math.degrees(float(angle_rad))
    if abs(deg) < 15.0:
        return STRAIGHT
    return RIGHT if deg > 0.0 else LEFT


def fuse(surface: SurfaceRoute, occupancy: OccupancyRoute) -> MobilioDecision:
    """Fuse the two independent route results without downgrading severity."""
    for state in (surface.state, occupancy.state):
        if state not in _SEVERITY:
            raise ValueError(f"unknown Mobilio state {state!r}")
    state = max((surface.state, occupancy.state), key=lambda value: _SEVERITY[value])
    if occupancy.state == BLOCKED:
        reason = f"occupancy:{occupancy.reason}"
        angle = occupancy.angle_rad
    elif surface.state == UNKNOWN or occupancy.state == UNKNOWN:
        reason = (f"surface:{surface.reason}" if surface.state == UNKNOWN
                  else f"occupancy:{occupancy.reason}")
        angle = surface.angle_rad if surface.angle_rad is not None else occupancy.angle_rad
    elif surface.state == OBSERVE or occupancy.state == OBSERVE:
        reason = (f"surface:{surface.reason}" if surface.state == OBSERVE
                  else f"occupancy:{occupancy.reason}")
        angle = surface.angle_rad if surface.angle_rad is not None else occupancy.angle_rad
    else:
        reason = f"surface:{surface.reason}"
        angle = surface.angle_rad if surface.angle_rad is not None else occupancy.angle_rad
    return MobilioDecision(
        state=state,
        angle_rad=angle,
        guidance=_guidance(angle) if state == CLEAR else None,
        reason=reason,
        surface_state=surface.state,
        occupancy_state=occupancy.state,
    )


class MobilioFusion:
    """Stateless wrapper useful to callers that prefer an object contract."""

    def decide(self, surface: SurfaceRoute, occupancy: OccupancyRoute) -> MobilioDecision:
        return fuse(surface, occupancy)
