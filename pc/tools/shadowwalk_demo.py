#!/usr/bin/env python3
"""ShadowWalk corridor stub smoke demo (synthetic, no dataset / no model download).

Renders a synthetic downward-pitched camera view of a predefined corridor as a walkable mask +
metric depth map, runs the stub's mask->map back-projection, and prints one hint per frame.
The walker deliberately drifts right so the LEFT / STOP transitions are visible.

    python tools/shadowwalk_demo.py
    python tools/shadowwalk_demo.py --corridor right_arc100 --frames 16 --drift 0.9 --yaw-error-deg 8
    python tools/shadowwalk_demo.py --corridor-json my_corridor.json
"""
from __future__ import annotations

import argparse
import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from blindnav_pc.shadowwalk import (PRESETS, ShadowWalkConfig, ShadowWalkTracker, get_corridor,
                                    load_corridor, mask_to_map_points)

W, H = 320, 240


def render_corridor_view(corridor, pose, width=W, height=H, fx=240.0, pitch_deg=20.0,
                         camera_height_m=1.2, max_range_m=8.0):
    """Return (walkable mask, z-depth, K, up) for a ground-only synthetic view."""
    th = math.radians(pitch_deg)
    up = np.array([0.0, -math.cos(th), -math.sin(th)])
    fwd = np.array([0.0, -math.sin(th), math.cos(th)])
    K = np.array([[fx, 0.0, width / 2.0], [0.0, fx, height / 2.0], [0.0, 0.0, 1.0]])

    u, v = np.meshgrid(np.arange(width), np.arange(height))
    rx = (u - K[0, 2]) / K[0, 0]
    ry = (v - K[1, 2]) / K[1, 1]
    denom = ry * up[1] + up[2]                    # up . (rx, ry, 1)
    valid = denom < -1e-3
    depth = np.zeros((height, width), np.float32)
    depth[valid] = -camera_height_m / denom[valid]
    x_lat = rx * depth
    z_fwd = depth * (ry * fwd[1] + fwd[2])

    cos_y, sin_y = math.cos(pose.yaw_rad), math.sin(pose.yaw_rad)
    mx = pose.x_m + x_lat * cos_y + z_fwd * sin_y
    mz = pose.z_m - x_lat * sin_y + z_fwd * cos_y
    s, off = corridor.project_many(np.column_stack([mx.ravel(), mz.ravel()]))
    s0, _ = corridor.project(pose.x_m, pose.z_m)
    ahead = (s - s0).reshape(height, width)
    walkable = valid & (np.abs(off.reshape(height, width)) <= corridor.width_m / 2.0)
    walkable &= (ahead >= 0.5) & (ahead <= max_range_m)
    depth[~np.isfinite(depth)] = 0.0
    return walkable.astype(np.uint8), depth, K, up


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--corridor", default="straight60", help="preset: " + ", ".join(sorted(PRESETS)))
    ap.add_argument("--corridor-json", help="custom corridor JSON {name,width_m,points:[[x,z],...]}")
    ap.add_argument("--frames", type=int, default=12)
    ap.add_argument("--step-m", type=float, default=0.8, help="walked distance per demo frame")
    ap.add_argument("--drift", type=float, default=0.9, help="final rightward drift in metres")
    ap.add_argument("--yaw-error-deg", type=float, default=0.0)
    ap.add_argument("--height", type=float, default=1.2)
    args = ap.parse_args(argv)

    corridor = load_corridor(args.corridor_json) if args.corridor_json else get_corridor(args.corridor)
    cfg = ShadowWalkConfig()
    tracker = ShadowWalkTracker(corridor, cfg)
    print(f"corridor={corridor.name} length={corridor.length_m:.1f}m width={corridor.width_m:.1f}m "
          f"frames={args.frames}")
    print(f"{'i':>3} {'s(m)':>5} {'off(m)':>7} {'yaw(deg)':>9} {'cover':>5} {'pts':>5}  hint     reason")
    for i in range(args.frames):
        s = 2.0 + i * args.step_m
        if s > corridor.length_m - 1.0:
            break
        drift = args.drift * (i / max(1, args.frames - 1))
        pose = corridor.pose_at(s, offset_m=drift, yaw_error_rad=math.radians(args.yaw_error_deg))
        mask, depth, K, up = render_corridor_view(corridor, pose, camera_height_m=args.height)
        points = mask_to_map_points(mask, depth, K, up, args.height, pose, cfg)
        hint = tracker.update(points, pose)
        print(f"{i:>3} {hint.progress_m:>5.1f} {hint.offset_m:>7.2f} "
              f"{math.degrees(hint.yaw_error_rad):>9.1f} {hint.coverage:>5.2f} {hint.n_points:>5}  "
              f"{hint.hint:<8} {hint.reason}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
