"""Height layering + conservative drop risk on synthetic level-frame scenes (P0)."""
from types import SimpleNamespace

import numpy as np

from blindnav_pc.config import BEVConfig, FREE, LayerConfig, SafetyConfig
from blindnav_pc.ground import GroundFit
from blindnav_pc.layers import (ABOVE, BELOW, BODY, GROUND, HEAD, LOW, HeightLayerAnalyzer,
                                classify_heights)
from blindnav_pc.safety import BLOCKED, CLEAR, UNKNOWN, SafetyStateMachine

B = BEVConfig()


# ---------------- synthetic scenes (level frame: x right, z forward, h up) ----------------
def ground(z0=0.3, z1=3.0, x_half=1.2, step=0.04):
    xs = np.arange(-x_half, x_half + 1e-9, step)
    zs = np.arange(z0, z1 + 1e-9, step)
    X, Z = np.meshgrid(xs, zs)
    H = np.zeros_like(X)
    W = np.ones(X.shape, dtype=bool)
    D = np.sqrt(X ** 2 + Z ** 2 + 1.2 ** 2)
    return [a.ravel() for a in (X, Z, H, W, D)]


def box(x0, x1, z0, z1, h0, h1, step=0.03, walkable=False):
    xs = np.arange(x0, x1 + 1e-9, step)
    zs = np.arange(z0, z1 + 1e-9, step)
    hs = np.arange(h0, h1 + 1e-9, step)
    X, Z, H = np.meshgrid(xs, zs, hs, indexing="ij")
    X, Z, H = X.ravel(), Z.ravel(), H.ravel()
    W = np.full(X.shape, walkable)
    D = np.sqrt(X ** 2 + Z ** 2 + 1.2 ** 2)
    return [X, Z, H, W, D]


def cat(*parts):
    return [np.concatenate([p[i] for p in parts]) for i in range(5)]


def fit():
    return GroundFit(ok=True, confidence=0.9, residual=0.01, attitude_visual_deg=0.0)


def states():
    return np.full((B.nz, B.nx), FREE, np.uint8)


def plan():
    return SimpleNamespace(dead_end=False, corridor_blocked=False)


def run_scene(scene, frames=1):
    analyzer = HeightLayerAnalyzer(LayerConfig(), B)
    machine = SafetyStateMachine(SafetyConfig(), B)
    out = []
    for i in range(frames):
        hz = analyzer.analyze_points(*scene)
        out.append((hz, machine.update(i * 100.0, fit(), states(), plan(), hz)))
    return out


# ---------------- layering ----------------
def test_height_labels_relative_to_ground_plane():
    h = np.array([-0.30, -0.03, 0.00, 0.04, 0.10, 0.15, 0.40, 0.80, 0.81, 1.50, 2.00, 2.50])
    assert classify_heights(h, LayerConfig()).tolist() == [
        BELOW, GROUND, GROUND, GROUND, LOW, LOW, BODY, BODY, HEAD, HEAD, HEAD, ABOVE]


# ---------------- obstacles in layers ----------------
def test_low_obstacle_in_corridor_is_not_silently_safe():
    scene = cat(ground(), box(-0.35, 0.35, 1.0, 1.2, 0.06, 0.12))
    hz, dec = run_scene(scene)[0]
    assert hz.corridor_low_cells >= LayerConfig().low_block_cells
    assert "low_obstacle" in hz.reasons
    assert dec.state == BLOCKED and "low_obstacle" in dec.reasons


def test_body_obstacle_in_corridor_is_blocked():
    scene = cat(ground(), box(-0.35, 0.35, 1.0, 1.2, 0.30, 0.70))
    hz, dec = run_scene(scene)[0]
    assert hz.corridor_body_cells > 0 and hz.corridor_low_cells == 0
    assert dec.state == BLOCKED and "body_obstacle" in dec.reasons


def test_head_height_overhang_above_walkable_ground_is_blocked():
    scene = cat(ground(), box(-0.40, 0.40, 1.0, 1.4, 1.0, 1.6))
    hz, dec = run_scene(scene)[0]
    assert hz.observation.ground.any()                       # the floor under the overhang is visible
    assert hz.corridor_head_cells > 0 and hz.corridor_body_cells == 0
    assert dec.state == BLOCKED and "head_obstacle" in dec.reasons


# ---------------- drop risk ----------------
def test_step_down_needs_consecutive_frames_then_blocks():
    scene = cat(ground(z1=1.5), box(-1.2, 1.2, 1.5, 3.0, -0.35, -0.30))
    seq = run_scene(scene, frames=4)
    assert seq[0][0].corridor_drop_unconfirmed_cells > 0
    assert not seq[0][0].drop_risk.any()
    assert seq[0][1].state == UNKNOWN and "drop_unconfirmed" in seq[0][1].reasons
    assert seq[1][1].state == UNKNOWN                        # one good frame is not enough
    assert seq[2][0].corridor_drop_risk_cells > 0
    assert seq[2][1].state == BLOCKED and "drop_risk" in seq[2][1].reasons


def test_pothole_inside_observed_ground_becomes_drop_risk():
    g = ground()
    not_hole = ~((np.abs(g[0]) <= 0.25) & (g[1] >= 1.4) & (g[1] <= 1.8))
    ground_around = [a[not_hole] for a in g]
    bottom = box(-0.25, 0.25, 1.4, 1.8, -0.32, -0.30, step=0.03)
    hz, dec = run_scene(cat(ground_around, bottom), frames=3)[-1]
    assert hz.corridor_drop_risk_cells > 0
    assert dec.state == BLOCKED and "drop_risk" in dec.reasons


def test_missing_depth_beyond_ground_edge_is_unknown_never_a_pothole():
    hz, dec = run_scene(ground(z1=1.4))[0]
    assert hz.corridor_edges_unobserved > 0
    assert "drop_edge_unobserved" in hz.reasons
    assert "drop_risk" not in hz.reasons and not hz.drop_risk.any()
    assert dec.state == UNKNOWN and not dec.reliable


def test_single_frame_drop_noise_does_not_trigger_drop_risk():
    analyzer = HeightLayerAnalyzer(LayerConfig(), B)
    machine = SafetyStateMachine(SafetyConfig(), B)
    noisy = cat(ground(), box(-0.05, 0.05, 1.2, 1.3, -0.30, -0.28, step=0.02))
    hz = analyzer.analyze_points(*noisy)
    assert hz.corridor_drop_unconfirmed_cells > 0
    assert not hz.drop_risk.any() and "drop_risk" not in hz.reasons
    d = machine.update(0.0, fit(), states(), plan(), hz)
    assert d.state == UNKNOWN                                 # potential drop is never "clear"
    for i in range(1, 5):
        hz = analyzer.analyze_points(*ground())
        assert not hz.drop_risk.any() and "drop_risk" not in hz.reasons
        d = machine.update(i * 100.0, fit(), states(), plan(), hz)
        assert d.state != BLOCKED
    assert d.state == CLEAR                                   # hysteresis has run out
