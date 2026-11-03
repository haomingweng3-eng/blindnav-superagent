import math

import numpy as np

from blindnav_pc.behavior import (AVOID_LEFT, AVOID_RIGHT, CRUISE, PASSING, RECENTER as S_RECENTER,
                                  BehaviorMachine)
from blindnav_pc.bev import apply_footprint
from blindnav_pc.config import BEVConfig, Config, FREE, OCCUPIED
from blindnav_pc.ego import EgoTracker
from blindnav_pc.planner import VFHPlanner
from blindnav_pc.policy import FORWARD, LEFT, OBSERVE, RECENTER, RIGHT, STOP, PolicyDecision

B = BEVConfig()


def free_plan(cfg=None):
    cfg = cfg or Config()
    g = np.full((B.nz, B.nx), FREE, np.uint8)
    return VFHPlanner(cfg.planner, cfg.bev).plan(g), g


def wall_plan(cfg=None):
    cfg = cfg or Config()
    g = np.full((B.nz, B.nx), FREE, np.uint8)
    g[10:14, :] = OCCUPIED
    return VFHPlanner(cfg.planner, cfg.bev).plan(g)


def dec(cmd, reason=""):
    return PolicyDecision(cmd, cmd, reason, 3)


class Rig:
    """Feeds the machine with a straight walk of 'steps' and a lateral offset created by one heading excursion."""
    def __init__(self):
        self.cfg = Config()
        self.ego = EgoTracker(self.cfg.ego)
        self.m = BehaviorMachine(self.cfg, self.ego)
        self.t = 0.0
        self.steps = 0
        self.psi = 0.0
        self.plan, _ = free_plan(self.cfg)

    def tick(self, pol, heading=None, new_steps=0, plan=None):
        self.t += 100.0
        self.steps += new_steps
        self.psi = self.psi if heading is None else heading
        self.ego.update(self.t, self.psi, self.steps)
        return self.m.step(self.t, pol, plan or self.plan)


def drive_to_passing(rig, side=RIGHT):
    rig.tick(dec(FORWARD))
    for _ in range(4):
        rig.tick(dec(side), heading=math.radians(20 if side == RIGHT else -20), new_steps=1)
    rig.tick(dec(FORWARD), heading=0.0)


def test_avoid_then_passing_then_recenter_then_cruise():
    r = Rig()
    assert r.m.state == CRUISE
    drive_to_passing(r)
    assert r.m.state == PASSING
    assert r.ego.x_hat > 0.5                              # PDR saw the lateral excursion
    for _ in range(12):                                  # walk straight until passed the obstacle
        r.tick(dec(FORWARD), heading=0.0, new_steps=1)
        if r.m.state != PASSING:
            break
    assert r.m.state == S_RECENTER
    seen = set()
    for _ in range(40):                                  # steer left toward x=0 by following the commanded bearing
        d = r.tick(dec(FORWARD), heading=r.psi, new_steps=1)
        seen.add(d.command)
        if d.command == RECENTER:
            assert d.goal is not None and abs(d.goal[0]) < 1e-9 and d.goal[1] > r.ego.z_hat
            assert d.recenter_dir == "left"
            r.psi = math.radians(-10.0)
        else:
            r.psi = 0.0
        if r.m.state == CRUISE:
            break
    assert r.m.state == CRUISE and abs(r.ego.x_hat) < 0.2 and RECENTER in seen


def test_recenter_not_cancelled_by_one_bad_planner_frame():
    r = Rig()
    drive_to_passing(r)
    for _ in range(12):
        r.tick(dec(FORWARD), heading=0.0, new_steps=1)
        if r.m.state == S_RECENTER:
            break
    assert r.m.state == S_RECENTER
    d = r.tick(dec(FORWARD), heading=0.0, plan=wall_plan(r.cfg))          # 1 frame: path to goal not safe
    assert r.m.state == S_RECENTER and d.command == FORWARD               # hold, do not steer, do not cancel
    d = r.tick(dec(FORWARD), heading=0.0)                                 # next good frame resumes
    assert r.m.state == S_RECENTER and d.command == RECENTER


def test_stop_override_keeps_state():
    r = Rig()
    drive_to_passing(r)
    for _ in range(12):
        r.tick(dec(FORWARD), heading=0.0, new_steps=1)
        if r.m.state == S_RECENTER:
            break
    d = r.tick(dec(STOP, "blocked"), heading=0.0)
    assert d.command == STOP and d.stop_reason == "blocked" and r.m.state == S_RECENTER
    d = r.tick(dec(OBSERVE), heading=0.0)
    assert d.command == OBSERVE and r.m.state == S_RECENTER


def test_exit_requires_heading_and_free_centre_frames():
    r = Rig()
    r.m.state = S_RECENTER
    r.m._t_state = r.t
    r.tick(dec(FORWARD), heading=0.0)                     # x_hat = 0 but heading error 15 deg -> must not exit
    for _ in range(6):
        r.tick(dec(FORWARD), heading=math.radians(15.0))
    assert r.m.state == S_RECENTER
    r.tick(dec(FORWARD), heading=0.0, plan=wall_plan(r.cfg))              # centre corridor blocked: counter resets
    assert r.m.state == S_RECENTER
    r.tick(dec(FORWARD), heading=0.0)
    r.tick(dec(FORWARD), heading=0.0)
    assert r.m.state == S_RECENTER                        # centre free only 2 frames (< center_free_frames=3)
    r.tick(dec(FORWARD), heading=0.0)
    assert r.m.state == CRUISE


def test_recenter_times_out_and_reanchors():
    r = Rig()
    drive_to_passing(r)
    for _ in range(12):
        r.tick(dec(FORWARD), heading=0.0, new_steps=1)
        if r.m.state == S_RECENTER:
            break
    for _ in range(100):                                  # planner never says the goal path is safe
        r.tick(dec(FORWARD), heading=0.0, plan=wall_plan(r.cfg))
        if r.m.state == CRUISE:
            break
    assert r.m.state == CRUISE and abs(r.ego.x_hat) < 1e-9


def test_without_orientation_sensor_no_recenter():
    cfg = Config()
    ego = EgoTracker(cfg.ego)
    m = BehaviorMachine(cfg, ego)
    plan, _ = free_plan(cfg)
    t = 0.0
    for cmd in [RIGHT] * 5 + [FORWARD] * 60:
        t += 100.0
        ego.update(t, None, None)
        d = m.step(t, dec(cmd), plan)
        assert d.command != RECENTER
    assert m.state == CRUISE and not m.has_pdr and m.yaw_deg() == 0.0


def test_new_obstacle_during_recenter_goes_back_to_avoid():
    r = Rig()
    drive_to_passing(r)
    for _ in range(12):
        r.tick(dec(FORWARD), heading=0.0, new_steps=1)
        if r.m.state == S_RECENTER:
            break
    r.tick(dec(LEFT), heading=0.0)
    assert r.m.state == AVOID_LEFT
