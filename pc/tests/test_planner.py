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
    r2 = planner().plan(g2)
    nar = np.abs(r2.headings_deg) <= 35.0 + 1e-6
    assert r2.clear_m[nar].max() < 1.5                 # unknown beyond the first metre ends every straight-ish path
    assert r2.dead_end                                 # ... and widening to +-60 does not invent a free path


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


def test_widen_before_stop_with_wider_view():
    """Everything within +-35 deg is blocked by a 1.8 m wide obstacle; with a +-3 m wide view the +-60 deg opening is
    seen free to the edge -> widened instead of STOP.  (With the default +-1.5 m view this is not provable.)"""
    b2 = BEVConfig(lateral_half_m=3.0)
    g = np.full((b2.nz, b2.nx), FREE, np.uint8)
    ix0, ix1 = int((-0.9 + 3.0) / b2.cell_m), int((0.9 + 3.0) / b2.cell_m)
    g[int(1.0 / b2.cell_m):int(1.3 / b2.cell_m), ix0:ix1] = OCCUPIED
    p = VFHPlanner(PlannerConfig(), b2)
    r = p.plan(g)
    assert r.corridor_blocked and r.widened and not r.dead_end
    assert abs(r.best_heading_deg) >= 55.0 and abs(r.best_heading_deg) <= 60.0
    p_narrow = VFHPlanner(PlannerConfig(widen_max_deg=35.0), b2)
    assert p_narrow.plan(g).dead_end                   # without widening it would have stopped


def test_no_widening_when_default_range_is_passable():
    r = planner().plan(grid())
    assert not r.widened


def test_wide_ray_ending_at_wall_is_not_an_opening():
    """The grid is only +-1.5 m wide: a 60 deg ray that leaves the view must not turn a full wall into a path."""
    g = grid()
    block(g, -1.5, 1.5, 1.2, 1.6)
    r = planner().plan(g)
    assert r.dead_end and not r.widened


def test_clearance_after_inflation_is_reported_and_signed():
    cfg = PlannerConfig()
    assert abs(cfg.half_width_m - 0.4) < 1e-9          # 0.5 m body width / 2 + 0.15 m margin
    g = grid()
    block(g, 0.2, 0.7, 1.0, 1.5)                       # obstacle edge 0.2 m right of the centre line
    r = planner().plan(g)
    assert r.inflation_m == cfg.half_width_m
    # centre line free but the body (0.25) + margin (0.15) touches the obstacle edge: negative -> ~-0.2
    assert np.isclose(planner().path_clearance(g, 0.0, 2.0), -0.2, atol=0.06)
    far = grid()
    block(far, 1.0, 1.5, 1.0, 1.5)
    assert planner().path_clearance(far, 0.0, 2.0) > 0.4


def test_unknown_near_field_does_not_cause_false_dead_end():
    """Camera sees nothing inside the first 0.6 m (blind zone UNKNOWN) but the footprint and everything beyond is free."""
    g = grid()
    g[:6, :] = UNKNOWN
    from blindnav_pc.bev import apply_footprint
    g = apply_footprint(g, B)
    r = planner().plan(g)
    assert not r.dead_end and not r.corridor_blocked
    assert r.unknown_ratio.max() > 0.0                 # penalised through delta * unknown_ratio ...
