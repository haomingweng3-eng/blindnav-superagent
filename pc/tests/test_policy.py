from blindnav_pc.policy import (FORWARD, LEFT, OBSERVE, RECENTER, RIGHT, STOP, PolicyInput,
                                WalkingSafetyPolicy, command_text)


def run(pol, seq, dt=100):
    out, t = [], 0
    for kw in seq:
        out.append(pol.evaluate(PolicyInput(now_ms=t, **kw)))
        t += dt
    return out


def test_texts():
    assert command_text(FORWARD) == "继续" and command_text(LEFT) == "稍向左"
    assert command_text(RIGHT) == "稍向右" and command_text(STOP) == "停下"
    assert command_text(RECENTER, "left") == "向左回正" and command_text(RECENTER, "right") == "向右回正"


def test_single_frame_never_stops():
    out = run(WalkingSafetyPolicy(), [dict(), dict(dead_end=True), dict(), dict()])
    assert all(o.command == FORWARD for o in out)


def test_persistent_stop_then_hold_then_release():
    out = run(WalkingSafetyPolicy(), [dict(dead_end=True)] * 5 + [dict()] * 12)
    cmds = [o.command for o in out]
    assert cmds[0] == FORWARD and cmds[1] == FORWARD       # needs streak >=2 AND 300 ms
    assert STOP in cmds[:5]
    first = cmds.index(STOP)
    assert cmds[first + 3] == STOP                          # hold ~800 ms after evidence disappears
    assert cmds[-1] == FORWARD


def test_side_needs_frames_and_dwell():
    out = run(WalkingSafetyPolicy(), [dict(desired_side="right")] * 6)
    cmds = [o.command for o in out]
    assert cmds[:4] == [FORWARD] * 4 and cmds[4] == RIGHT   # 3 frames AND 400 ms dwell
    out = run(WalkingSafetyPolicy(), [dict(desired_side="right"), dict(desired_side="left")] * 5)
    assert all(o.command == FORWARD for o in out)           # flipping sides never fires (no jitter)


def test_back_to_forward_needs_center_frames():
    out = run(WalkingSafetyPolicy(), [dict(desired_side="left")] * 6 + [dict(), dict()])
    cmds = [o.command for o in out]
    assert cmds[5] == LEFT and cmds[6] == LEFT and cmds[7] == FORWARD


def test_recenter_after_bypass_with_delay_and_done():
    pol = WalkingSafetyPolicy()
    seq = [dict(desired_side="right", lateral_offset_m=0.3)] * 6
    seq += [dict(lateral_offset_m=0.4)] * 20
    out = run(pol, seq)
    cmds = [o.command for o in out]
    assert RIGHT in cmds
    i_fwd = cmds.index(FORWARD, cmds.index(RIGHT))
    i_rec = cmds.index(RECENTER)
    assert (i_rec - i_fwd) * 100 >= 1100                    # waited ~1200 ms of straight walking
    assert out[i_rec].text == "向左回正" and out[i_rec].target_heading_deg < 0
    out2 = run(pol, [dict(desired_side="left", lateral_offset_m=0.05)] * 2)
    assert out2[-1].command == FORWARD                      # offset < done threshold -> finished


def test_ground_lost_observe_then_stop_and_recover():
    out = run(WalkingSafetyPolicy(), [dict(ground_ok=False)] * 30 + [dict()], dt=100)
    cmds = [o.command for o in out]
    assert cmds[2] == FORWARD                               # short loss: keep the last command
    assert cmds[6] == OBSERVE
    assert cmds[25] == STOP
    assert cmds[-1] == FORWARD
