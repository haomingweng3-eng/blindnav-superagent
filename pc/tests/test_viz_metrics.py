import numpy as np
import pytest

from blindnav_pc.config import BEVConfig, PlannerConfig
from blindnav_pc.metrics import compute_metrics, format_report, load_labels_csv
from blindnav_pc.planner import VFHPlanner
from blindnav_pc.viz import compose, overlay_frame, put_text, render_bev


def recs(cmds, dt=100):
    return [{"t_ms": i * dt, "command": c, "latency_ms": 10.0 + i} for i, c in enumerate(cmds)]


def test_metrics_flips_and_latency():
    m = compute_metrics(recs(["FORWARD", "FORWARD", "LEFT", "FORWARD", "STOP", "STOP"]))
    assert m["command_flips"] == 3 and m["max_flips_in_window"] == 3
    assert m["latency_ms_p50"] == pytest.approx(12.5) and m["false_stop_rate"] is None


def test_metrics_false_and_missed_stop(tmp_path):
    r = recs(["FORWARD", "STOP", "STOP", "FORWARD"])
    labels = [{"t_ms": 0, "should_stop": 0}, {"t_ms": 100, "should_stop": 0}, {"t_ms": 200, "should_stop": 1},
              {"t_ms": 300, "should_stop": 1}]
    m = compute_metrics(r, labels)
    assert m["false_stop_rate"] == 0.5 and m["missed_stop_rate"] == 0.5
    p = tmp_path / "l.csv"
    p.write_text("t_ms,should_stop\n0,0\n100,1\n")
    assert load_labels_csv(str(p)) == [{"t_ms": 0.0, "should_stop": 0}, {"t_ms": 100.0, "should_stop": 1}]
    rep = format_report(m)
    for k in ("误停率", "漏停率", "指令翻转次数", "延迟"):
        assert k in rep


def test_metrics_action_labels_unsafe_forward_wrong_side_oscillation(tmp_path):
    cmds = ["FORWARD", "FORWARD", "RIGHT", "LEFT", "STOP", "FORWARD", "RECENTER", "STOP"]
    gt = ["FORWARD", "STOP", "RIGHT", "RIGHT", "STOP", "STOP", "FORWARD", "FORWARD"]
    side = ["", "", "right", "right", "", "", "", ""]
    labels = [{"t_ms": i * 100, "action": a, "should_stop": int(a == "STOP"), "preferred_side": s}
              for i, (a, s) in enumerate(zip(gt, side))]
    m = compute_metrics(recs(cmds), labels)
    assert m["unsafe_forward_rate"] == pytest.approx(2 / 5)       # label wants STOP/side at frames 1-5; forward at 1, 5
    assert m["unsafe_forward_stop_rate"] == pytest.approx(2 / 3)  # label STOP at 1, 4, 5
    assert m["false_stop_rate"] == pytest.approx(1 / 5)           # STOP at 7 where the label is FORWARD
    assert m["wrong_side_rate"] == pytest.approx(1 / 2)           # LEFT at 3 vs preferred right
    assert m["side_reversals"] == 1
    p = tmp_path / "l.csv"
    p.write_text("t_ms,action,preferred_side\n0,FORWARD,\n100,LEFT,left\n200,stop,\n")
    ls = load_labels_csv(str(p))
    assert [l["action"] for l in ls] == ["FORWARD", "LEFT", "STOP"] and ls[2]["should_stop"] == 1
    assert ls[1]["preferred_side"] == "left"
    rep = format_report(m)
    for k in ("危险前进", "选错侧", "来回切换"):
        assert k in rep
    bad = tmp_path / "b.csv"
    bad.write_text("t_ms,action\n0,JUMP\n")
    with pytest.raises(ValueError):
        load_labels_csv(str(bad))


def test_viz_shapes_and_text():
    b = BEVConfig()
    states = np.zeros((b.nz, b.nx), np.uint8)
    states[5:10, 5:10] = 2
    plan = VFHPlanner(PlannerConfig(), b).plan(np.ones_like(states))
    img = render_bev(states, b.cell_m, b.lateral_half_m, plan, (1.5, 0.3), scale_px=4)
    assert img.shape == (b.nz * 4, b.nx * 4, 3)
    frame = np.zeros((120, 160, 3), np.uint8)
    ov = overlay_frame(frame, np.ones((120, 160), np.uint8), np.full((120, 160), 2.0, np.float32), ["继续 FORWARD"])
    assert ov.shape == frame.shape and ov.any()
    assert compose(ov, img).shape[0] == 120
    put_text(frame, "稍向左", (5, 5))             # must not raise with or without a CJK font
