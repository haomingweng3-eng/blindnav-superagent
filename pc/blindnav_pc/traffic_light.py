"""Three-state pedestrian traffic-light perception: green / red / uncertain only.

There is deliberately no fourth state and no crossing permit.  ``green`` only says that the lamp
was recognised as green; the crossing decision always stays with the person.  Any missing,
low-confidence, disagreeing, countdown-blank, occluded or unknown observation is ``uncertain``
and must be treated like "wait".

Two label surfaces are accepted:

* ImVisible / LYTNet numeric ``mode``: 0 = red, 1 = green, 2 = green countdown, 3 = countdown
  blank, 4 = no light.  CSV columns ``file,mode,x1,y1,x2,y2,block``.
* Text labels used by the same family of sets: ``red``, ``green``, ``countdown_green``,
  ``countdown_blank``, ``none``, plus ``unknown`` / ``occluded-heavy``.

``TrafficLightThreeState.predict`` is an HSV / OpenCV color-ROI baseline for offline
scaffolding.  It is not the final model and it cannot return ``CROSS`` or a go-ahead.
"""
from __future__ import annotations

import csv
import os
from dataclasses import dataclass
from typing import Optional, Sequence

GREEN, RED, UNCERTAIN = "green", "red", "uncertain"
STATES = (GREEN, RED, UNCERTAIN)

# Perception outputs that would authorise a crossing.  They are not states.
GO_AHEAD_TOKENS = ("cross", "go", "go_ahead", "goahead", "可以过街", "现在走", "auto_go")

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

# Text labels collapse onto the same three states.  countdown_green is still only a lamp colour.
TEXT_LABEL_TO_STATE = {
    "red": RED,
    "green": GREEN,
    "countdown_green": GREEN,
    "green_countdown": GREEN,
    "countdown_blank": UNCERTAIN,
    "blank": UNCERTAIN,
    "none": UNCERTAIN,
    "no_light": UNCERTAIN,
    "nolight": UNCERTAIN,
    "unknown": UNCERTAIN,
    "occluded": UNCERTAIN,
    "occluded_heavy": UNCERTAIN,
    "uncertain": UNCERTAIN,
    "0": RED,
    "1": GREEN,
    "2": GREEN,
    "3": UNCERTAIN,
    "4": UNCERTAIN,
}

_IMAGE_PATH_KEYS = ("file", "filename", "image", "image_path", "path", "img", "filepath")
_IMAGE_ID_KEYS = ("image_id", "id", "frame_id")
_LABEL_KEYS = ("label", "class", "class_name", "state", "gt", "annotation", "raw_label", "name")
_MODE_KEYS = ("mode",)


def _normalize_label(label: str) -> str:
    text = label.strip().lower().replace("-", "_").replace(" ", "_")
    while "__" in text:
        text = text.replace("__", "_")
    return text


def _is_go_ahead_token(label: str) -> bool:
    key = _normalize_label(label)
    return key in GO_AHEAD_TOKENS or label.strip() in GO_AHEAD_TOKENS


def label_to_state(label) -> str:
    """Map a numeric mode or a text label onto green / red / uncertain.

    Anything that would mean "cross" or "go ahead", and anything unrecognised, is uncertain.
    """
    if label is None or isinstance(label, bool):
        return UNCERTAIN
    if isinstance(label, (int, float)):
        return imvisible_mode_to_state(label)
    text = str(label).strip()
    if not text or _is_go_ahead_token(text):
        return UNCERTAIN
    key = _normalize_label(text)
    if key in TEXT_LABEL_TO_STATE:
        return TEXT_LABEL_TO_STATE[key]
    return imvisible_mode_to_state(text)


def imvisible_mode_to_state(mode) -> str:
    """Map the ImVisible ``mode`` column to one of the three allowed states (unknown -> uncertain)."""
    if isinstance(mode, str) and _is_go_ahead_token(mode):
        return UNCERTAIN
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

    def __post_init__(self) -> None:
        if self.state not in STATES:
            raise ValueError(f"traffic-light state must be one of {STATES}, got {self.state!r}")
        if self.auto_go:
            raise ValueError("auto_go must stay false; traffic light never authorises crossing")
        if "可以过街" in self.text or "CROSS" in self.text.upper():
            raise ValueError("traffic-light text must not grant a crossing")


def imvisible_row_to_observation(row: dict, confidence: float = 1.0) -> TrafficLightObservation:
    """One ImVisible CSV row -> observation.  Coordinates/``block`` are not used by the light stub."""
    state = imvisible_mode_to_state(row.get("mode"))
    return TrafficLightObservation(state, confidence=confidence,
                                   t_ms=float(row["t_ms"]) if row.get("t_ms") not in (None, "") else None,
                                   source=f"imvisible:{row.get('file', '')}")


