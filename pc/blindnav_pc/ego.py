"""Ego-motion for the BEV memory and the behaviour state machine: orientation sensor + step events (PDR).

Inputs (all optional, per frame):
  * heading   - yaw about the gravity axis, radians, **positive = turning right**, from a
                game-rotation-vector style orientation (gyro+accel fused, no magnetometer, so it
                drifts slowly but is smooth).  Only differences matter: we use ``psi - psi_ref``.
  * steps     - cumulative step-event count (TYPE_STEP_DETECTOR style).

Output: ``EgoDelta`` = rotation + translation between consecutive frames, expressed in the
*previous* body frame (x right, z forward), plus a PDR pose in the episode reference frame
(x_hat = lateral offset from the reference path, z_hat = distance along it).

Modes: 'full' (gyro + steps), 'rot_only' (no step in this interval -> rotate, do not translate),
'none' (no orientation sensor: the caller must not warp and should shorten the history).
An 'unreliable' delta (time gap, absurd rotation) tells the memory to drop older history.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional, Sequence, Tuple

import numpy as np

from .config import EgoConfig


def wrap_pi(a: float) -> float:
    return (a + math.pi) % (2 * math.pi) - math.pi


@dataclass
class EgoDelta:
    dyaw_rad: float = 0.0       # + = turned right
    dx_m: float = 0.0           # + = moved right   (previous body frame)
    dz_m: float = 0.0           # forward
    steps: int = 0
    mode: str = "none"          # 'full' | 'rot_only' | 'none'
    reliable: bool = True


class EgoTracker:
    def __init__(self, cfg: Optional[EgoConfig] = None):
        self.cfg = cfg or EgoConfig()
        self.reset()

    def reset(self) -> None:
        self.psi_ref: Optional[float] = None
        self._psi_prev: Optional[float] = None
        self._steps_prev: Optional[int] = None
        self._t_prev: Optional[float] = None
        self.x_hat = 0.0
        self.z_hat = 0.0
        self.yaw_rel = 0.0          # psi - psi_ref, + = right of the reference direction

    @property
    def has_orientation(self) -> bool:
        return self._psi_prev is not None

    def reanchor(self, keep_z: bool = True) -> None:
        """Make the *current* position/heading the reference path (x_hat = 0, psi_ref = psi)."""
        if self._psi_prev is not None:
            self.psi_ref = self._psi_prev
        self.x_hat = 0.0
        self.yaw_rel = 0.0
        if not keep_z:
            self.z_hat = 0.0

    def update(self, t_ms: float, heading_rad: Optional[float], steps_total: Optional[int] = None) -> EgoDelta:
        c = self.cfg
        if heading_rad is None or not math.isfinite(heading_rad):
            self._t_prev = t_ms
            return EgoDelta(mode="none", reliable=True)
        if self.psi_ref is None:                       # episode start: reference line x=0, psi_ref
            self.psi_ref = heading_rad
            self._psi_prev = heading_rad
            self._steps_prev = steps_total
            self._t_prev = t_ms
            self.yaw_rel = 0.0
            return EgoDelta(mode="rot_only" if steps_total is None else "full", reliable=True)
        d = wrap_pi(heading_rad - self._psi_prev)
        dt = 0.0 if self._t_prev is None else (t_ms - self._t_prev)
        reliable = (0.0 <= dt <= c.max_dt_ms) and abs(math.degrees(d)) <= c.max_dyaw_deg
        n = 0
        if c.use_steps and steps_total is not None and self._steps_prev is not None:
            n = max(0, int(steps_total) - int(self._steps_prev))
        mode = "full" if (c.use_steps and steps_total is not None) else "rot_only"
        mid_prev = self.yaw_rel
        self._psi_prev = heading_rad
        self._steps_prev = steps_total if steps_total is not None else self._steps_prev
        self._t_prev = t_ms
        self.yaw_rel = wrap_pi(heading_rad - self.psi_ref)
        if not reliable:
            return EgoDelta(dyaw_rad=d, mode=mode, reliable=False)
        L = n * c.step_length_m
        dx, dz = L * math.sin(d / 2.0), L * math.cos(d / 2.0)
        mid = mid_prev + d / 2.0                         # PDR in the reference frame
        self.x_hat += L * math.sin(mid)
        self.z_hat += L * math.cos(mid)
        return EgoDelta(dyaw_rad=d, dx_m=dx, dz_m=dz, steps=n, mode=mode, reliable=True)


# --------------------------------------------------------------------------------------
# sensor conversions
# --------------------------------------------------------------------------------------
# camera axes used by the sensor/pose source -> OpenCV camera axes (x right, y down, z forward)
CAM_OPENCV = np.eye(3)
CAM_OPENGL = np.diag([1.0, -1.0, -1.0])      # x right, y up, -z forward (Android back camera, OpenGL, ARCore)


def quat_to_matrix(q_xyzw: Sequence[float]) -> np.ndarray:
    x, y, z, w = (float(v) for v in q_xyzw)
    n = math.sqrt(x * x + y * y + z * z + w * w)
    if n < 1e-12:
        raise ValueError("zero quaternion")
    x, y, z, w = x / n, y / n, z / n, w / n
    return np.array([
        [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
        [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
        [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])


def orientation_to_gravity_heading(q_xyzw: Sequence[float], world_up: Sequence[float] = (0.0, 0.0, 1.0),
                                   cam_axes: np.ndarray = CAM_OPENCV) -> Tuple[np.ndarray, float]:
    """(gravity unit vector in the OpenCV camera frame, heading yaw in radians, + = right).

    ``q`` rotates *camera -> world* (Android TYPE_GAME_ROTATION_VECTOR: device -> world, world Z up).
    ``cam_axes`` maps the source camera axes to OpenCV axes (identity, or CAM_OPENGL for Android/ARCore).
    Heading is measured from an arbitrary but fixed horizontal reference axis: only differences matter.
    """
    R = quat_to_matrix(q_xyzw)
    u = np.asarray(world_up, dtype=np.float64)
    u = u / np.linalg.norm(u)
    up_src = R.T @ u                                   # world up in the source camera axes
    g_cv = -(cam_axes @ up_src)
    f_world = R @ (cam_axes.T @ np.array([0.0, 0.0, 1.0]))
    fh = f_world - (f_world @ u) * u
    a = np.eye(3)[int(np.argmin(np.abs(u)))]
    e_ref = a - (a @ u) * u
    e_ref /= np.linalg.norm(e_ref)
    if np.linalg.norm(fh) < 1e-6:                      # looking straight up/down: heading undefined
        return g_cv / np.linalg.norm(g_cv), float("nan")
    ccw = math.atan2(float(np.cross(e_ref, fh) @ u), float(e_ref @ fh))
    return g_cv / np.linalg.norm(g_cv), -ccw


def steps_from_accel(t_ms: Sequence[float], acc_xyz: np.ndarray, min_interval_ms: float = 300.0,
                     thresh: float = 1.0) -> np.ndarray:
    """Very small step detector for when the phone has no step-detector sensor: peaks of the
    gravity-removed accelerometer magnitude (m/s^2).  Returns step times (ms)."""
    t = np.asarray(t_ms, dtype=np.float64)
    a = np.linalg.norm(np.asarray(acc_xyz, dtype=np.float64).reshape(-1, 3), axis=1)
    if len(a) < 3:
        return np.array([])
    a = a - np.median(a)
    k = max(1, int(round(100.0 / max(np.median(np.diff(t)), 1.0))))     # ~100 ms smoothing
    a = np.convolve(a, np.ones(k) / k, mode="same")
    out, last = [], -1e18
    for i in range(1, len(a) - 1):
        if a[i] > thresh and a[i] >= a[i - 1] and a[i] > a[i + 1] and t[i] - last >= min_interval_ms:
            out.append(t[i])
            last = t[i]
    return np.array(out)
