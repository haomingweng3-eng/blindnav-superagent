import json

import cv2
import numpy as np

from blindnav_pc import cli
from blindnav_pc import depth as depth_mod
from eval.fixture import FX, H, W, make_fixture_session
from eval.sanpo import SanpoSession


def test_cli_sim_and_metrics(tmp_path, capsys):
    rc = cli.main(["sim", "right_free", "--memory", "full", "--log", str(tmp_path)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "碰撞=False" in out
    assert cli.main(["sim", "right_free", "--memory", "none", "--blind", "1.5"]) == 1   # documented limitation
    assert cli.main(["metrics", str(tmp_path / "sim_right_free.jsonl")]) == 0


def test_cli_run_with_orientation_and_steps(tmp_path, monkeypatch):
    """Full CLI path on the fixture: video + orientation.csv (quaternions) + steps.csv, test-double depth."""
    sess = SanpoSession(make_fixture_session(str(tmp_path)), max_width=0)
    vp = str(tmp_path / "v.avi")
    vw = cv2.VideoWriter(vp, cv2.VideoWriter_fourcc(*"MJPG"), 5.0, (W, H))
    rows = ["t_ms,qx,qy,qz,qw"]
    for i in sess.available_frames():
        vw.write(sess.inputs(i).bgr)
        q = sess.quat[i]
        rows.append(f"{i * 200},{q[0]},{q[1]},{q[2]},{q[3]}")
    vw.release()
    (tmp_path / "o.csv").write_text("\n".join(rows) + "\n")
    (tmp_path / "s.csv").write_text("t_ms\n560\n1120\n1680\n")
    v = (np.arange(H)[:, None] - H / 2.0).clip(0) * np.ones((1, W))
    monkeypatch.setattr(depth_mod, "DepthEstimator",
                        lambda input_size=None: depth_mod.SyntheticDepth(lambda f: (v / (1.5 * FX) + 0.01)))
    out = tmp_path / "out"
    rc = cli.main(["run", "--video", vp, "--orientation", str(tmp_path / "o.csv"), "--steps", str(tmp_path / "s.csv"),
                   "--seg", "const", "--height", "1.5", "--no-gui", "--out", str(out)])
    assert rc == 0
    recs = [json.loads(l) for l in (out / "log.jsonl").read_text().splitlines()]
    assert len(recs) >= 8 and all("command" in r for r in recs)
    assert (out / "metrics.json").exists()
