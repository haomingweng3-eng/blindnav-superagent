"""World, walker kinematics and synthetic BEV rendering.

World frame: x lateral (+right), z along the corridor.  Walker yaw: 0 = along +z, positive = right.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

import numpy as np

from blindnav_pc.behavior import BehaviorMachine
from blindnav_pc.bev import BEVMemory, apply_footprint
from blindnav_pc.config import BEVConfig, Config, FREE, OCCUPIED, UNKNOWN
from blindnav_pc.ego import EgoTracker
from blindnav_pc.planner import VFHPlanner
from blindnav_pc.policy import (FORWARD, LEFT, OBSERVE, RECENTER, RIGHT, STOP, PolicyInput,
                                WalkingSafetyPolicy)


@dataclass
class Box:
    xmin: float
    xmax: float
    zmin: float
    zmax: float


@dataclass
class Corridor:
    half_width_m: float = 1.2
    length_m: float = 12.0
    boxes: List[Box] = field(default_factory=list)

    def solid(self, x: np.ndarray, z: np.ndarray) -> np.ndarray:
        s = (np.abs(x) > self.half_width_m)
        for b in self.boxes:
            s |= (x >= b.xmin) & (x <= b.xmax) & (z >= b.zmin) & (z <= b.zmax)
        return s

    def clearance(self, x: float, z: float) -> float:
        """Distance from a point to the nearest solid (walls + boxes), >= 0."""
        d = self.half_width_m - abs(x)
        for b in self.boxes:
            dx = max(b.xmin - x, 0.0, x - b.xmax)
            dz = max(b.zmin - z, 0.0, z - b.zmax)
            d = min(d, float(np.hypot(dx, dz)))
        return max(d, 0.0)


@dataclass
class Walker:
    x: float = 0.0
    z: float = 0.0
    yaw: float = 0.0           # rad, + right
    speed_mps: float = 1.0
    yaw_rate_dps: float = 90.0
    steer_deg: float = 20.0
    recenter_deg: float = 10.0
    body_radius_m: float = 0.2
    step_len_m: float = 0.7    # true step length (the config's L_step may differ -> PDR error)
    dist_m: float = 0.0        # path length walked so far

    @property
    def steps(self) -> int:
        return int(self.dist_m / self.step_len_m)

    def step(self, command: str, target_axis_deg: float, dt: float) -> None:
        """FORWARD aims along the axis; LEFT/RIGHT side-step by ``steer_deg``; RECENTER aims at the
        behaviour machine's goal bearing (axis frame, clipped)."""
        target = 0.0
        v = self.speed_mps
        if command == LEFT:
            target = -self.steer_deg
        elif command == RIGHT:
            target = self.steer_deg
        elif command == RECENTER:
            target = float(np.clip(target_axis_deg, -self.steer_deg, self.steer_deg))
        elif command in (STOP, OBSERVE):
            v = 0.0
        cur = np.rad2deg(self.yaw)
        dmax = self.yaw_rate_dps * dt
        cur += float(np.clip(target - cur, -dmax, dmax))
        self.yaw = np.deg2rad(cur)
        self.dist_m += v * dt
        self.x += v * dt * np.sin(self.yaw)
        self.z += v * dt * np.cos(self.yaw)


def render_bev(world: Corridor, w: Walker, bev: BEVConfig, fov_half_deg: float = 35.0,
               blind_m: float = 0.6, range_m: Optional[float] = None) -> np.ndarray:
    """Ground-truth BEV in the walker frame with a view cone and occlusion (behind a solid = unknown)."""
    nx, nz = bev.nx, bev.nz
    rng = range_m or bev.forward_m
    xl = (np.arange(nx) + 0.5) * bev.cell_m - bev.lateral_half_m
    zl = (np.arange(nz) + 0.5) * bev.cell_m
    XL, ZL = np.meshgrid(xl, zl)
    fwd = np.array([np.sin(w.yaw), np.cos(w.yaw)])
    right = np.array([np.cos(w.yaw), -np.sin(w.yaw)])
    wx = w.x + XL * right[0] + ZL * fwd[0]
    wz = w.z + XL * right[1] + ZL * fwd[1]
    solid = world.solid(wx, wz)
    ang = np.rad2deg(np.arctan2(XL, ZL))
    visible = (np.abs(ang) <= fov_half_deg) & (ZL >= blind_m) & (ZL <= rng)
    # occlusion: march from the walker towards each cell
    occluded = np.zeros_like(solid)
    dist = np.hypot(wx - w.x, wz - w.z)
    ux = (wx - w.x) / np.maximum(dist, 1e-9)
    uz = (wz - w.z) / np.maximum(dist, 1e-9)
    for k in range(1, int(rng / 0.05) + 3):
        s = k * 0.05
        part = s < dist - bev.cell_m * 0.75       # only solids clearly in front of the cell occlude it
        if not part.any():
            break
        occluded |= part & world.solid(w.x + s * ux, w.z + s * uz)
    states = np.full((nz, nx), UNKNOWN, dtype=np.uint8)
    vis = visible & ~occluded
    states[vis & ~solid] = FREE
    states[vis & solid] = OCCUPIED
    return apply_footprint(states, bev)      # the person's own footprint is known free; the rest of the blind zone is unknown


