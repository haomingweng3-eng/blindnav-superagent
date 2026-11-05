"""Target-specific entrance grounding: find the target's sign, then its door.

The old ranking answered "which door is nearest / largest / most confident".  This module
answers "which door belongs to the shop the user asked for", and refuses to answer when the
evidence does not support it.  Every score is decomposed so a reviewer can see why a door was
ranked where it was.

Boxes are normalised ``(x1, y1, x2, y2)`` with the origin at the bottom-left, matching both
YOLO label files and Vision's bounding boxes.
"""
from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass
from typing import Iterable, Optional, Sequence

Box = tuple[float, float, float, float]


@dataclass(frozen=True)
class Sign:
    text: str
    box: Box
    confidence: float = 1.0


@dataclass(frozen=True)
class Door:
    box: Box
    confidence: float = 1.0
    distance_m: Optional[float] = None

    @property
    def center_x(self) -> float:
        return (self.box[0] + self.box[2]) / 2.0


@dataclass(frozen=True)
class EntranceCandidate:
    door: Door
    sign_text: str
    name_score: float
    spatial_score: float
    total: float


@dataclass(frozen=True)
class EntranceResult:
    candidates: tuple[EntranceCandidate, ...]
    state: str                 # "confirmed" | "ambiguous" | "target_not_seen"
    reason: str
    best_sign: Optional[Sign] = None


_PUNCT = re.compile(r"[\s　·.,!?;:'\"()\[\]{}<>《》「」【】、，。！？；：]+")


def normalize(text: str) -> str:
    """Case-fold, strip punctuation and full-width variants so OCR noise does not defeat matching."""
    folded = unicodedata.normalize("NFKC", text).casefold()
    return _PUNCT.sub("", folded)


def name_score(sign_text: str, target: str, aliases: Iterable[str] = ()) -> float:
    """1.0 exact, 0.9 alias, 0.7 containment (>=2 chars), else 0."""
    s = normalize(sign_text)
    if not s:
        return 0.0
    t = normalize(target)
    if s == t:
        return 1.0
    for alias in aliases:
        a = normalize(alias)
        if a and s == a:
            return 0.9
    for probe in [t, *(normalize(a) for a in aliases)]:
        if len(probe) >= 2 and (probe in s or s in probe):
            return 0.7
    return 0.0


def _intersection_x(a: Box, b: Box) -> float:
    return max(0.0, min(a[2], b[2]) - max(a[0], b[0]))


def spatial_score(sign: Sign, door: Door) -> float:
    """How plausibly this door belongs to the shop under this sign.

    A sign sits above its own entrance, and the entrance is horizontally under it.  Shared
    building facades mean proximity alone never proves ownership, so this tops out below the
    name evidence and is returned as a separate, inspectable number.
    """
    s, d = sign.box, door.box
    width = max(1e-6, min(s[2] - s[0], d[2] - d[0]))
    overlap = _intersection_x(s, d) / width
    if overlap <= 0.0:
        return 0.0
    gap = max(0.0, max(s[1], d[1]) - min(s[2], d[2]))
    vertical = math.exp(-gap / 0.15)
    above = 1.0 if s[1] >= d[3] else 0.0   # classic shopfront: sign above the opening
    return 0.5 * overlap + 0.4 * vertical + 0.1 * above


def ground(
    doors: Sequence[Door],
    signs: Sequence[Sign],
    target: str,
    aliases: Iterable[str] = (),
    min_name_score: float = 0.7,
    min_total: float = 0.5,
    tie_margin: float = 0.08,
) -> EntranceResult:
    """Rank doors by target ownership, not by distance; abstain when evidence is thin."""
    aliases = tuple(aliases)
    if not doors:
        return EntranceResult((), "target_not_seen", "no_door")
    if not signs:
        return EntranceResult((), "target_not_seen", "no_sign_text")

    best_sign, best_name = None, 0.0
    for sign in signs:
        score = name_score(sign.text, target, aliases)
        if score > best_name:
            best_sign, best_name = sign, score
    if best_sign is None or best_name < min_name_score:
        return EntranceResult((), "target_not_seen", "target_name_absent")

    scored = []
    for door in doors:
        spatial = spatial_score(best_sign, door)
        total = 0.7 * best_name + 0.3 * spatial
        scored.append(EntranceCandidate(door, best_sign.text, best_name, spatial, total))
    scored.sort(key=lambda c: c.total, reverse=True)

    plausible = [c for c in scored if c.spatial_score > 0.0 and c.total >= min_total]
    if not plausible:
        return EntranceResult((), "ambiguous", "no_door_under_sign", best_sign)
    if len(plausible) > 1 and plausible[0].total - plausible[1].total < tie_margin:
        return EntranceResult(tuple(plausible), "ambiguous", "two_doors_tie", best_sign)
    return EntranceResult((plausible[0],), "confirmed", "ok", best_sign)