def read_imvisible_rows(path: str) -> list[dict]:
    with open(path, "r", encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def _header_key(name: Optional[str]) -> str:
    return (name or "").strip().lower().replace("-", "_").replace(" ", "_")


def _first_field(row: dict, keys: Sequence[str]) -> Optional[str]:
    for key in keys:
        if key not in row:
            continue
        value = row[key]
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return None


@dataclass(frozen=True)
class ImVisibleRecord:
    """One annotated frame after the three-state collapse."""

    image_id: str
    path: str
    gt_state: str
    raw_label: str

    def __post_init__(self) -> None:
        if self.gt_state not in STATES:
            raise ValueError(f"gt_state must be one of {STATES}, got {self.gt_state!r}")


def parse_imvisible_csv(path: str) -> list[ImVisibleRecord]:
    """Read an ImVisible-style CSV into three-state records.

    ``file`` / ``image`` / ``path`` supplies the image path.  ``image_id`` is taken from an id
    column when present, otherwise from the filename.  A text ``label`` wins over numeric
    ``mode``; if both are present and disagree, the record is ``uncertain`` rather than a guess.
    Go-ahead words are never stored as a state.
    """
    records: list[ImVisibleRecord] = []
    for index, raw in enumerate(read_imvisible_rows(path)):
        row = {_header_key(key): value for key, value in raw.items()}
        if not any(str(value).strip() for value in row.values() if value is not None):
            continue
        image_path = _first_field(row, _IMAGE_PATH_KEYS)
        image_id = _first_field(row, _IMAGE_ID_KEYS)
        if not image_id:
            image_id = os.path.basename(image_path) if image_path else f"row-{index}"
        if not image_path:
            image_path = image_id
        label = _first_field(row, _LABEL_KEYS)
        mode = _first_field(row, _MODE_KEYS)
        if label is not None:
            raw_label = label
            state = label_to_state(label)
            if mode is not None and label_to_state(mode) != state:
                state = UNCERTAIN
        elif mode is not None:
            raw_label = mode
            state = label_to_state(mode)
        else:
            raw_label = ""
            state = UNCERTAIN
        records.append(ImVisibleRecord(image_id, image_path, state, raw_label))
    return records


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


def perception_output(state: str, confidence: float) -> tuple:
    """The only legal ``(state, confidence)`` pair.  CROSS / go-ahead cannot pass through."""
    if state not in STATES or (isinstance(state, str) and _is_go_ahead_token(state)):
        raise ValueError(f"traffic-light perception cannot emit {state!r}")
    confidence_f = float(confidence)
    if not 0.0 <= confidence_f <= 1.0:
        raise ValueError("confidence must be in [0, 1]")
    return (state, confidence_f)


class TrafficLightThreeState:
    """HSV / OpenCV color-ROI baseline.  Offline scaffolding, not a crossing policy.

    ``predict(frame_bgr)`` returns ``(state, confidence)`` with ``state`` in
    ``{"green", "red", "uncertain"}`` — the W1 labels GREEN / RED / UNCERTAIN.
    ``green`` means the saturated pixels look green.  It does not mean CROSS,
    go-ahead, or “可以过街”.  ``auto_go`` is not a return value and cannot be set
    on :class:`TrafficLightDecision`.

    The baseline converts the frame to OpenCV HSV (hue 0–179) and counts pixels:

    * red: hue in ``[0, 12]`` or ``[168, 179]``, saturation ``>= 70``, value ``>= 70``
    * green: hue in ``[40, 95]``, saturation ``>= 70``, value ``>= 70``

    Fewer than ``min_color_pixels`` matching pixels, or a winner below
    ``min_dominance`` of the red+green total, yields ``uncertain``.  Otherwise
    the dominant colour is returned.  Confidence is that dominance ratio, or
    ``0`` when there is no coloured mass.  Blue, yellow, gray and mixed lamps
    stay uncertain on purpose.  A temporal caller should still use
    :class:`TrafficLightFilter`; this class does not confirm across frames.
    """

    AUTO_GO = False
    EMITS_CROSS = False

    RED_RANGES = (((0, 70, 70), (12, 255, 255)), ((168, 70, 70), (179, 255, 255)))
    GREEN_RANGE = ((40, 70, 70), (95, 255, 255))

    def __init__(self, min_color_pixels: int = 40, min_dominance: float = 0.75):
        if min_color_pixels < 1:
            raise ValueError("min_color_pixels must be >= 1")
        if not 0.5 < float(min_dominance) <= 1.0:
            raise ValueError("min_dominance must be in (0.5, 1]")
        self.min_color_pixels = int(min_color_pixels)
        self.min_dominance = float(min_dominance)

    def predict(self, frame_bgr) -> tuple:
        import numpy as np

        frame = None if frame_bgr is None else np.asarray(frame_bgr)
        if frame is None or frame.ndim != 3 or frame.shape[2] != 3 or frame.size == 0:
            return perception_output(UNCERTAIN, 0.0)
        if frame.dtype != np.uint8:
            scaled = frame.astype(np.float32)
            if scaled.size and float(np.nanmax(scaled)) <= 1.0:
                scaled = scaled * 255.0
            frame = np.clip(scaled, 0, 255).astype(np.uint8)
        try:
            import cv2
        except ImportError as exc:  # pragma: no cover - environment, not logic
            raise RuntimeError("TrafficLightThreeState baseline needs OpenCV") from exc
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        red = None
        for low, high in self.RED_RANGES:
            mask = cv2.inRange(hsv, np.array(low, dtype=np.uint8), np.array(high, dtype=np.uint8))
            red = mask if red is None else cv2.bitwise_or(red, mask)
        low, high = self.GREEN_RANGE
        green = cv2.inRange(hsv, np.array(low, dtype=np.uint8), np.array(high, dtype=np.uint8))
        red_n = int(np.count_nonzero(red))
        green_n = int(np.count_nonzero(green))
        total = red_n + green_n
        if total < self.min_color_pixels:
            return perception_output(UNCERTAIN, 0.0)
        if red_n >= green_n:
            dominant, state = red_n, RED
        else:
            dominant, state = green_n, GREEN
        confidence = dominant / float(total)
        if confidence < self.min_dominance:
            return perception_output(UNCERTAIN, confidence)
        return perception_output(state, confidence)
