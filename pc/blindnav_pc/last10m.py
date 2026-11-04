"""Last-10 m door proposal stub (Entrances labels / any door detector output).

Input is a list of door candidates (normalised YOLO boxes, optional confidence and estimated
distance).  The stub keeps only candidates within the last 10 m, ranks them, and proposes at most
three.  It refuses instead of guessing when there is nothing reliable: no box, low confidence,
missing/out-of-range distance, or a near-tie at the cut-off.  It never steers or drives; the caller
still decides whether to approach a door.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional, Sequence, Tuple

import numpy as np

from .ground import flat_ground_depth

MAX_CANDIDATES = 3
DEFAULT_RANGE_M = 10.0


@dataclass(frozen=True)
class DoorCandidate:
    """Normalised YOLO box ``(cx, cy, w, h)``; ``distance_m`` is optional ground-plane range."""

    class_id: int
    cx: float
    cy: float
    w: float
    h: float
    confidence: float = 1.0
    distance_m: Optional[float] = None
    source: str = ""

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be in [0, 1]")
        if self.distance_m is not None and self.distance_m < 0.0:
            raise ValueError("distance_m must be non-negative")

    @property
    def bbox(self) -> Tuple[float, float, float, float]:
        return (self.cx - self.w / 2.0, self.cy - self.h / 2.0,
                self.cx + self.w / 2.0, self.cy + self.h / 2.0)

    @property
    def area(self) -> float:
        return self.w * self.h

    @property
    def bottom_y(self) -> float:
        return self.cy + self.h / 2.0

    def with_distance(self, distance_m: Optional[float]) -> "DoorCandidate":
        return DoorCandidate(self.class_id, self.cx, self.cy, self.w, self.h,
                             self.confidence, distance_m, self.source)


@dataclass(frozen=True)
class DoorProposal:
    candidates: Tuple[DoorCandidate, ...]
    candidate_count: int          # eligible candidates in the last 10 m
    total_count: int              # parsed candidates before filtering
    refused: bool
    reason: str
    max_candidates: int = MAX_CANDIDATES


def load_entrances_labels(path: str) -> list[DoorCandidate]:
    """Parse an Entrances YOLO label file: ``class cx cy w h`` (normalised), one per line."""
    out: list[DoorCandidate] = []
    with open(path, "r", encoding="utf-8-sig") as fh:
        for line in fh:
            parts = line.split()
            if len(parts) < 5:
                continue
            try:
                cls = int(float(parts[0]))
                cx, cy, w, h = (float(v) for v in parts[1:5])
            except ValueError:
                continue
            out.append(DoorCandidate(cls, cx, cy, w, h))
    return out


def estimate_distance_m(candidate: DoorCandidate, K: np.ndarray, image_size: Tuple[int, int],
                        up: np.ndarray, camera_height_m: float = 1.2) -> Optional[float]:
    """Ground-plane z-depth at the box bottom-centre (flat-ground approximation; None if no hit)."""
    width, height = int(image_size[0]), int(image_size[1])
    u = candidate.cx * width
    v = candidate.bottom_y * height
    ray = np.array([[(u - K[0, 2]) / K[0, 0], (v - K[1, 2]) / K[1, 1], 1.0]], dtype=np.float64)
    z = float(flat_ground_depth(ray, up, camera_height_m)[0])
    return z if math.isfinite(z) and z > 0.0 else None


def _rank_key(c: DoorCandidate):
    known = c.distance_m is not None
    return (not known, c.distance_m if known else math.inf, -c.area, -c.confidence)


def _ambiguous(a: DoorCandidate, b: DoorCandidate, margin_m: float, margin_area: float) -> bool:
    if a.distance_m is not None and b.distance_m is not None:
        return abs(a.distance_m - b.distance_m) <= margin_m
    if a.distance_m is None and b.distance_m is None:
        return abs(a.area - b.area) <= margin_area
    return False


def propose_doors(candidates: Sequence[DoorCandidate], max_candidates: int = MAX_CANDIDATES,
                  max_distance_m: float = DEFAULT_RANGE_M, min_confidence: float = 0.5,
                  ambiguity_margin_m: float = 0.5, ambiguity_margin_area: float = 0.01,
                  require_distance: bool = True) -> DoorProposal:
    """Filter, rank and propose at most three doors; refuse when the choice is not reliable.

    ``require_distance=True`` (default) means a candidate whose ground range is unknown is treated
    as uncertain: a "last 10 m" proposal is not possible without range.
    """
    if max_candidates < 1:
        raise ValueError("max_candidates must be >= 1")
    limit = min(int(max_candidates), MAX_CANDIDATES)
    total = len(candidates)
    if total == 0:
        return DoorProposal((), 0, 0, True, "no_candidate", limit)

    eligible = [c for c in candidates if c.confidence >= min_confidence]
    if not eligible:
        return DoorProposal((), 0, total, True, "low_confidence", limit)
    if require_distance and any(c.distance_m is None for c in eligible):
        return DoorProposal((), 0, total, True, "distance_unknown", limit)

    in_range = [c for c in eligible if c.distance_m is None or c.distance_m <= max_distance_m]
    if not in_range:
        return DoorProposal((), 0, total, True, "out_of_range", limit)

    ranked = sorted(in_range, key=_rank_key)
    if len(ranked) > limit and _ambiguous(ranked[limit - 1], ranked[limit],
                                          ambiguity_margin_m, ambiguity_margin_area):
        return DoorProposal((), len(ranked), total, True, "ambiguous", limit)
    picked = tuple(ranked[:limit])
    return DoorProposal(picked, len(ranked), total, False, "ok", limit)
