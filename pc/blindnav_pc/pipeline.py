"""End-to-end per-frame pipeline: frame -> mask + inverse depth -> ground calibration -> BEV -> plan -> policy."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from .behavior import BehaviorMachine
from .bev import BEVMemory, build_bev_frame
from .config import Config
from .ego import EgoTracker
from .bev import level_frame
from .ground import GroundCalibrator, GroundFit, flat_ground_depth, metric_depth, up_from_gravity
from .layers import HazardAssessment, HeightLayerAnalyzer, empty_hazards_log
from .planner import PlanResult, VFHPlanner
from .policy import (FORWARD, LEFT, OBSERVE, RECENTER, RIGHT, STOP, PolicyDecision, PolicyInput,
                     WalkingSafetyPolicy)
from .safety import (BLOCKED, CLEAR, UNKNOWN as SAFETY_UNKNOWN, BLOCKED_TEXT, UNKNOWN_TEXT,
                     SafetyStateMachine, safety_text)


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
    state: str = "CRUISE"
    safety_state: str = CLEAR
    hazards: Optional[HazardAssessment] = None


class Pipeline:
    """Glue.  ``segmenter`` and ``depth`` only need ``predict(frame_bgr)``; ``K`` is the 3x3 intrinsics."""

    def __init__(self, segmenter, depth, K: np.ndarray, cfg: Optional[Config] = None):
        self.cfg = cfg or Config()
        self.seg, self.depth, self.K = segmenter, depth, np.asarray(K, dtype=np.float64)
        self.calib = GroundCalibrator(self.cfg.ground)
        self.memory = BEVMemory(self.cfg.bev)
        self.planner = VFHPlanner(self.cfg.planner, self.cfg.bev)
        self.policy = WalkingSafetyPolicy(self.cfg.policy)
        self.safety = SafetyStateMachine(self.cfg.safety, self.cfg.bev)
        self.layers = HeightLayerAnalyzer(self.cfg.layers, self.cfg.bev)
        self.ego = EgoTracker(self.cfg.ego)
        self.behavior = BehaviorMachine(self.cfg, self.ego)
        self._prev_h = 0.0

    def reset(self) -> None:
        self.calib.reset()
        self.memory.reset()
        self.policy.reset()
        self.safety.reset()
        self.layers.reset()
        self.behavior.reset()
        self._prev_h = 0.0

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

    def process(self, frame_bgr: np.ndarray, t_ms: float, gravity_cam, heading_rad: Optional[float] = None,
                steps_total: Optional[int] = None, gravity_source: str = "gravity") -> FrameResult:
        """``heading_rad``: yaw (+ = right) from a game-rotation-vector style sensor; ``steps_total``:
        cumulative step count.  Both optional: without a heading the BEV memory keeps only the current
        frame (+ a very short tail) and the behaviour machine cannot recenter.  ``gravity_source`` is
        ``gravity`` for TYPE_GRAVITY, ``attitude`` for an orientation-derived vector, or ``fixed``."""
        c = self.cfg
        lat = {}
        t0 = time.perf_counter()
        mask = self.seg.predict(frame_bgr)
        lat["seg"] = time.perf_counter() - t0
        t1 = time.perf_counter()
        q = self.depth.predict(frame_bgr)
        lat["depth"] = time.perf_counter() - t1

        t2 = time.perf_counter()
        ego = self.ego.update(t_ms, heading_rad, steps_total)
        yaw_deg = self.behavior.yaw_deg()
        up = up_from_gravity(gravity_cam)
        fit = self.calib.update(q, mask, self.K, up, up_source=gravity_source)
        up = fit.up if fit.up is not None else up
        depth_m = None
        plan = None
        hazards = None
        if fit.ok:
            depth_m = metric_depth(q, fit.a, fit.b, c.ground.max_depth_m)
            hazards = self.layers.update(depth_m, mask, self.K, up, c.ground.camera_height_m)
            free, occ = build_bev_frame(depth_m, mask, self.K, up, c.ground.camera_height_m, c.bev)
            states = self.memory.update(free, occ, ego)
            tgt = self.planner.planner_target(yaw_deg, self.behavior.target_axis_deg())
            plan = self.planner.plan(states, target_heading_deg=tgt, prev_heading_deg=self._prev_h,
                                     blind_m=self._blind_distance(up, frame_bgr.shape))
            self._prev_h = plan.best_heading_deg
            desired_side = self.planner.desired_side(plan, yaw_deg, 0.0)
        else:
            # degraded calibration: do NOT trust any geometry; keep warping/decaying memory only and let the policy hold/OBSERVE/STOP
            self.layers.reset()                   # drop evidence must be consecutive frames
            z = np.zeros_like(self.memory.occ_ev, dtype=np.int32)
            states = self.memory.update(z, z, ego)
            desired_side = "center"
        safety = self.safety.update(t_ms, fit, states, plan, hazards=hazards)
        if fit.ok and safety.state != SAFETY_UNKNOWN:
            inp = PolicyInput(now_ms=t_ms, ground_ok=True, corridor_blocked=plan.corridor_blocked,
                              corridor_min_dist_m=plan.corridor_min_dist_m, dead_end=plan.dead_end,
                              desired_side=desired_side)
        else:
            inp = PolicyInput(now_ms=t_ms, ground_ok=False)
        pdec = self._safety_policy_override(self.policy.evaluate(inp), safety)
        dec = self.behavior.step(t_ms, pdec, plan) if plan is not None else self.behavior._out(
            pdec.command, pdec.stop_reason)
        command, text, stop_reason = self._safety_command(dec, safety)
        lat["geometry"] = time.perf_counter() - t2
        lat["total"] = time.perf_counter() - t0

        log = {"t_ms": round(float(t_ms), 1), **fit.to_log()}
        if plan is not None:
            log.update(plan.to_log())
        log.update(hazards.to_log() if hazards is not None else empty_hazards_log())
        log.update({"command": command, "text": text, "stop_reason": stop_reason, "state": dec.state,
                    "safety_state": safety.state, "safety_reasons": list(safety.reasons),
                    "corridor_coverage": round(float(safety.corridor_coverage), 3),
                    "x_hat_m": round(dec.x_hat_m, 3), "z_hat_m": round(dec.z_hat_m, 3),
                    "yaw_rel_deg": round(yaw_deg, 2), "ego_mode": ego.mode, "ego_reliable": bool(ego.reliable),
                    "latency_ms": round(lat["total"] * 1000, 1),
                    "latency_stage_ms": {k: round(v * 1000, 1) for k, v in lat.items()}})
        return FrameResult(t_ms, command, text, mask, depth_m, states, plan, fit, log, dec.state,
                           safety.state, hazards)

    @staticmethod
    def _safety_policy_override(pdec: PolicyDecision, safety) -> PolicyDecision:
        """Feed UNKNOWN/BLOCKED into the behaviour machine as OBSERVE/STOP so it does not advance state."""
        if safety.state == SAFETY_UNKNOWN and pdec.command in (FORWARD, LEFT, RIGHT, RECENTER):
            return PolicyDecision(OBSERVE, UNKNOWN_TEXT, "safety_unknown", pdec.stable_frames)
        if safety.state == BLOCKED and pdec.command in (FORWARD, RECENTER):
            return PolicyDecision(STOP, BLOCKED_TEXT, "safety_blocked", pdec.stable_frames)
        return pdec

    @staticmethod
    def _safety_command(dec, safety) -> tuple:
        """Final output guard: UNKNOWN can never become a forward/side instruction."""
        command, text, reason = dec.command, dec.text, dec.stop_reason
        if safety.state == SAFETY_UNKNOWN:
            if command in (FORWARD, LEFT, RIGHT, RECENTER):
                command = OBSERVE
            text = UNKNOWN_TEXT
            reason = "safety_unknown:" + "+".join(safety.reasons)
        elif safety.state == BLOCKED:
            if command in (FORWARD, RECENTER):
                command = STOP
            if command in (STOP, OBSERVE):
                text = BLOCKED_TEXT
            else:
                text = safety_text(BLOCKED, command)
            reason = "safety_blocked:" + "+".join(safety.reasons)
        return command, text, reason
