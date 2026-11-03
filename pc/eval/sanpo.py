"""SANPO loader (SANPO-Synthetic and SANPO-Real; CC BY 4.0, https://github.com/google-research-datasets/sanpo_dataset).

On-disk layout (same for both kinds, verified on the public bucket listing)::

    <session>/description.json                       camera intrinsics (fx, fy, cx, cy, image size), fps, session type
    <session>/camera_{head|chest}/camera_poses.csv   tracking_state,pos_x,pos_y,pos_z,q_x,q_y,q_z,q_w
    <session>/camera_{head|chest}/fixed_camera_poses.csv   (SANPO-Real only; the corrected orientations, 06/2025)
    <session>/camera_*/left/video_frames/NNNNNN.png
    <session>/camera_*/left/depth_maps/NNNNNN.float16.gz     float16 [H, W, H*W values]; metres, *z-depth*
    <session>/camera_*/left/segmentation_masks/NNNNNN.png    BGR png: channel 2 = semantic id, channel 0 = instance id

Facts checked on downloaded samples (see README "数据与约定"):
  * depth is z-depth (a plane fit is ~2x tighter with z-depth than with ray length);
  * SANPO-Real depth is 720x1280 while the image is 1242x2208 (the header holds the true (H, W));
  * poses (synthetic: camera_poses.csv; real: fixed_camera_poses.csv) behave as camera->world with OpenCV camera
    axes and a **Z-up** world: world-Z expressed in the camera frame points along image -y within ~7-11 deg of the
    ground-plane normal of the GT depth.  (The upstream README calls the frame "Y-up"; the data did not support
    that for the samples we looked at - ``world_up``/``cam_axes`` are therefore constructor arguments and
    ``gravity_check`` reports the measured angle.)  Real-data conclusions rest on ONE real session.
  * SANPO publishes no raw IMU stream in the bucket.  The *pose orientation* (ZED visual-inertial tracking for
    Real; simulator pose for Synthetic) is therefore used as the IMU proxy for gravity/heading, and the step
    events are *synthesised* from the pose displacement.  These are proxies, not a real IMU.
"""
from __future__ import annotations

import gzip
import json
import math
import os
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

import cv2
import numpy as np

from blindnav_pc.ego import CAM_OPENCV, orientation_to_gravity_heading

# SANPO labelmap ids (labelmap.json)
WALKABLE_IDS = (1, 3, 5, 6, 17)       # road, sidewalk, crosswalk, paved trail, other walkable surface
IGNORE_IDS = (0, 27)                  # unlabeled, sky: neither walkable nor an obstacle
DEFAULT_CAMERA_HEIGHT_M = {"camera_head": 1.85, "camera_chest": 1.35}   # from GT ground-plane fits on the samples


@dataclass
class SanpoInput:
    """Everything the system under test may see."""
    index: int
    t_ms: float
    bgr: np.ndarray
    gravity_cam: np.ndarray            # unit gravity in the OpenCV camera frame (IMU proxy)
    heading_rad: float                 # + = right (IMU proxy)
    tracking_ok: bool


@dataclass
class SanpoGT:
    """Scoring only."""
    depth_m: np.ndarray                # HxW float32, same size as SanpoInput.bgr, 0 = invalid
    seg: np.ndarray                    # HxW uint8 semantic ids (SANPO labelmap)


def read_depth_gz(path: str) -> np.ndarray:
    a = np.frombuffer(gzip.open(path).read(), dtype=np.float16)
    h, w = int(a[0]), int(a[1])
    if a.size != h * w + 2:
        raise ValueError(f"unexpected depth file layout: {path}")
    return a[2:].reshape(h, w).astype(np.float32)


def read_poses(path: str) -> Tuple[np.ndarray, np.ndarray, List[bool]]:
    pos, quat, ok = [], [], []
    with open(path, "r", encoding="utf-8") as fh:
        header = fh.readline().strip().split(",")
        col = {k: i for i, k in enumerate(header)}
        for line in fh:
            p = line.strip().split(",")
            if len(p) < len(header):
                continue
            ok.append(p[col["tracking_state"]].endswith("READY") and not p[col["tracking_state"]].endswith("NOT_READY"))
            pos.append([float(p[col[k]]) for k in ("pos_x", "pos_y", "pos_z")])
            quat.append([float(p[col[k]]) for k in ("q_x", "q_y", "q_z", "q_w")])
    return np.array(pos), np.array(quat), ok


