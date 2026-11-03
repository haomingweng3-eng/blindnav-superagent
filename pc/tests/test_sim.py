import pytest

from sim import run_scenario


def compress(tl):
    return [c for c, _ in tl]


def test_clear_corridor_forward_only():
    r = run_scenario("clear")
    assert compress(r.timeline()) == ["FORWARD"] and r.reached_end and not r.collided


@pytest.mark.parametrize("name,side,bias", [("right_free", "RIGHT", 1), ("left_free", "LEFT", -1)])
def test_bypass_then_recenter(name, side, bias):
    r = run_scenario(name)
    cmds = compress(r.timeline())
    assert not r.collided and r.min_clearance_m > 0.15 and r.reached_end
    assert side in cmds and "RECENTER" in cmds and "STOP" not in cmds
    assert cmds.index(side) < cmds.index("RECENTER")
    assert cmds[-1] == "FORWARD"
    assert abs(r.final_x) < 0.25                          # back close to the original line
    other = "LEFT" if side == "RIGHT" else "RIGHT"
    assert other not in cmds
    assert max(bias * t["x"] for t in r.trace) > 0.2      # actually went around on the free side


def test_dead_end_stops_without_collision():
    r = run_scenario("dead_end")
    assert r.timeline()[-1][0] == "STOP" and not r.collided and not r.reached_end
    assert r.final_z < 4.6
