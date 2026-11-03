"""Ground-plane calibration of RELATIVE depth without ARCore / LiDAR.

Idea (re-implemented from scratch; same family as Tian et al., ICRA 2021,
https://arxiv.org/abs/2101.05995): a monocular depth network such as Depth-Anything-V2
outputs *affine-invariant inverse depth* q (larger = nearer).  If we know the camera height h
above a flat ground and the gravity direction (IMU), every ground pixel has a theoretical metric
depth, which lets us fit  a*q + b = 1/Z  robustly and recover metric depth  D = 1/(a*q + b)
for the whole image (obstacles included).

Conventions: camera frame = OpenCV (x right, y down, z forward).  ``Z`` is the z-depth (the z
component of the 3-D point), not the range along the ray.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import cv2
import numpy as np

from .config import GroundConfig


def up_from_gravity(g_cam) -> np.ndarray:
    """Unit 'up' vector in the camera frame: up = -normalize(gravity).

    ``g_cam`` is the gravity direction (pointing DOWN) measured in the camera frame.
    """
    g = np.asarray(g_cam, dtype=np.float64).reshape(3)
    n = np.linalg.norm(g)
    if n < 1e-9:
        raise ValueError("gravity vector has zero length")
    return -g / n


def pixel_rays(K: np.ndarray, width: int, height: int, stride: int = 1) -> np.ndarray:
    """Rays r = K^-1 [u, v, 1] (z component = 1) for a pixel grid; shape (H', W', 3)."""
    us = np.arange(0, width, stride, dtype=np.float64)
    vs = np.arange(0, height, stride, dtype=np.float64)
    uu, vv = np.meshgrid(us, vs)
    x = (uu - K[0, 2]) / K[0, 0]
    y = (vv - K[1, 2]) / K[1, 1]
    return np.stack([x, y, np.ones_like(x)], axis=-1)


def flat_ground_depth(rays: np.ndarray, up: np.ndarray, h: float, eps: float = 0.02) -> np.ndarray:
    """Theoretical z-depth of a flat ground plane (camera h metres above it).

    Z = -h / (up . r); NaN where up . r >= -eps (ray does not hit the ground in front).
    """
    d = rays @ np.asarray(up, dtype=np.float64)
    z = np.full(d.shape, np.nan, dtype=np.float64)
    ok = d < -eps
    z[ok] = -h / d[ok]
    return z


def metric_depth(q: np.ndarray, a: float, b: float, max_depth: float = 15.0, eps: float = 1e-4) -> np.ndarray:
    """D = 1 / (a*q + b); NaN where a*q + b <= eps (beyond infinity / invalid) or D > max_depth."""
    den = a * q.astype(np.float64) + b
    out = np.full(den.shape, np.nan, dtype=np.float32)
    ok = den > eps
    out[ok] = (1.0 / den[ok]).astype(np.float32)
    out[out > max_depth] = np.nan
    return out


@dataclass
class GroundFit:
    a: float = float("nan")
    b: float = float("nan")
    ok: bool = False
    inlier_ratio: float = 0.0
    residual: float = float("inf")       # relative RMS residual of inliers, in units of 1/Z
    n_pixels: int = 0
    confidence: float = 0.0
    reused: bool = False                 # (a, b) copied from an earlier frame
    degraded: bool = False               # no usable calibration -> caller should OBSERVE / STOP
    reason: str = ""
    up_source: str = "imu"
    up: Optional[np.ndarray] = None

    def to_log(self) -> dict:
        d = {"a": _f(self.a), "b": _f(self.b), "ground_ok": bool(self.ok),
                "inlier_ratio": _f(self.inlier_ratio), "residual": _f(self.residual),
                "n_ground_px": int(self.n_pixels), "confidence": _f(self.confidence),
                "reused": bool(self.reused), "degraded": bool(self.degraded), "reason": self.reason}
        d["up_source"] = self.up_source
        return d


def _f(x: float) -> Optional[float]:
    return None if (x is None or not np.isfinite(x)) else round(float(x), 5)


def ground_samples(q: np.ndarray, ground_mask: np.ndarray, K: np.ndarray, up: np.ndarray,
                   cfg: GroundConfig, rng: Optional[np.random.Generator] = None):
    """Collect (q_i, 1/Z_i) pairs from the eroded ground mask in the lower ROI."""
    H, W = q.shape[:2]
    m = (ground_mask > 0).astype(np.uint8)
    if cfg.erode_px > 1:
        m = cv2.erode(m, np.ones((cfg.erode_px, cfg.erode_px), np.uint8))
    m[: int(H * cfg.roi_top_frac)] = 0
    vs, us = np.nonzero(m)
    if len(vs) == 0:
        return np.empty(0), np.empty(0)
    if len(vs) > cfg.max_samples:
        rng = rng or np.random.default_rng(cfg.seed)
        idx = rng.choice(len(vs), cfg.max_samples, replace=False)
        vs, us = vs[idx], us[idx]
    r = np.stack([(us - K[0, 2]) / K[0, 0], (vs - K[1, 2]) / K[1, 1], np.ones(len(us))], axis=-1)
    z = flat_ground_depth(r, up, cfg.camera_height_m, cfg.up_dot_eps)
    qi = q[vs, us].astype(np.float64)
    ok = np.isfinite(z) & (z > cfg.min_ground_depth_m) & (z < cfg.max_ground_depth_m) & np.isfinite(qi)
    return qi[ok], 1.0 / z[ok]


def fit_affine_ransac(q: np.ndarray, w: np.ndarray, cfg: GroundConfig) -> GroundFit:
    """Robust fit of a*q + b = w (w = 1/Z).  RANSAC with 2-point samples + least squares refit."""
    n = len(q)
    fit = GroundFit(n_pixels=n)
    if n < cfg.min_pixels:
        fit.reason = "too_few_ground_pixels"
        fit.degraded = True
        return fit
    qm = float(np.mean(q))
    if qm <= 0 or float(np.std(q)) / max(qm, 1e-9) < cfg.min_q_spread:
        fit.reason = "q_spread_too_small"
        fit.degraded = True
        return fit
    tol = np.maximum(cfg.rel_tol * w, cfg.abs_tol)
    rng = np.random.default_rng(cfg.seed)
    best_cnt, best = -1, None
    for _ in range(cfg.ransac_iters):
        i, j = rng.choice(n, 2, replace=False)
        dq = q[j] - q[i]
        if abs(dq) < 1e-6 * max(1.0, abs(qm)):
            continue
        a = (w[j] - w[i]) / dq
        if a <= 0:
            continue
        b = w[i] - a * q[i]
        inl = np.abs(a * q + b - w) <= tol
        c = int(inl.sum())
        if c > best_cnt:
            best_cnt, best = c, inl
    if best is None or best_cnt < max(8, cfg.min_pixels // 3):
        fit.reason = "ransac_failed"
        fit.degraded = True
        return fit
    inl = best
    for _ in range(3):  # LS refit on inliers, then re-select inliers
        A = np.stack([q[inl], np.ones(int(inl.sum()))], axis=1)
        sol, *_ = np.linalg.lstsq(A, w[inl], rcond=None)
        a, b = float(sol[0]), float(sol[1])
        inl_new = np.abs(a * q + b - w) <= tol
        if inl_new.sum() < 8:
            break
        inl = inl_new
    res = (a * q[inl] + b - w[inl]) / w[inl]
    rms = float(np.sqrt(np.mean(res ** 2)))
    ratio = float(inl.sum()) / n
    fit.a, fit.b, fit.inlier_ratio, fit.residual = a, b, ratio, rms
    # Confidence must increase with inlier support and decrease monotonically with fit error.
    fit.confidence = float(np.clip(ratio, 0.0, 1.0) *
                          (1.0 / (1.0 + rms / max(cfg.rel_tol, 1e-9))) *
                          np.clip(n / max(cfg.min_pixels, 1), 0.0, 1.0))
    fit.ok = bool(a > 0 and fit.confidence >= cfg.min_confidence)
    if a <= 0:
        fit.reason = "non_positive_scale"
    elif not fit.ok:
        fit.reason = "low_confidence"
    fit.degraded = not fit.ok
    return fit


def fit_ground(q: np.ndarray, ground_mask: np.ndarray, K: np.ndarray, up: np.ndarray,
               cfg: GroundConfig) -> GroundFit:
    qi, wi = ground_samples(q, ground_mask, K, up, cfg)
    return fit_affine_ransac(qi, wi, cfg)


def visual_plane_up(q: np.ndarray, ground_mask: np.ndarray, K: np.ndarray, imu_up: np.ndarray,
                    cfg: GroundConfig, a: Optional[float] = None,
                    b: Optional[float] = None) -> Optional[tuple[np.ndarray, float]]:
    """Estimate a ground normal from mask pixels and relative inverse depth.

    The initial affine fit supplies metric depth.  This is important: directly using
    ``1/q`` would turn the unknown affine offset into a false plane tilt.
    """
    if not cfg.enable_visual_up:
        return None
    H, W = q.shape[:2]
    m = (ground_mask > 0).astype(np.uint8)
    if cfg.erode_px > 1:
        m = cv2.erode(m, np.ones((cfg.erode_px, cfg.erode_px), np.uint8))
    m[: int(H * cfg.roi_top_frac)] = 0
    ys, xs = np.nonzero(m)
    if len(ys) < max(50, cfg.min_pixels // 2):
        return None
    qi = q[ys, xs].astype(np.float64)
    valid = np.isfinite(qi) & (qi > 1e-5)
    ys, xs, qi = ys[valid], xs[valid], qi[valid]
    if len(qi) < max(50, cfg.min_pixels // 2):
        return None
    if len(qi) > cfg.max_samples:
        rng = np.random.default_rng(cfg.seed)
        ix = rng.choice(len(qi), cfg.max_samples, replace=False)
        ys, xs, qi = ys[ix], xs[ix], qi[ix]
    if a is None or b is None or a <= 0:
        return None
    den = a * qi + b
    valid = den > 1e-5
    ys, xs, den = ys[valid], xs[valid], den[valid]
    if len(den) < max(50, cfg.min_pixels // 2):
        return None
    z = 1.0 / den
    P = np.stack([(xs - K[0, 2]) / K[0, 0] * z,
                  (ys - K[1, 2]) / K[1, 1] * z, z], axis=1)
    c = P.mean(0)
    _, _, vh = np.linalg.svd(P - c, full_matrices=False)
    n = vh[-1]
    n /= max(np.linalg.norm(n), 1e-12)
    imu = np.asarray(imu_up, dtype=np.float64); imu /= max(np.linalg.norm(imu), 1e-12)
    if n @ imu < 0: n = -n
    angle = float(np.degrees(np.arccos(np.clip(n @ imu, -1.0, 1.0))))
    return n, angle


class GroundCalibrator:
    """Stateful per-frame calibration: smoothing, short reuse, then degraded."""

    def __init__(self, cfg: Optional[GroundConfig] = None):
        self.cfg = cfg or GroundConfig()
        self.reset()

    def reset(self) -> None:
        self._a = None
        self._b = None
        self._stale = 0

    def update(self, q: np.ndarray, ground_mask: np.ndarray, K: np.ndarray, up: np.ndarray) -> GroundFit:
        selected = np.asarray(up, dtype=np.float64)
        source = "imu"
        # First pass uses gravity.  The resulting affine mapping removes q's
        # unknown offset before the visual plane is estimated.
        initial = fit_ground(q, ground_mask, K, selected, self.cfg)
        vp = visual_plane_up(q, ground_mask, K, selected, self.cfg,
                             initial.a if initial.ok else None,
                             initial.b if initial.ok else None)
        if vp is not None:
            vup, angle = vp
            if angle > self.cfg.prefer_visual_up_deg:
                selected, source = vup, "visual"
            elif angle <= self.cfg.max_gravity_tilt_deg:
                selected = (selected + vup); selected /= max(np.linalg.norm(selected), 1e-12)
                source = "fused"
        fit = fit_ground(q, ground_mask, K, selected, self.cfg)
        fit.up_source, fit.up = source, selected
        if fit.ok:
            if self._a is not None and self.cfg.ema < 1.0:
                e = self.cfg.ema
                fit.a = e * fit.a + (1 - e) * self._a
                fit.b = e * fit.b + (1 - e) * self._b
            self._a, self._b, self._stale = fit.a, fit.b, 0
            return fit
        # failed fit: reuse the previous calibration briefly, then degrade
        self._stale += 1
        if self._a is not None and self._stale <= self.cfg.reuse_frames:
            fit.a, fit.b, fit.reused, fit.ok, fit.degraded = self._a, self._b, True, True, False
            fit.reason = (fit.reason + "+reused").strip("+")
            return fit
        fit.ok, fit.degraded = False, True
        fit.reason = (fit.reason + "+stale").strip("+")
        return fit