@dataclass
class SimResult:
    trace: List[dict]
    collided: bool
    min_clearance_m: float
    final_x: float
    final_z: float
    reached_end: bool

    def commands(self) -> List[str]:
        return [r["command"] for r in self.trace]

    def states(self) -> List[str]:
        return [r["state"] for r in self.trace]

    def timeline(self) -> List[tuple]:
        """Run-length encoded command sequence [(command, n_ticks), ...]."""
        out: List[list] = []
        for c in self.commands():
            if out and out[-1][0] == c:
                out[-1][1] += 1
            else:
                out.append([c, 1])
        return [tuple(x) for x in out]


def run_sim(world: Corridor, cfg: Optional[Config] = None, steps: int = 300, dt: float = 0.1,
            walker: Optional[Walker] = None, start_x: float = 0.0, memory: str = "oracle",
            gyro_drift_dps: float = 0.0, fov_half_deg: float = 35.0, blind_m: float = 0.6) -> SimResult:
    """Closed-loop simulation.

    memory: 'oracle' - the planner sees the current rendered BEV only (no BEVMemory);
            'full' | 'rot_only' | 'none' - the rendered BEV is fed through BEVMemory with
            gyro+steps / gyro only / no orientation input respectively.
    gyro_drift_dps: constant heading-sensor drift added to the reported heading.
    """
    cfg = cfg or Config()
    walker = walker or Walker(x=start_x)
    planner = VFHPlanner(cfg.planner, cfg.bev)
    policy = WalkingSafetyPolicy(cfg.policy)
    ego = EgoTracker(cfg.ego)
    beh = BehaviorMachine(cfg, ego)
    mem = BEVMemory(cfg.bev)
    trace: List[dict] = []
    min_clear = 1e9
    collided = False
    prev_h = 0.0
    for i in range(steps):
        t_ms = i * dt * 1000.0
        rendered = render_bev(world, walker, cfg.bev, fov_half_deg=fov_half_deg, blind_m=blind_m)
        if memory == "oracle":
            states = rendered
            ego.update(t_ms, walker.yaw + np.deg2rad(gyro_drift_dps * t_ms / 1000.0), walker.steps)
        else:
            head = None if memory == "none" else float(walker.yaw + np.deg2rad(gyro_drift_dps * t_ms / 1000.0))
            d = ego.update(t_ms, head, walker.steps if memory == "full" else None)
            fc = ((rendered == FREE) * 10).astype(np.int32)
            oc = ((rendered == OCCUPIED) * 10).astype(np.int32)
            mem.update(fc, oc, d)
            states = apply_footprint(mem.states(), cfg.bev)
        yaw_deg = beh.yaw_deg()
        tgt = planner.planner_target(yaw_deg, beh.target_axis_deg())
        plan = planner.plan(states, target_heading_deg=tgt, prev_heading_deg=prev_h, blind_m=blind_m + 0.1)
        prev_h = plan.best_heading_deg
        side = planner.desired_side(plan, yaw_deg, 0.0)
        pdec = policy.evaluate(PolicyInput(
            now_ms=t_ms, ground_ok=True, corridor_blocked=plan.corridor_blocked,
            corridor_min_dist_m=plan.corridor_min_dist_m, dead_end=plan.dead_end, desired_side=side))
        dec = beh.step(t_ms, pdec, plan)
        clr = world.clearance(walker.x, walker.z) - walker.body_radius_m
        min_clear = min(min_clear, clr)
        collided |= clr < 0.0
        trace.append({"t_ms": t_ms, "x": walker.x, "z": walker.z, "yaw_deg": float(np.rad2deg(walker.yaw)),
                      "command": dec.command, "text": dec.text, "state": dec.state, "clearance_m": clr,
                      "x_hat": dec.x_hat_m, "z_hat": dec.z_hat_m, "widened": plan.widened,
                      "plan_clearance_m": plan.clearance_after_inflation_m,
                      "best_heading_deg": plan.best_heading_deg, "best_side": side,
                      "corridor_blocked": plan.corridor_blocked, "dead_end": plan.dead_end,
                      "left_clear_m": plan.left_clear_m, "center_clear_m": plan.center_clear_m,
                      "right_clear_m": plan.right_clear_m})
        walker.step(dec.command, dec.target_heading_deg, dt)
        if walker.z >= world.length_m - 0.5:
            break
    return SimResult(trace, collided, float(min_clear), walker.x, walker.z,
                     walker.z >= world.length_m - 0.5)
