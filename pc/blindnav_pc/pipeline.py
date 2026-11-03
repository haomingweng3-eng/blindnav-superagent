"""End-to-end per-frame pipeline: frame -> mask + inverse depth -> ground calibration -> BEV -> plan -> policy."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from .bev import BEVMemory, build_bev_frame
from .config import Config
from .bev import level_frame
from .ground import GroundCalibrator, GroundFit, flat_ground_depth, metric_depth, up_from_gravity
from .planner import PlanResult, VFHPlanner
from .policy import OBSERVE, PolicyInput, WalkingSafetyPolicy


@dataclass
class FrameResult:
    t_ms: float
    command: str
    text: str
    mask: np.ndarray
    depth_metric: Optional[np.ndarray]
    states: np.ndarray
    plan: Optional[PlanResult]
    fit: GroundFit
    log: dict = field(default_factory=dict)


class Pipeline:
    """Glue.  ``segmenter`` and ``depth`` only need ``predict(frame_bgr)``; ``K`` is the 3x3 intrinsics."""

    def __init__(self, segmenter, depth, K: np.ndarray, cfg: Optional[Config] = None):
        self.cfg = cfg or Config()
        self.seg, self.depth, self.K = segmenter, depth, np.asarray(K, dtype=np.float64)
        self.calib = GroundCalibrator(self.cfg.ground)
        self.memory = BEVMemory(self.cfg.bev)
        self.planner = VFHPlanner(self.cfg.planner, self.cfg.bev)
        self.policy = WalkingSafetyPolicy(self.cfg.policy)
        self._target = 0.0
        self._prev_h = 0.0

    def reset(self) -> None:
        self.calib.reset()
        self.memory.reset()
        self.policy.reset()
        self._target = self._prev_h = 0.0

    def _blind_distance(self, up: np.ndarray, shape) -> float:
        """Forward distance (level frame) of the ground at the bottom-centre pixel: nearer ground is outside the
        camera view, so 'unknown' there is a camera blind zone and must not be read as an obstacle."""
        H, W = shape[:2]
        r = np.array([[(W / 2 - self.K[0, 2]) / self.K[0, 0], (H - 1 - self.K[1, 2]) / self.K[1, 1], 1.0]])
        z = flat_ground_depth(r, up, self.cfg.ground.camera_height_m, self.cfg.ground.up_dot_eps)[0]
        if not np.isfinite(z):
            return self.cfg.planner.near_unknown_ignore_m
        fwd, _ = level_frame(up)
        d = float((r[0] * z) @ fwd)
        return float(np.clip(d + 0.1, self.cfg.planner.near_unknown_ignore_m, 3.0))

    def process(self, frame_bgr: np.ndarray, t_ms: float, gravity_cam) -> FrameResult:
        c = self.cfg
        lat = {}
        t0 = time.perf_counter()
        mask = self.seg.predict(frame_bgr)
        lat["seg"] = time.perf_counter() - t0
        t1 = time.perf_counter()
        q = self.depth.predict(frame_bgr)
        lat["depth"] = time.perf_counter() - t1

        t2 = time.perf_counter()
        up = up_from_gravity(gravity_cam)
        fit = self.calib.update(q, mask, self.K, up)
        depth_m = None
        if fit.ok:
            depth_m = metric_depth(q, fit.a, fit.b, c.ground.max_depth_m)
            free, occ = build_bev_frame(depth_m, mask, self.K, up, c.ground.camera_height_m, c.bev)
            states = self.memory.update(free, occ)
            plan = self.planner.plan(states, target_heading_deg=self._target, prev_heading_deg=self._prev_h,
                                     blind_m=self._blind_distance(up, frame_bgr.shape))
            self._prev_h = plan.best_heading_deg
            inp = PolicyInput(now_ms=t_ms, ground_ok=True, corridor_blocked=plan.corridor_blocked,
                              corridor_min_dist_m=plan.corridor_min_dist_m, dead_end=plan.dead_end,
                              desired_side=plan.best_side)
        else:
            # degraded calibration: do NOT trust any geometry; decay memory only and let the policy hold/OBSERVE/STOP
            states = self.memory.update(np.zeros_like(self.memory.occ_ev, dtype=np.int32),
                                        np.zeros_like(self.memory.occ_ev, dtype=np.int32))
            plan = None
            inp = PolicyInput(now_ms=t_ms, ground_ok=False)
        dec = self.policy.evaluate(inp)
        self._target = dec.target_heading_deg
        lat["geometry"] = time.perf_counter() - t2
        lat["total"] = time.perf_counter() - t0

        log = {"t_ms": round(float(t_ms), 1), **fit.to_log()}
        if plan is not None:
            log.update(plan.to_log())
        log.update({"command": dec.command, "text": dec.text, "stop_reason": dec.stop_reason,
                    "offset_est_m": round(dec.offset_est_m, 3),
                    "latency_ms": round(lat["total"] * 1000, 1),
                    "latency_stage_ms": {k: round(v * 1000, 1) for k, v in lat.items()}})
        return FrameResult(t_ms, dec.command, dec.text, mask, depth_m, states, plan, fit, log)
