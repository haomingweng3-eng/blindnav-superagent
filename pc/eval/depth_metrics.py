"""Depth / scale metrics.  All depths are metric z-depth in metres; ``gt <= 0`` or non-finite = invalid."""
from __future__ import annotations

from typing import Dict, Optional, Sequence

import numpy as np


def _valid(pred, gt, lo, hi):
    pred = np.asarray(pred, dtype=np.float64)
    gt = np.asarray(gt, dtype=np.float64)
    return np.isfinite(pred) & (pred > 0) & np.isfinite(gt) & (gt >= lo) & (gt <= hi)


def depth_errors(pred: np.ndarray, gt: np.ndarray, min_m: float = 0.3, max_m: float = 80.0) -> Dict[str, float]:
    """AbsRel, RMSE (m), delta1 (max(p/g, g/p) < 1.25), plus the median scale ratio p/g.  Pixels with gt in
    [min_m, max_m] and a finite positive prediction only; ``n`` is the number of pixels used."""
    m = _valid(pred, gt, min_m, max_m)
    n = int(m.sum())
    if n == 0:
        return {"n": 0, "absrel": float("nan"), "rmse": float("nan"), "delta1": float("nan"),
                "scale_ratio": float("nan")}
    p, g = np.asarray(pred, dtype=np.float64)[m], np.asarray(gt, dtype=np.float64)[m]
    return {"n": n, "absrel": float(np.mean(np.abs(p - g) / g)), "rmse": float(np.sqrt(np.mean((p - g) ** 2))),
            "delta1": float(np.mean(np.maximum(p / g, g / p) < 1.25)), "scale_ratio": float(np.median(p / g))}


def depth_report(pred: np.ndarray, gt: np.ndarray, near_m: float = 5.0) -> Dict[str, dict]:
    """``all`` (gt up to 80 m) and ``near`` (0.3-``near_m`` m: the range that matters for walking)."""
    return {"all": depth_errors(pred, gt), "near": depth_errors(pred, gt, 0.3, near_m)}


def _rank(x: np.ndarray) -> np.ndarray:
    order = np.argsort(x, kind="mergesort")
    r = np.empty(len(x))
    r[order] = np.arange(len(x))
    for v in np.unique(x):                       # average ranks of ties
        k = x == v
        if k.sum() > 1:
            r[k] = r[k].mean()
    return r


def spearman(a: Sequence[float], b: Sequence[float]) -> Optional[float]:
    a, b = np.asarray(a, dtype=np.float64), np.asarray(b, dtype=np.float64)
    ok = np.isfinite(a) & np.isfinite(b)
    a, b = a[ok], b[ok]
    if len(a) < 3 or np.std(a) == 0 or np.std(b) == 0:
        return None
    ra, rb = _rank(a), _rank(b)
    return float(np.corrcoef(ra, rb)[0, 1])


def confidence_vs_error(confidences: Sequence[float], errors: Sequence[float]) -> Dict[str, Optional[float]]:
    """Spearman correlation between a per-frame confidence and the per-frame depth error (e.g. AbsRel).  A useful
    confidence is **negatively** correlated with the error (high confidence <-> low error).  ``None`` when there
    are fewer than 3 frames or one of the series is constant."""
    return {"spearman": spearman(confidences, errors), "n": int(len(confidences))}
