"""The product-path demo runs offline and stays inside the locked safety rules."""
import json
import os
import subprocess
import sys

from blindnav_pc.product_demo import run_demo, script_frames
from blindnav_pc.shadowwalk import LEFT, STOP, STRAIGHT, UNKNOWN
from blindnav_pc.traffic_light import GREEN, RED, STATES, UNCERTAIN

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def test_script_uses_the_real_stubs_and_the_locked_rules():
    rows = script_frames()
    corridor = [row for row in rows if row["phase"] == "corridor"]
    doors = [row for row in rows if row["phase"] == "doors"]
    lights = [row for row in rows if row["phase"] == "traffic"]

    assert [row["hint"] for row in corridor] == [
        STRAIGHT, STRAIGHT, LEFT, LEFT, STRAIGHT, UNKNOWN, STOP,
    ]
    assert [row["reason"] for row in corridor] == [
        "centred", "centred", "lateral", "lateral", "centred", "walkable_unseen", "off_corridor",
    ]
    assert corridor[2]["text"] == "稍向左"
    assert corridor[5]["text"].startswith("前方走廊无法确认")
    assert corridor[6]["text"] == "请停下"

    assert doors[0]["refused"] is False
    assert doors[0]["candidate_count"] == 3
    assert doors[0]["distances_m"] == [2.0, 3.0, 6.0]
    assert doors[1]["refused"] is True
    assert doors[1]["reason"] == "distance_unknown"
    assert doors[1]["candidate_count"] == 0

    assert [row["state"] for row in lights] == [
        UNCERTAIN, UNCERTAIN, RED, UNCERTAIN, UNCERTAIN, UNCERTAIN, GREEN,
    ]
    assert lights[-1]["stable"] is True
    assert all(row["auto_go"] is False for row in rows)
    assert all(row["state"] in STATES for row in lights)
    blob = json.dumps(rows, ensure_ascii=False)
    assert "可以过街" not in blob
    assert "CROSS" not in blob.upper()
    assert "由您决定" in lights[-1]["text"]


def test_run_demo_writes_video_summary_and_frames(tmp_path):
    out = tmp_path / "demo"
    report = run_demo(str(out), fps=4, hold=2)
    assert report["auto_go"] is False
    assert report["w1_curb_recall_gate"] == "not_claimed"
    assert report["w1_near_field_recall_gate"] == "not_claimed"
    assert report["pure_monocular"] == "supervised_low_speed_constrained_demo"
    assert report["n_frames"] == 16
    assert (out / "demo.json").is_file()
    assert (out / "demo.avi").stat().st_size > 1000
    summary = (out / "summary.md").read_text(encoding="utf-8")
    assert "受控演示" in summary
    assert "拒答" in summary
    assert "尚未" in summary
    assert "可以过街" not in summary
    frames = list((out / "frames").glob("*.jpg"))
    assert len(frames) == 16
    written = json.loads((out / "demo.json").read_text(encoding="utf-8"))
    assert written["frames"][6]["hint"] == STOP
    assert written["frames"][7]["candidate_count"] == 3
    assert written["frames"][8]["reason"] == "distance_unknown"


def test_cli_demo_command(tmp_path):
    out = tmp_path / "cli"
    proc = subprocess.run(
        [sys.executable, "-m", "blindnav_pc", "demo", "--out", str(out), "--hold", "1"],
        cwd=ROOT, check=False, capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert "受控演示完成" in proc.stdout
    assert (out / "summary.md").is_file()
    assert (out / "demo.avi").stat().st_size > 1000
