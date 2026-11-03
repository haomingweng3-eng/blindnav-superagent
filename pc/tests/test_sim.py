import pytest

from blindnav_pc.config import Config
from sim import Walker, build_scenario, run_scenario, run_sim


def compress(tl):
    return [c for c, _ in tl]


def test_clear_corridor_forward_only():
    r = run_scenario("clear")
    assert compress(r.timeline()) == ["FORWARD"] and r.reached_end and not r.collided
    assert set(r.states()) == {"CRUISE"}


@pytest.mark.parametrize("memory", ["oracle", "full"])
@pytest.mark.parametrize("name,side,bias", [("right_free", "RIGHT", 1), ("left_free", "LEFT", -1)])
def test_bypass_then_recenter(name, side, bias, memory):
    r = run_scenario(name, memory=memory)
    cmds = compress(r.timeline())
    assert not r.collided and r.min_clearance_m > 0.15 and r.reached_end
    assert side in cmds and "RECENTER" in cmds and "STOP" not in cmds
    assert cmds.index(side) < cmds.index("RECENTER")
    assert cmds[-1] == "FORWARD"
    assert abs(r.final_x) < 0.25                          # back close to the original line
    other = "LEFT" if side == "RIGHT" else "RIGHT"
    assert other not in cmds
    assert max(bias * t["x"] for t in r.trace) > 0.2      # actually went around on the free side


def test_state_machine_sequence():
    r = run_scenario("right_free")
    seq = [s for i, s in enumerate(r.states()) if i == 0 or s != r.states()[i - 1]]
    assert seq == ["CRUISE", "AVOID_RIGHT", "PASSING", "RECENTER", "CRUISE"]


def test_recenter_is_a_state_not_a_single_frame_command():
    r = run_scenario("right_free")
    run = [i for i, s in enumerate(r.states()) if s == "RECENTER"]
    assert len(run) >= 10                                # held for many frames (>1 s), not one-frame flicker
    assert run[-1] - run[0] + 1 >= len(run) * 0.8        # ... and (almost) contiguous


def test_dead_end_stops_without_collision():
    r = run_scenario("dead_end")
    assert r.timeline()[-1][0] == "STOP" and not r.collided and not r.reached_end
    assert r.final_z < 4.6


def test_slalom_two_obstacles_both_sides():
    for mem in ("oracle", "full"):
        r = run_scenario("slalom", memory=mem)
        cmds = compress(r.timeline())
        assert not r.collided and r.reached_end and r.min_clearance_m > 0.1, mem
        assert "RIGHT" in cmds and "LEFT" in cmds and "STOP" not in cmds


def test_gap_wider_than_body_plus_margin_is_passed():
    r = run_scenario("gap_ok")
    assert r.reached_end and not r.collided and "STOP" not in compress(r.timeline())


def test_gap_narrower_than_body_stops():
    r = run_scenario("gap_too_narrow")
    assert r.timeline()[-1][0] == "STOP" and not r.collided and r.final_z < 5.0


def test_unknown_near_field_no_false_stop():
    """A 1.0-1.5 m camera blind zone on an empty corridor must not trigger a STOP."""
    for b in (0.6, 1.0, 1.5):
        r = run_scenario("clear", blind_m=b, memory="full")
        assert compress(r.timeline()) == ["FORWARD"] and r.reached_end, b


def test_blind_zone_obstacle_is_remembered_with_ego_motion():
    """Blind zone 1.5 m: the box leaves the view long before the walker has passed it.  With gyro+steps memory the
    obstacle stays known (warped by PDR); without ego-motion the same run hits it - which is exactly why the
    history must be dropped/shortened there (documented limitation, not a regression)."""
    ok = run_scenario("right_free", memory="full", blind_m=1.5)
    assert not ok.collided and ok.min_clearance_m > 0.15 and ok.reached_end
    bad = run_scenario("right_free", memory="none", blind_m=1.5)
    assert bad.collided


def test_step_length_error_is_tolerated():
    for L in (0.6, 0.8):                                  # +-15 % PDR scale error against the walker's true 0.7 m
        cfg = Config()
        cfg.ego.step_length_m = L
        r = run_sim(build_scenario("right_free"), cfg, steps=300, walker=Walker(step_len_m=0.7),
                    memory="full", blind_m=1.5)
        assert not r.collided and r.reached_end, L


def test_all_scenarios_run_in_all_memory_modes():
    for name in ("clear", "right_free", "dead_end"):
        for m in ("oracle", "full", "rot_only", "none"):
            r = run_scenario(name, memory=m, steps=60)
            assert len(r.trace) > 0
