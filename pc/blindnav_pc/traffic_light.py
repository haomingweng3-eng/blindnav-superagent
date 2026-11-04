"""Three-state pedestrian traffic-light stub: green / red / uncertain only.

There is deliberately no fourth state and no "go" output.  ``green`` only says that the lamp was
recognised as green; the crossing decision always stays with the person.  Any missing, low-confidence,
disagreeing or countdown-blank observation is ``uncertain`` and must be treated like "wait".

The label helper targets the ImVisible (LYTNet) CSV columns ``file,mode,x1,y1,x2,y2,block``:
mode 0 = red, 1 = green, 2 = green countdown, 3 = countdown blank, 4 = no light.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from typing import Optional

GREEN, RED, UNCERTAIN = "green", "red", "uncertain"
STATES = (GREEN, RED, UNCERTAIN)

IMVISIBLE_MODE_TO_STATE = {
    0: RED,
    1: GREEN,
    2: GREEN,       # green with a countdown: still green, but never a crossing permit
    3: UNCERTAIN,   # countdown blank
    4: UNCERTAIN,   # no pedestrian light visible
}

TEXT = {
    GREEN: "识别到绿灯；是否过街由您决定，请确认左右车辆",
    RED: "红灯，请等待",
    UNCERTAIN: "红绿灯状态无法确认，请等待并使用盲杖探查",
}


def imvisible_mode_to_state(mode) -> str:
    """Map the ImVisible ``mode`` column to one of the three allowed states (unknown -> uncertain)."""
    try:
        return IMVISIBLE_MODE_TO_STATE.get(int(mode), UNCERTAIN)
    except (TypeError, ValueError):
        return UNCERTAIN


@dataclass(frozen=True)
class TrafficLightObservation:
    state: str
    confidence: float = 1.0
    t_ms: Optional[float] = None
    source: str = ""

    def __post_init__(self) -> None:
        if self.state not in STATES:
            raise ValueError(f"traffic-light state must be one of {STATES}, got {self.state!r}")
        if not 0.0 <= float(self.confidence) <= 1.0:
            raise ValueError("confidence must be in [0, 1]")


@dataclass(frozen=True)
class TrafficLightDecision:
    state: str
    text: str
    stable: bool
    frames: int
    reason: str
    auto_go: bool = False                    # invariant: this stub never authorises crossing
    requires_user_confirmation: bool = True


def imvisible_row_to_observation(row: dict, confidence: float = 1.0) -> TrafficLightObservation:
    """One ImVisible CSV row -> observation.  Coordinates/``block`` are not used by the light stub."""
    state = imvisible_mode_to_state(row.get("mode"))
    return TrafficLightObservation(state, confidence=confidence,
                                   t_ms=float(row["t_ms"]) if row.get("t_ms") not in (None, "") else None,
                                   source=f"imvisible:{row.get('file', '')}")


def read_imvisible_rows(path: str) -> list[dict]:
    with open(path, "r", encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


class TrafficLightFilter:
    """Consecutive-frame confirmation with hysteresis; anything else is uncertain."""

    def __init__(self, min_confirm_frames: int = 3, min_confidence: float = 0.6):
        if min_confirm_frames < 1:
            raise ValueError("min_confirm_frames must be >= 1")
        self.min_confirm_frames = int(min_confirm_frames)
        self.min_confidence = float(min_confidence)
        self.reset()

    def reset(self) -> None:
        self._last: Optional[str] = None
        self._count = 0

    def _decision(self, state: str, stable: bool, reason: str) -> TrafficLightDecision:
        return TrafficLightDecision(state, TEXT[state], stable, self._count, reason,
                                    auto_go=False,
                                    requires_user_confirmation=(state == GREEN))

    def update(self, obs: Optional[TrafficLightObservation]) -> TrafficLightDecision:
        if obs is None:
            self.reset()
            return self._decision(UNCERTAIN, False, "no_observation")
        if obs.state == UNCERTAIN or obs.confidence < self.min_confidence:
            self.reset()
            reason = "uncertain_observation" if obs.state == UNCERTAIN else "low_confidence"
            return self._decision(UNCERTAIN, False, reason)
        if obs.state == self._last:
            self._count += 1
        else:
            self._last, self._count = obs.state, 1
        if self._count >= self.min_confirm_frames:
            return self._decision(obs.state, True, "confirmed")
        return self._decision(UNCERTAIN, False, "confirming")
