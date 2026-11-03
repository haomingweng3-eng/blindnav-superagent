import json
import math

import numpy as np
import pytest

from blindnav_pc.config import FREE, OCCUPIED, UNKNOWN, Config
from blindnav_pc.io import OrientationLog, StepLog, load_orientation_csv, load_steps_csv
from eval import run as erun
from eval.bev_eval import action_comparison, bev_compare, corridor_width_m, nearest_obstacle_m
from eval.depth_metrics import confidence_vs_error, depth_errors, depth_report, spearman
from eval.fixture import make_fixture_session
from eval.sanpo import SanpoSession
from eval.ub_visiogeoloc import UBVisioGeolocSequence


@pytest.fixture(scope="module")
def session(tmp_path_factory):
    return SanpoSession(make_fixture_session(str(tmp_path_factory.mktemp("fx"))), max_width=0)


def test_depth_errors_scale_and_masks():
    gt = np.full((20, 20), 2.0, np.float32)
    gt[0, :] = 0                               # invalid GT is ignored
    e = depth_errors(gt * 1.5, gt)
    assert e["absrel"] == pytest.approx(0.5) and e["rmse"] == pytest.approx(1.0)
    assert e["delta1"] == 0.0 and e["scale_ratio"] == pytest.approx(1.5)
    far = gt.copy()
    far[5, 5] = 30.0
    r = depth_report(far, far)
    assert r["near"]["n"] == r["all"]["n"] - 1 and r["all"]["absrel"] == 0.0


def test_spearman_and_confidence():
    assert spearman([1, 2, 3, 4], [4, 3, 2, 1]) == pytest.approx(-1.0)
    assert confidence_vs_error([1, 2], [1, 2])["spearman"] is None
    assert confidence_vs_error([0.9, 0.5, 0.2, 0.1], [0.1, 0.2, 0.3, 0.9])["spearman"] < 0


