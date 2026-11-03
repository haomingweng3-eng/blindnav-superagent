import json

import numpy as np
import pytest

from blindnav_pc.depth import DepthEstimator, SyntheticDepth
from blindnav_pc.io import gravity_from_pitch_roll, load_camera, load_imu_csv
from blindnav_pc.segmentation import ConstantSegmenter, make_segmenter, match_label_ids


def test_camera_fov_fallback_and_json_scaling(tmp_path):
    K = load_camera(None, 640, 480, fov_deg=90.0)
    assert K[0, 0] == pytest.approx(320.0) and K[0, 2] == 320.0 and K[1, 2] == 240.0
    p = tmp_path / "camera.json"
    p.write_text(json.dumps({"fx": 1000, "fy": 1000, "cx": 960, "cy": 540, "width": 1920, "height": 1080}))
    K = load_camera(str(p), 960, 540)
    assert (K[0, 0], K[0, 2], K[1, 2]) == (500.0, 480.0, 270.0)
    p.write_text(json.dumps({"fx": 1}))
    with pytest.raises(ValueError):
        load_camera(str(p), 10, 10)


def test_gravity_from_pitch_roll():
    assert np.allclose(gravity_from_pitch_roll(0), [0, 1, 0])
    p = np.deg2rad(20)
    assert np.allclose(gravity_from_pitch_roll(20), [0, np.cos(p), np.sin(p)])
    g = gravity_from_pitch_roll(0, 90)
    assert np.allclose(g, [-1, 0, 0], atol=1e-9) and np.isclose(np.linalg.norm(gravity_from_pitch_roll(33, 12)), 1)


def test_imu_csv(tmp_path):
    p = tmp_path / "imu.csv"
    p.write_text("t_ms,gx,gy,gz,extra\n0,0,9.8,0,x\n1000,0,0,9.8,y\n")
    log = load_imu_csv(str(p))
    assert np.allclose(log.gravity_at(100), [0, 1, 0]) and np.allclose(log.gravity_at(900), [0, 0, 1])
    p.write_text("t_ms,gx,gy\n0,0,1\n")
    with pytest.raises(ValueError, match="gz"):
        load_imu_csv(str(p))


def test_constant_segmenter_and_factory():
    f = np.zeros((10, 8, 3), np.uint8)
    m = make_segmenter("const").predict(f)
    assert m.shape == (10, 8) and m.dtype == np.uint8 and m[-1, 0] == 1 and m[0, 0] == 0
    assert ConstantSegmenter(0.8).predict(f)[7, 0] == 0
    with pytest.raises(ValueError):
        make_segmenter("nope")


def test_label_matching_handles_synonyms():
    id2label = {0: "wall", 3: "floor, flooring", 6: "road, route", 11: "sidewalk, pavement", 52: "path", 9: "grass"}
    assert match_label_ids(id2label, ["floor", "road", "sidewalk", "path", "pavement"]) == [3, 6, 11, 52]
    assert match_label_ids(id2label, ["grass"]) == [9]


def test_depth_rejects_non_small_models():
    for bad in ("depth-anything/Depth-Anything-V2-Large-hf", "x/Base", "x/Giant"):
        with pytest.raises(ValueError, match="Small"):
            DepthEstimator(bad)


def test_synthetic_depth():
    d = SyntheticDepth(lambda f: np.full(f.shape[:2], 2.0))
    assert d.predict(np.zeros((3, 4, 3))).dtype == np.float32 and d(np.zeros((3, 4, 3))).shape == (3, 4)