class SanpoSession:
    def __init__(self, session_dir: str, camera: Optional[str] = None, max_width: int = 640,
                 world_up: Sequence[float] = (0.0, 0.0, 1.0), cam_axes: np.ndarray = CAM_OPENCV,
                 camera_height_m: Optional[float] = None):
        self.dir = session_dir
        cams = sorted(d for d in os.listdir(session_dir) if d.startswith("camera_"))
        if camera is None:
            if not cams:
                raise FileNotFoundError(f"no camera_* directory in {session_dir}")
            camera = cams[0]
        self.camera = camera
        self.cdir = os.path.join(session_dir, camera)
        self.ldir = os.path.join(self.cdir, "left")
        with open(os.path.join(session_dir, "description.json"), "r", encoding="utf-8") as fh:
            desc = json.load(fh)
        self.kind = desc.get("session_type", "unknown")
        cd = desc["session_camera_details"][0]
        p = cd["left_camera_params"]
        self.fps = float(cd.get("fps", 5.0))
        self.img_w, self.img_h = int(p["image_width"]), int(p["image_height"])
        s = min(1.0, max_width / self.img_w) if max_width else 1.0
        self.w, self.h = int(round(self.img_w * s)), int(round(self.img_h * s))
        self.K = np.array([[p["fx"] * s, 0, p["cx"] * s], [0, p["fy"] * s, p["cy"] * s], [0, 0, 1.0]])
        self.camera_height_m = float(camera_height_m if camera_height_m is not None
                                     else desc.get("camera_height_m", DEFAULT_CAMERA_HEIGHT_M.get(camera, 1.5)))
        fixed = os.path.join(self.cdir, "fixed_camera_poses.csv")
        self.pose_file = fixed if os.path.exists(fixed) else os.path.join(self.cdir, "camera_poses.csv")
        self.pos, self.quat, self.tracking = read_poses(self.pose_file)
        self.world_up, self.cam_axes = tuple(world_up), cam_axes
        fdir = os.path.join(self.ldir, "video_frames")
        self.frame_ids = sorted(int(f[:6]) for f in os.listdir(fdir) if f.endswith(".png"))

    # ---- inputs (what the system sees) ---------------------------------
    def inputs(self, i: int) -> SanpoInput:
        bgr = cv2.imread(os.path.join(self.ldir, "video_frames", f"{i:06d}.png"), cv2.IMREAD_COLOR)
        if bgr is None:
            raise FileNotFoundError(f"frame {i} missing")
        if bgr.shape[1] != self.w:
            bgr = cv2.resize(bgr, (self.w, self.h), interpolation=cv2.INTER_AREA)
        g, hd = orientation_to_gravity_heading(self.quat[i], self.world_up, self.cam_axes)
        return SanpoInput(i, i * 1000.0 / self.fps, bgr, g, hd, bool(self.tracking[i]))

    # ---- ground truth (scoring only) -----------------------------------
    def gt(self, i: int) -> SanpoGT:
        d = read_depth_gz(os.path.join(self.ldir, "depth_maps", f"{i:06d}.float16.gz"))
        d = cv2.resize(d, (self.w, self.h), interpolation=cv2.INTER_NEAREST)
        d = np.where(np.isfinite(d) & (d > 0), d, 0).astype(np.float32)
        m = cv2.imread(os.path.join(self.ldir, "segmentation_masks", f"{i:06d}.png"), cv2.IMREAD_UNCHANGED)
        sem = (m[..., 2] if m.ndim == 3 else m).astype(np.uint8)
        sem = cv2.resize(sem, (self.w, self.h), interpolation=cv2.INTER_NEAREST)
        return SanpoGT(d, sem)

    def has_gt(self, i: int) -> bool:
        return (os.path.exists(os.path.join(self.ldir, "depth_maps", f"{i:06d}.float16.gz"))
                and os.path.exists(os.path.join(self.ldir, "segmentation_masks", f"{i:06d}.png")))

    def available_frames(self) -> List[int]:
        return [i for i in self.frame_ids if i < len(self.quat) and self.has_gt(i)]

    # ---- motion proxies --------------------------------------------------
    def horizontal_xy(self) -> np.ndarray:
        """Camera position projected on the horizontal plane (2 columns: e1, e2 of the plane), metres."""
        u = np.asarray(self.world_up, dtype=np.float64)
        u = u / np.linalg.norm(u)
        a = np.eye(3)[int(np.argmin(np.abs(u)))]
        e1 = a - (a @ u) * u
        e1 /= np.linalg.norm(e1)
        e2 = np.cross(u, e1)
        return np.stack([self.pos @ e1, self.pos @ e2], axis=1)

    def step_times_ms(self, indices: Sequence[int], step_length_m: float = 0.7) -> np.ndarray:
        """PROXY step detector: one step event every ``step_length_m`` of horizontal path length (from the pose
        track).  Equivalent to a perfect pedometer whose step length equals ``step_length_m``."""
        xy = self.horizontal_xy()
        idx = list(indices)
        out, acc = [], 0.0
        for a, b in zip(idx[:-1], idx[1:]):
            seg = float(np.linalg.norm(xy[b] - xy[a]))
            n0, n1 = int(acc // step_length_m), int((acc + seg) // step_length_m)
            for k in range(n0, n1):
                f = ((k + 1) * step_length_m - acc) / max(seg, 1e-9)
                out.append((a + f * (b - a)) * 1000.0 / self.fps)
            acc += seg
        return np.array(out)

    def gt_gravity(self, i: int, max_depth_m: float = 12.0, stride: int = 6) -> Optional[np.ndarray]:
        """ORACLE gravity (unit, pointing down, camera frame) from a plane fitted to GT depth on GT walkable pixels,
        plus the plane height as second return of ``_gt_plane``.  Diagnostic only - never a system input."""
        r = self._gt_plane(i, max_depth_m, stride)
        return None if r is None else -r[0]

    def _gt_plane(self, i: int, max_depth_m: float = 12.0, stride: int = 6):
        gt = self.gt(i)
        sel = np.isin(gt.seg, WALKABLE_IDS) & (gt.depth_m > 0.5) & (gt.depth_m < max_depth_m)
        ys, xs = np.nonzero(sel)
        ys, xs = ys[::stride], xs[::stride]
        if len(ys) < 200:
            return None
        z = gt.depth_m[ys, xs].astype(np.float64)
        P = np.stack([(xs - self.K[0, 2]) / self.K[0, 0] * z, (ys - self.K[1, 2]) / self.K[1, 1] * z, z], 1)
        c = P.mean(0)
        n = np.linalg.svd(P - c, full_matrices=False)[2][2]
        if n[1] > 0:
            n = -n                                   # camera y points down -> the up normal has y < 0
        return n, float(abs(n @ c))

    def gravity_check(self, indices: Optional[Sequence[int]] = None, max_depth_m: float = 12.0,
                      stride: int = 6) -> Dict[str, float]:
        """Angle (deg) between the pose-derived 'up' and the ground-plane normal fitted to GT depth on GT walkable
        pixels, plus the GT camera height.  Run this once per new data source: it is the check of the pose
        convention (``world_up``/``cam_axes``)."""
        idx = list(indices) if indices is not None else self.available_frames()[:: max(1, len(self.available_frames()) // 8)]
        angs, hs = [], []
        for i in idx:
            r = self._gt_plane(i, max_depth_m, stride)
            if r is None:
                continue
            inp_g, _ = orientation_to_gravity_heading(self.quat[i], self.world_up, self.cam_axes)
            angs.append(math.degrees(math.acos(float(np.clip(r[0] @ (-inp_g), -1, 1)))))
            hs.append(r[1])
        if not angs:
            return {"n": 0}
        return {"n": len(angs), "up_angle_mean_deg": float(np.mean(angs)), "up_angle_max_deg": float(np.max(angs)),
                "gt_camera_height_m": float(np.median(hs))}


def load_labelmap(path: str) -> Dict[str, int]:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)
