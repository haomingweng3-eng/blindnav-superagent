"""Pipeline on a synthetic flat-ground scene (no network, no models)."""
import json

import numpy as np

from blindnav_pc.bev import level_frame
from blindnav_pc.cli import main
from blindnav_pc.depth import SyntheticDepth
from blindnav_pc.ground import flat_ground_depth, pixel_rays, up_from_gravity
from blindnav_pc.io import gravity_from_pitch_roll
from blindnav_pc.pipeline import Pipeline
from blindnav_pc.safety import UNKNOWN_TEXT
from blindnav_pc.segmentation import ConstantSegmenter

W, H = 320, 240
K = np.array([[250.0, 0, 160.0], [0, 250.0, 120.0], [0, 0, 1.0]])


def scene(obstacle_z=None):
    g = gravity_from_pitch_roll(15.0)
    up = up_from_gravity(g)
    z = flat_ground_depth(pixel_rays(K, W, H), up, 1.2)
    if obstacle_z is not None:                         # wall across the whole view at z-depth obstacle_z
        z[:192, :] = obstacle_z                         # the lower rows stay (farther) ground for calibration
    q = (1.0 / np.where(np.isfinite(z), z, 1e3) - 0.05) / 0.8
    return g, q.astype(np.float32)


def step_scene(edge_z=1.3, drop_m=0.30, pitch_deg=25.0):
    """Walkable ground up to ``edge_z``, then a lower ground plane (a step down).  The mask only
    covers the near ground so the affine calibration still fits the height-0 plane."""
    g = gravity_from_pitch_roll(pitch_deg)
    up = up_from_gravity(g)
    rays = pixel_rays(K, W, H)
    f, _ = level_frame(up)
    d_up, zc = rays @ up, rays @ f
    near = -1.2 / d_up                       # z-depth of the height-0 ground plane
    far = -(1.2 + drop_m) / d_up             # z-depth of the lower plane
    z_near, z_far = near * zc, far * zc
    ok = d_up < -0.02
    take_near = ok & (z_near > 0) & (z_near < edge_z)
    take_far = ok & (z_far >= edge_z)
    d = np.where(take_near, near, np.where(take_far, far, np.nan))
    q = (1.0 / d - 0.05) / 0.8
    return g, np.where(np.isfinite(d), q, np.nan).astype(np.float32)


def run(depth_q, seg, n=15, grav=None):
    pipe = Pipeline(seg, SyntheticDepth(depth_q), K)
    frame = np.zeros((H, W, 3), np.uint8)
    out = []
    for i in range(n):
        out.append(pipe.process(frame, i * 100.0, grav))
    return out


def test_open_ground_forward_and_logged_fields():
    g, q = scene()
    res = run(q, ConstantSegmenter(0.45), grav=g)
    assert all(r.command == "FORWARD" for r in res)
    log = res[-1].log
    assert abs(log["a"] - 0.8) < 0.05 and abs(log["b"] - 0.05) < 0.02
    for k in ("confidence", "inlier_ratio", "left_clear_m", "center_clear_m", "right_clear_m", "command", "latency_ms",
              "up_source", "input_up_source", "attitude_visual_deg", "visual_fallback",
              "layer_low_cells", "layer_body_cells", "layer_head_cells", "drop_risk_cells",
              "drop_unconfirmed_cells", "drop_unobserved_cells", "hazard_reasons"):
        assert k in log
    assert log["up_source"] == "gravity" and log["input_up_source"] == "gravity"
    assert log["hazard_reasons"] == []                 # open flat ground: no layer/drop hazard
    json.dumps(log)                                    # serialisable


def test_wall_ahead_stops():
    g, q = scene(obstacle_z=1.2)
    res = run(q, ConstantSegmenter(0.8), n=12, grav=g)
    assert res[-1].command == "STOP"


def test_no_ground_degrades_to_observe_then_stop():
    g, q = scene()
    res = run(q, ConstantSegmenter(1.0), n=40, grav=g)        # mask empty -> no calibration possible
    cmds = [r.command for r in res]
    assert not res[-1].fit.ok
    assert "OBSERVE" in cmds and cmds[-1] == "STOP"
    assert res[0].command != "FORWARD" or cmds.index("OBSERVE") >= 4   # never confidently FORWARD without ground


