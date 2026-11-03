"""Inputs: video / webcam frames, camera intrinsics, optional IMU gravity log.

Camera frame = OpenCV convention (x right, y down, z forward).  Gravity vectors point DOWN.
"""
from __future__ import annotations

import csv
import json
import math
from typing import Iterator, Optional, Tuple, Union

import numpy as np


class VideoSource:
    """Iterate ``(frame_bgr, t_ms)`` from a video file or a webcam index."""

    def __init__(self, path_or_index: Union[str, int], max_frames: Optional[int] = None):
        import cv2
        self._cv2 = cv2
        self.cap = cv2.VideoCapture(path_or_index)
        if not self.cap.isOpened():
            raise ValueError(f"cannot open video source: {path_or_index!r}")
        self.max_frames = max_frames
        self.fps = float(self.cap.get(cv2.CAP_PROP_FPS) or 0.0)
        self.width = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        self.height = int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        self._is_file = isinstance(path_or_index, str)
        self._i = 0
        self._t0: Optional[float] = None

    def __iter__(self) -> Iterator[Tuple[np.ndarray, float]]:
        import time
        while self.cap is not None and (self.max_frames is None or self._i < self.max_frames):
            ok, frame = self.cap.read()
            if not ok:
                break
            if self._is_file:
                t = float(self.cap.get(self._cv2.CAP_PROP_POS_MSEC))
                if not (t > 0 or self._i == 0):
                    t = self._i / self.fps * 1000.0 if self.fps > 0 else float(self._i)
            else:                                     # webcam: wall clock
                self._t0 = self._t0 if self._t0 is not None else time.perf_counter()
                t = (time.perf_counter() - self._t0) * 1000.0
            self._i += 1
            yield frame, t

    def close(self) -> None:
        if self.cap is not None:
            self.cap.release()
            self.cap = None

    def __enter__(self) -> "VideoSource":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def load_camera(path: Optional[str], width: int, height: int, fov_deg: float = 70.0) -> np.ndarray:
    """3x3 intrinsics K.  JSON keys fx, fy, cx, cy (+ optional width/height of the calibration image,
    used to rescale).  Without a file: fx = fy from the horizontal FOV, principal point at the centre."""
    if path is None:
        f = (width / 2.0) / math.tan(math.radians(fov_deg) / 2.0)
        return np.array([[f, 0, width / 2.0], [0, f, height / 2.0], [0, 0, 1.0]])
    with open(path, "r", encoding="utf-8") as fh:
        d = json.load(fh)
    missing = [k for k in ("fx", "fy", "cx", "cy") if k not in d]
    if missing:
        raise ValueError(f"camera json is missing keys: {missing}")
    sx = width / d["width"] if d.get("width") else 1.0
    sy = height / d["height"] if d.get("height") else 1.0
    return np.array([[d["fx"] * sx, 0, d["cx"] * sx], [0, d["fy"] * sy, d["cy"] * sy], [0, 0, 1.0]])


class ImuLog:
    """Time-stamped gravity directions (camera frame).  ``gravity_at`` = nearest sample, unit length."""

    def __init__(self, t_ms, g):
        t = np.asarray(t_ms, dtype=np.float64)
        g = np.asarray(g, dtype=np.float64).reshape(-1, 3)
        order = np.argsort(t)
        self.t, self.g = t[order], g[order]

    def gravity_at(self, t_ms: float) -> np.ndarray:
        if len(self.t) == 0:
            raise ValueError("empty IMU log")
        i = int(np.searchsorted(self.t, t_ms))
        if i >= len(self.t) or (i > 0 and abs(self.t[i - 1] - t_ms) <= abs(self.t[i] - t_ms)):
            i -= 1
        v = self.g[i]
        n = float(np.linalg.norm(v))
        if n < 1e-9:
            raise ValueError(f"zero gravity vector in IMU log at t={self.t[i]} ms")
        return v / n


