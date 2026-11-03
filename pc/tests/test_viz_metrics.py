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