def test_dropped_frame_is_unknown_never_forward_and_recovers_after_hysteresis():
    g, q = scene()
    pipe = Pipeline(ConstantSegmenter(0.45), SyntheticDepth(q), K)
    frame = np.zeros((H, W, 3), np.uint8)
    for i in range(3):
        assert pipe.process(frame, i * 100.0, g).command == "FORWARD"
    r = pipe.process(frame, 5000.0, g)                        # 4.7 s input gap
    assert r.safety_state == "UNKNOWN" and r.command != "FORWARD" and r.text == UNKNOWN_TEXT
    assert "stale_frame" in r.log["safety_reasons"]
    for t in (5100.0, 5200.0):                                # needs N reliable frames, not one
        r = pipe.process(frame, t, g)
        assert r.safety_state == "UNKNOWN" and r.command != "FORWARD"
    r = pipe.process(frame, 5300.0, g)
    assert r.safety_state == "CLEAR" and r.command == "FORWARD"


def test_attitude_vs_visual_conflict_is_unknown_and_never_forward():
    g, q = scene()
    pipe = Pipeline(ConstantSegmenter(0.45), SyntheticDepth(q), K)
    frame = np.zeros((H, W, 3), np.uint8)
    for i in range(4):                                        # calibrate with a consistent attitude
        r = pipe.process(frame, i * 100.0, g, gravity_source="attitude")
        assert r.safety_state == "CLEAR" and r.command == "FORWARD"
    wrong = gravity_from_pitch_roll(0.0)                      # true pitch is 15 deg -> conflict
    for i in range(4, 9):
        r = pipe.process(frame, i * 100.0, wrong, gravity_source="attitude")
        assert r.safety_state == "UNKNOWN"
        assert "attitude_visual_conflict" in r.log["safety_reasons"]
        assert r.command != "FORWARD" and r.text == UNKNOWN_TEXT


def test_distorted_depth_is_unknown_and_never_forward():
    g, q = scene()
    rng = np.random.default_rng(0)
    bad = rng.uniform(-1.0, 2.0, q.shape).astype(np.float32)
    pipe = Pipeline(ConstantSegmenter(0.45), SyntheticDepth(bad), K)
    frame = np.zeros((H, W, 3), np.uint8)
    for i in range(10):
        r = pipe.process(frame, i * 100.0, g)
        assert r.safety_state == "UNKNOWN" and r.command != "FORWARD"
        assert r.text == UNKNOWN_TEXT


def test_unseen_corridor_region_is_unknown_and_never_forward():
    g, q = scene()
    q_near_nan = q.copy()
    q_near_nan[150:] = np.nan                                 # far rows still calibrate; the near/forward corridor has no depth
    pipe = Pipeline(ConstantSegmenter(0.45), SyntheticDepth(q_near_nan), K)
    frame = np.zeros((H, W, 3), np.uint8)
    for i in range(8):
        r = pipe.process(frame, i * 100.0, g)
        assert r.safety_state == "UNKNOWN" and r.command != "FORWARD"
        assert "corridor_unseen" in r.log["safety_reasons"]


def test_obstacle_ahead_is_blocked_and_never_forward():
    g, q = scene(obstacle_z=1.2)
    res = run(q, ConstantSegmenter(0.8), n=6, grav=g)
    assert all(r.command != "FORWARD" for r in res)
    assert any(r.safety_state == "BLOCKED" and "障碍" in r.text for r in res)
    assert res[-1].log["layer_body_cells"] > 0 and res[-1].log["layer_head_cells"] > 0
    assert {"body_obstacle", "head_obstacle"} <= set(res[-1].log["hazard_reasons"])


def test_step_down_is_unknown_then_drop_risk_never_forward():
    g, q = step_scene()
    pipe = Pipeline(ConstantSegmenter(0.9), SyntheticDepth(q), K)
    frame = np.zeros((H, W, 3), np.uint8)
    reasons, states = [], []
    for i in range(6):
        r = pipe.process(frame, i * 100.0, g)
        assert r.command != "FORWARD"                  # never forward across a possible drop
        reasons.append(set(r.log["hazard_reasons"]))
        states.append(r.safety_state)
    assert "drop_unconfirmed" in reasons[0] and "drop_risk" not in reasons[0]
    assert "drop_risk" in reasons[-1] and r.log["drop_risk_cells"] > 0
    assert states[0] == "UNKNOWN" and states[-1] == "BLOCKED"


def test_cli_sim_all(capsys):
    assert main(["sim", "all"]) == 0
    out = capsys.readouterr().out
    for n in ("clear", "right_free", "left_free", "dead_end"):
        assert n in out
