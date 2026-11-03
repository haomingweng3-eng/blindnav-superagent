"""Synthetic fixture in the SANPO on-disk format (so the harness runs and is tested without any download).

Scene: flat ground, camera 1.5 m above it looking horizontally, walking straight at ``speed`` m per frame towards
a box (1.2 m wide, 1 m high) whose front face starts ``box_dist_m`` ahead and slightly to the right.  World Z-up,
poses are camera->world with OpenCV camera axes - the same convention verified for SANPO-Synthetic.
Depth is analytic (z-depth), segmentation ids follow the SANPO labelmap (1 road, 20 obstacle, 27 sky).
"""
from __future__ import annotations

import gzip
import json
import os
from typing import Optional

import cv2
import numpy as np

W, H = 320, 180
FX = FY = 240.0
CX, CY = W / 2.0, H / 2.0


def _render(trav: float, h: float, box_dist: float, box_x0: float, box_x1: float, box_h: float):
    v, u = np.mgrid[0:H, 0:W].astype(np.float64)
    depth = np.full((H, W), np.inf)
    seg = np.full((H, W), 27, np.uint8)
    below = v > CY + 1e-6
    zg = np.where(below, h * FY / np.maximum(v - CY, 1e-6), np.inf)
    depth[below] = zg[below]
    seg[below] = 1
    z_box = box_dist - trav
    if z_box > 0.3:
        x = (u - CX) / FX * z_box
        y_down = (v - CY) / FY * z_box                     # + = below the optical axis
        hit = (x >= box_x0) & (x <= box_x1) & (y_down <= h) & (y_down >= h - box_h) & (z_box < depth)
        depth[hit] = z_box
        seg[hit] = 20
    depth[~np.isfinite(depth)] = 0
    return depth.astype(np.float32), seg


def make_fixture_session(root: str, n_frames: int = 10, speed_m: float = 0.25, height_m: float = 1.5,
                         box_dist_m: float = 5.0, box_x: tuple = (0.1, 1.3), fps: float = 5.0,
                         seed: int = 0) -> str:
    """Write the fixture under ``root`` and return the session directory."""
    sess = os.path.join(root, "fixture-session")
    cam = os.path.join(sess, "camera_head")
    for d in ("video_frames", "depth_maps", "segmentation_masks"):
        os.makedirs(os.path.join(cam, "left", d), exist_ok=True)
    with open(os.path.join(sess, "description.json"), "w", encoding="utf-8") as fh:
        json.dump({"session_type": "synthetic", "session_video_metadata": {},
                   "session_camera_details": [{"fps": fps, "left_camera_params": {
                       "fx": FX, "fy": FY, "cx": CX, "cy": CY, "image_width": W, "image_height": H}}],
                   "session_camera_location": ["camera_head"], "camera_height_m": height_m}, fh)
    rng = np.random.default_rng(seed)
    tex = (rng.random((H, W, 3)) * 80 + 90).astype(np.uint8)
    rows = ["tracking_state,pos_x,pos_y,pos_z,q_x,q_y,q_z,q_w"]
    # camera->world, columns = images of the cam axes: x->X(right), y->-Z(down), z->Y(forward)
    q = _quat_from_matrix(np.array([[1, 0, 0], [0, 0, 1], [0, -1, 0]], dtype=float))
    for i in range(n_frames):
        trav = i * speed_m
        depth, seg = _render(trav, height_m, box_dist_m, box_x[0], box_x[1], 1.0)
        cv2.imwrite(os.path.join(cam, "left", "video_frames", f"{i:06d}.png"),
                    np.where(seg[..., None] == 20, np.uint8(30), tex))
        d16 = np.concatenate([np.array([H, W], np.float16), depth.astype(np.float16).ravel()])
        with gzip.open(os.path.join(cam, "left", "depth_maps", f"{i:06d}.float16.gz"), "wb") as fh:
            fh.write(d16.tobytes())
        m = np.zeros((H, W, 3), np.uint8)
        m[..., 2] = seg
        cv2.imwrite(os.path.join(cam, "left", "segmentation_masks", f"{i:06d}.png"), m)
        rows.append(f"TrackingState.READY,0.0,{trav},{height_m},{q[0]},{q[1]},{q[2]},{q[3]}")
    with open(os.path.join(cam, "camera_poses.csv"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(rows) + "\n")
    return sess


def _quat_from_matrix(R: np.ndarray):
    """(x, y, z, w) of a proper rotation matrix (no scipy)."""
    t = np.trace(R)
    if t > 0:
        s = np.sqrt(t + 1.0) * 2
        return ((R[2, 1] - R[1, 2]) / s, (R[0, 2] - R[2, 0]) / s, (R[1, 0] - R[0, 1]) / s, 0.25 * s)
    i = int(np.argmax(np.diag(R)))
    j, k = (i + 1) % 3, (i + 2) % 3
    s = np.sqrt(1.0 + R[i, i] - R[j, j] - R[k, k]) * 2
    q = [0.0] * 4
    q[i] = 0.25 * s
    q[j] = (R[j, i] + R[i, j]) / s
    q[k] = (R[k, i] + R[i, k]) / s
    q[3] = (R[k, j] - R[j, k]) / s
    return tuple(q)