def load_imu_csv(path: str) -> ImuLog:
    """CSV with header ``t_ms,gx,gy,gz`` (extra columns ignored)."""
    with open(path, "r", newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        need = ("t_ms", "gx", "gy", "gz")
        missing = [k for k in need if k not in (reader.fieldnames or [])]
        if missing:
            raise ValueError(f"IMU csv needs columns t_ms,gx,gy,gz; missing: {missing}")
        rows = list(reader)
    try:
        t = [float(r["t_ms"]) for r in rows]
        g = [[float(r["gx"]), float(r["gy"]), float(r["gz"])] for r in rows]
    except ValueError as e:
        raise ValueError(f"IMU csv has a non-numeric value: {e}") from e
    return ImuLog(t, g)


def gravity_from_pitch_roll(pitch_deg: float, roll_deg: float = 0.0) -> np.ndarray:
    """Unit gravity (down) in the camera frame for a camera pitched DOWN by ``pitch_deg``, then rolled
    about the optical axis by ``roll_deg``.  pitch 0 -> (0,1,0); pitch p -> (0, cos p, sin p)."""
    p, r = math.radians(pitch_deg), math.radians(roll_deg)
    v = np.array([0.0, math.cos(p), math.sin(p)])
    c, s = math.cos(r), math.sin(r)
    return np.array([c * v[0] - s * v[1], s * v[0] + c * v[1], v[2]])


class OrientationLog:
    """Time-stamped orientation (game-rotation-vector style).  Gives gravity (camera frame) and heading (+ = right).
    CSV: ``t_ms,qx,qy,qz,qw`` (camera -> world, world Z up) **or** ``t_ms,heading_deg`` (heading only; gravity then
    comes from the separate gravity log / fixed pitch)."""

    def __init__(self, t_ms, quats=None, heading_rad=None, cam_axes=None):
        from .ego import CAM_OPENCV, orientation_to_gravity_heading
        t = np.asarray(t_ms, dtype=np.float64)
        order = np.argsort(t)
        self.t = t[order]
        cam_axes = CAM_OPENCV if cam_axes is None else cam_axes
        if quats is not None:
            q = np.asarray(quats, dtype=np.float64).reshape(-1, 4)[order]
            gh = [orientation_to_gravity_heading(x, cam_axes=cam_axes) for x in q]
            self.g = np.array([x[0] for x in gh])
            self.h = np.array([x[1] for x in gh])
        else:
            self.g = None
            self.h = np.asarray(heading_rad, dtype=np.float64)[order]

    def _i(self, t_ms: float) -> int:
        i = int(np.searchsorted(self.t, t_ms))
        if i >= len(self.t) or (i > 0 and abs(self.t[i - 1] - t_ms) <= abs(self.t[i] - t_ms)):
            i -= 1
        return i

    def heading_at(self, t_ms: float) -> float:
        return float(self.h[self._i(t_ms)])

    def gravity_at(self, t_ms: float) -> Optional[np.ndarray]:
        return None if self.g is None else self.g[self._i(t_ms)]


def load_orientation_csv(path: str, cam_axes=None) -> OrientationLog:
    with open(path, "r", newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        cols = reader.fieldnames or []
        rows = list(reader)
    if "t_ms" not in cols:
        raise ValueError("orientation csv needs a t_ms column")
    t = [float(r["t_ms"]) for r in rows]
    if all(k in cols for k in ("qx", "qy", "qz", "qw")):
        return OrientationLog(t, quats=[[float(r[k]) for k in ("qx", "qy", "qz", "qw")] for r in rows], cam_axes=cam_axes)
    if "heading_deg" in cols:
        return OrientationLog(t, heading_rad=[math.radians(float(r["heading_deg"])) for r in rows])
    raise ValueError("orientation csv needs qx,qy,qz,qw or heading_deg")


class StepLog:
    """Step events (TYPE_STEP_DETECTOR style); ``count_at(t)`` = cumulative number of steps up to t."""

    def __init__(self, step_times_ms):
        self.t = np.sort(np.asarray(step_times_ms, dtype=np.float64))

    def count_at(self, t_ms: float) -> int:
        return int(np.searchsorted(self.t, t_ms, side="right"))


def load_steps_csv(path: str) -> StepLog:
    """CSV with a ``t_ms`` column: one row per detected step."""
    with open(path, "r", newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        if "t_ms" not in (reader.fieldnames or []):
            raise ValueError("steps csv needs a t_ms column")
        return StepLog([float(r["t_ms"]) for r in reader])
