"""Ego-motion tests on a SANPO session: gyro-only vs gyro + step events.

Inputs are the *proxies* documented in sanpo.py (pose orientation = IMU proxy, steps synthesised from the pose
track with a chosen step length).  Ground truth for the PDR test is the pose track itself.
"""
from __future__ import annotations

import copy
import math
from typing import Dict, Optional, Sequence

import numpy as np

from blindnav_pc.bev import BEVMemory
from blindnav_pc.config import FREE, OCCUPIED, Config
from blindnav_pc.ego import EgoTracker, orientation_to_gravity_heading, quat_to_matrix
from blindnav_pc.ground import up_from_gravity

from .bev_eval import oracle_evidence
from .sanpo import SanpoSession


def noisy_headings(headings: np.ndarray, t_ms: np.ndarray, bias_deg_per_s: float = 0.0, noise_deg: float = 0.0,
                   seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    t = (np.asarray(t_ms) - t_ms[0]) / 1000.0
    return headings + math.radians(bias_deg_per_s) * t + np.deg2rad(rng.normal(0, noise_deg, len(t)))


def _gt_track(session: SanpoSession, indices: Sequence[int]):
    """GT displacement of the camera in the frame of the first pose: (dx right, dz forward) in metres."""
    u = np.asarray(session.world_up, dtype=np.float64)
    u = u / np.linalg.norm(u)
    R0 = quat_to_matrix(session.quat[indices[0]])
    f = R0 @ (session.cam_axes.T @ np.array([0.0, 0.0, 1.0]))
    f = f - (f @ u) * u
    f /= np.linalg.norm(f)
    r = np.cross(f, u)
    d = session.pos[list(indices)] - session.pos[indices[0]]
    return d @ r, d @ f


def pdr_test(session: SanpoSession, indices: Sequence[int], mode: str, step_length_m: float = 0.7,
             bias_deg_per_s: float = 0.0, noise_deg: float = 0.0, cfg: Optional[Config] = None) -> Dict[str, float]:
    """Run EgoTracker over ``indices``.  mode: 'gyro_only' (no step events) or 'gyro_step'.  Returns the lateral /
    forward / total position error of x_hat, z_hat against the pose track (final and RMS)."""
    cfg = cfg or Config()
    idx = list(indices)
    t = np.array([i * 1000.0 / session.fps for i in idx])
    hs = np.array([orientation_to_gravity_heading(session.quat[i], session.world_up, session.cam_axes)[1] for i in idx])
    hs = noisy_headings(hs, t, bias_deg_per_s, noise_deg)
    steps_t = session.step_times_ms(idx, step_length_m)
    trk = EgoTracker(cfg.ego)
    xs, zs = [], []
    for k in range(len(idx)):
        n = int(np.searchsorted(steps_t, t[k], side="right")) if mode == "gyro_step" else None
        trk.update(float(t[k]), float(hs[k]), n)
        xs.append(trk.x_hat)
        zs.append(trk.z_hat)
    gx, gz = _gt_track(session, idx)
    ex, ez = np.array(xs) - gx, np.array(zs) - gz
    tot = np.hypot(ex, ez)
    return {"mode": mode, "n": len(idx), "path_len_m": float(gz[-1]), "final_dx_err_m": float(ex[-1]),
            "final_dz_err_m": float(ez[-1]), "final_pos_err_m": float(tot[-1]),
            "rms_pos_err_m": float(np.sqrt(np.mean(tot ** 2)))}


def memory_prediction_test(session: SanpoSession, indices: Sequence[int], mode: str, cfg: Optional[Config] = None,
                           step_length_m: float = 0.7, bias_deg_per_s: float = 0.0) -> Dict[str, float]:
    """How well does the ego-compensated memory *predict* the next frame's Oracle BEV?

    At frame k the memory built from frames < k (Oracle evidence) is warped with the ego delta (no current
    evidence) and compared with the Oracle BEV of frame k.  mode: 'none' (no orientation), 'rot_only' (gyro only),
    'full' (gyro + steps).  ``ghost_rate`` = Oracle-FREE cells the prediction calls OCCUPIED."""
    cfg = cfg or Config()
    idx = list(indices)
    t = np.array([i * 1000.0 / session.fps for i in idx])
    hs = np.array([orientation_to_gravity_heading(session.quat[i], session.world_up, session.cam_axes)[1] for i in idx])
    hs = noisy_headings(hs, t, bias_deg_per_s)
    steps_t = session.step_times_ms(idx, step_length_m)
    mem, trk = BEVMemory(cfg.bev), EgoTracker(cfg.ego)
    tp = {"occ": 0, "occ_hit": 0, "free": 0, "free_hit": 0, "free_ghost": 0}
    zero = np.zeros((cfg.bev.nz, cfg.bev.nx), dtype=np.int32)
    for k, i in enumerate(idx):
        gt = session.gt(i)
        g, _ = orientation_to_gravity_heading(session.quat[i], session.world_up, session.cam_axes)
        free, occ = oracle_evidence(gt.depth_m, gt.seg, session.K, up_from_gravity(g), session.camera_height_m, cfg)
        n = int(np.searchsorted(steps_t, t[k], side="right")) if mode == "full" else None
        ego = trk.update(float(t[k]), None if mode == "none" else float(hs[k]), n)
        if k > 0:
            pred = copy.deepcopy(mem).update(zero, zero, ego)
            ref = BEVMemory(cfg.bev).update(free, occ, None)
            o, f = ref == OCCUPIED, ref == FREE
            tp["occ"] += int(o.sum())
            tp["occ_hit"] += int((pred[o] == OCCUPIED).sum())
            tp["free"] += int(f.sum())
            tp["free_hit"] += int((pred[f] == FREE).sum())
            tp["free_ghost"] += int((pred[f] == OCCUPIED).sum())
        mem.update(free, occ, ego)
    return {"mode": mode, "frames": len(idx) - 1,
            "occ_recall": tp["occ_hit"] / tp["occ"] if tp["occ"] else float("nan"),
            "free_recall": tp["free_hit"] / tp["free"] if tp["free"] else float("nan"),
            "ghost_rate": tp["free_ghost"] / tp["free"] if tp["free"] else float("nan"),
            "n_occ_cells": tp["occ"], "n_free_cells": tp["free"]}
