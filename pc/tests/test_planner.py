import numpy as np

from blindnav_pc.config import BEVConfig, FREE, OCCUPIED, PlannerConfig, UNKNOWN
from blindnav_pc.planner import VFHPlanner

B = BEVConfig()


def grid(fill=FREE):
    return np.full((B.nz, B.nx), fill, np.uint8)


def block(g, x0, x1, z0, z1, v=OCCUPIED):
    ix0, ix1 = int((x0 + B.lateral_half_m) / B.cell_m), int((x1 + B.lateral_half_m) / B.cell_m)
    g[int(z0 / B.cell_m):int(z1 / B.cell_m), ix0:ix1] = v


def planner():
    return VFHPlanner(PlannerConfig(), B)


def test_clear_goes_forward():
    r = planner().plan(grid())
    assert r.best_side == "center" and abs(r.best_heading_deg) < 1e-6
    assert not r.corridor_blocked and not r.dead_end


def test_obstacle_ahead_steers_to_freer_side():
    g = grid()
    block(g, -0.55, 0.05, 1.2, 1.8)                  # obstacle centre-left -> right is freer
    r = planner().plan(g)
    assert r.corridor_blocked
    assert r.best_side == "right" and r.best_heading_deg > 0
    g2 = grid()
    block(g2, -0.05, 0.55, 1.2, 1.8)
    r2 = planner().plan(g2)
    assert r2.best_side == "left" and r2.best_heading_deg < 0


def test_wall_everywhere_is_dead_end():
    g = grid()
    block(g, -1.5, 1.5, 1.2, 1.6)
    r = planner().plan(g)
    assert r.dead_end and r.corridor_blocked
    assert np.isclose(r.corridor_min_dist_m, 1.2, atol=0.11)


def test_narrow_gap_rejected_by_swept_width():
    g = grid()
    block(g, -1.5, -0.2, 1.2, 2.0)
    block(g, 0.2, 1.5, 1.2, 2.0)                      # 0.4 m gap < 0.6 m corridor
    r = planner().plan(g)
    assert r.dead_end


def test_unknown_is_never_free():
    g = grid(UNKNOWN)
    r = planner().plan(g)
    assert r.dead_end                                  # nothing observed -> no passable heading
    assert r.unknown_ratio.max() > 0.9
    g2 = grid()
    g2[10:, :] = UNKNOWN                               # only the first metre is known free
    assert planner().plan(g2).clear_m.max() < 1.5


def test_target_heading_biases_choice_and_clear_toward():
    p = planner()
    r = p.plan(grid(), target_heading_deg=10.0)
    assert 5.0 <= r.best_heading_deg <= 15.0
    assert p.side_of(3.0) == "center" and p.side_of(10.0) == "right" and p.side_of(-10.0) == "left"
    g = grid()
    block(g, -0.3, 0.3, 2.0, 2.5)
    r = p.plan(g)
    assert r.clear_toward(0.0) < 2.1 and r.clear_toward(30.0) > r.clear_toward(0.0)


def test_log_has_required_fields():
    d = planner().plan(grid()).to_log()
    for k in ("left_clear_m", "center_clear_m", "right_clear_m"):
        assert k in d
