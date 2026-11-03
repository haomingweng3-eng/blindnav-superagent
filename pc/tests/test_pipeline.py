"""Pipeline on a synthetic flat-ground scene (no network, no models)."""
import json

import numpy as np

from blindnav_pc.cli import main
from blindnav_pc.depth import SyntheticDepth
from blindnav_pc.ground import flat_ground_depth, pixel_rays, up_from_gravity
from blindnav_pc.io import gravity_from_pitch_roll
from blindnav_pc.pipeline import Pipeline
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
              "up_source", "input_up_source", "attitude_visual_deg", "visual_fallback"):
        assert k in log
    assert log["up_source"] == "gravity" and log["input_up_source"] == "gravity"
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


def test_cli_sim_all(capsys):
    assert main(["sim", "all"]) == 0
    out = capsys.readouterr().out
    for n in ("clear", "right_free", "left_free", "dead_end"):
        assert n in out