def test_bev_compare_known_cases():
    cfg = Config()
    b = cfg.bev
    o = np.full((b.nz, b.nx), FREE, np.uint8)
    o[20, b.nx // 2] = OCCUPIED                              # obstacle at 2.05 m in the corridor
    assert nearest_obstacle_m(o, b, 0.4) == pytest.approx(2.05)
    assert corridor_width_m(np.full((b.nz, b.nx), FREE, np.uint8), b) == pytest.approx(3.0)
    pred_missing = o.copy()
    pred_missing[20, b.nx // 2] = FREE                       # dangerous: Pred calls it free
    r = bev_compare(o, pred_missing, cfg, max_z_m=4.0)
    assert r["false_free_rate"] == 1.0 and r["nearest_miss"] is True
    pred_off = o.copy()
    pred_off[20, b.nx // 2] = FREE
    pred_off[16, b.nx // 2] = OCCUPIED                       # obstacle seen 0.4 m too near
    r = bev_compare(o, pred_off, cfg, max_z_m=4.0)
    assert r["nearest_err_m"] == pytest.approx(-0.4) and r["false_occ_rate"] > 0
    unk = np.full_like(o, UNKNOWN)
    r = bev_compare(o, unk, cfg, max_z_m=4.0)
    assert r["miss_rate"] == 1.0 and r["false_free_rate"] == 0.0


def test_action_comparison():
    a = action_comparison(["FORWARD", "LEFT", "STOP", "RIGHT", "FORWARD"], ["FORWARD", "FORWARD", "STOP", "LEFT", "STOP"])
    assert a["agreement"] == pytest.approx(0.4)
    assert a["unsafe_forward_rate"] == pytest.approx(1 / 3)     # Oracle needs action on LEFT/STOP/RIGHT, Pred FORWARD once
    assert a["false_stop_rate"] == pytest.approx(1 / 4)         # Oracle moves on 4 frames, Pred STOPs on one
    assert a["wrong_side_rate"] == 1.0                          # RIGHT vs LEFT


def test_fixture_session_conventions(session):
    assert session.available_frames() == list(range(10)) and session.has_gt
    inp, gt = session.inputs(2), session.gt(2)
    assert inp.gravity_cam == pytest.approx([0, 1, 0], abs=1e-6)       # camera level: gravity = +y (down)
    assert inp.bgr.shape[:2] == gt.depth_m.shape and gt.seg.shape == gt.depth_m.shape
    chk = session.gravity_check()
    assert chk["up_angle_max_deg"] < 0.1 and chk["gt_camera_height_m"] == pytest.approx(1.5, abs=0.02)
    assert session.gt_gravity(2) == pytest.approx([0, 1, 0], abs=1e-2)
    st = session.step_times_ms(range(10), 0.7)
    assert len(st) == 3 and np.all(np.diff(st) > 0)                    # 2.25 m walked / 0.7 m


def test_ego_pdr_gyro_step_beats_gyro_only(session):
    from eval.ego_eval import pdr_test
    g = pdr_test(session, range(10), "gyro_only", 0.7)
    s = pdr_test(session, range(10), "gyro_step", 0.7)
    assert s["final_pos_err_m"] < 0.3 < g["final_pos_err_m"]


def test_memory_prediction_modes(session):
    from eval.ego_eval import memory_prediction_test
    cfg = Config()
    cfg.bev.forward_m = 8.0
    r = {m: memory_prediction_test(session, range(10), m, cfg, 0.7) for m in ("none", "rot_only", "full")}
    assert r["none"]["occ_recall"] == 0.0                                  # no history -> predicts nothing
    assert r["full"]["occ_recall"] >= r["rot_only"]["occ_recall"] >= 0.0
    assert all(v["ghost_rate"] <= 0.1 for v in r.values())


def test_run_eval_end_to_end_on_fixture(tmp_path, capsys):
    rc = erun.main(["--fixture", "--out", str(tmp_path)])
    assert rc == 0
    out = json.loads((tmp_path / "eval.json").read_text(encoding="utf-8"))
    s = out["summary"]
    assert s["frames"] == 10 and s["frames_ground_ok"] == 10
    assert s["depth_all"]["absrel"] < 1e-3                                  # oracle depth in -> ~0 error out
    assert s["actions"]["agreement"] >= 0.8                                # oracle seg+depth in -> actions agree
    assert out["gravity_check"]["up_angle_max_deg"] < 0.1
    assert {r["mode"] for r in out["ego"]["pdr"]} == {"gyro_only", "gyro_step"}
    assert "评测" in capsys.readouterr().out


def test_parse_frames():
    assert erun.parse_frames("2:8:2", list(range(10))) == [2, 4, 6]
    assert erun.parse_frames("1,3,99", list(range(10))) == [1, 3]


def test_ub_visiogeoloc_is_a_stub():
    with pytest.raises(NotImplementedError):
        UBVisioGeolocSequence("nowhere")


def test_orientation_and_steps_loaders(tmp_path):
    p = tmp_path / "o.csv"
    p.write_text("t_ms,heading_deg\n0,0\n100,90\n200,-90\n")
    o = load_orientation_csv(str(p))
    assert o.heading_at(110) == pytest.approx(math.pi / 2) and o.gravity_at(0) is None
    q = tmp_path / "q.csv"
    q.write_text("t_ms,qx,qy,qz,qw\n0,0,0,0,1\n")
    o2 = load_orientation_csv(str(q))
    assert o2.gravity_at(0) is not None and np.linalg.norm(o2.gravity_at(0)) == pytest.approx(1.0)
    s = tmp_path / "s.csv"
    s.write_text("t_ms\n500\n1100\n1700\n")
    st = load_steps_csv(str(s))
    assert [st.count_at(t) for t in (0, 500, 1099, 1100, 5000)] == [0, 1, 1, 2, 3]
    bad = tmp_path / "b.csv"
    bad.write_text("x\n1\n")
    with pytest.raises(ValueError):
        load_steps_csv(str(bad))
