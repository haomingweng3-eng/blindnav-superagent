"""Ground-plane calibration: synthetic plane with known h/K/pitch and synthetic affine inverse depth."""
import numpy as np
import pytest

from blindnav_pc.config import GroundConfig
from blindnav_pc.ground import (GroundCalibrator, choose_up, fit_affine_ransac, fit_ground,
                                flat_ground_depth, metric_depth, pixel_rays, up_from_gravity,
                                visual_plane_up)
from eval.depth_metrics import confidence_vs_error, spearman

W, H_IMG = 320, 240
K = np.array([[250.0, 0, 160.0], [0, 250.0, 120.0], [0, 0, 1.0]])
CAM_H = 1.2
A_TRUE, B_TRUE = 0.8, 0.05


def gravity(pitch_deg):
    p = np.deg2rad(pitch_deg)
    return np.array([0.0, np.cos(p), np.sin(p)])


def synth_scene(pitch_deg=15.0, noise=0.01, outlier_frac=0.10, seed=0, wall_z=None):
    """Return q (affine inverse depth), true z-depth map, ground mask, up vector."""
    rng = np.random.default_rng(seed)
    up = up_from_gravity(gravity(pitch_deg))
    rays = pixel_rays(K, W, H_IMG)
    z = flat_ground_depth(rays, up, CAM_H, eps=0.02)
    if wall_z is not None:                       # fronto-parallel wall in the middle: z-depth = wall_z
        wall = np.zeros((H_IMG, W), bool)
        wall[100:200, 120:200] = True
        z = np.where(wall & ((z > wall_z) | ~np.isfinite(z)), wall_z, z)
    mask = (np.isfinite(flat_ground_depth(rays, up, CAM_H, eps=0.02))).astype(np.uint8)
    if wall_z is not None:
        mask[wall] = 0
    w = 1.0 / np.where(np.isfinite(z), z, 1e6)   # sky -> w ~ 0
    q = (w - B_TRUE) / A_TRUE
    q = q * (1 + noise * rng.standard_normal(q.shape))
    out = rng.random(q.shape) < outlier_frac     # segmentation / depth outliers
    q = np.where(out, rng.uniform(q.min(), q.max(), q.shape), q)
    return q.astype(np.float32), z, mask, up


def test_up_from_gravity():
    assert np.allclose(up_from_gravity([0, 9.81, 0]), [0, -1, 0])
    assert np.isclose(np.linalg.norm(up_from_gravity([1, 2, 3])), 1.0)
    with pytest.raises(ValueError):
        up_from_gravity([0, 0, 0])


def test_flat_ground_depth_known_geometry():
    up = up_from_gravity(gravity(0.0))           # level camera
    r = np.array([[0.0, 0.5, 1.0]])              # ray 0.5 below the optical axis (y down)
    assert np.isclose(flat_ground_depth(r, up, 1.2)[0], 1.2 / 0.5)
    r_up = np.array([[0.0, -0.1, 1.0]])          # above the horizon -> no ground
    assert np.isnan(flat_ground_depth(r_up, up, 1.2)[0])


@pytest.mark.parametrize("pitch", [10.0, 15.0, 25.0])
def test_recover_a_b_and_metric_depth_within_5pct(pitch):
    q, z_true, mask, up = synth_scene(pitch_deg=pitch)
    fit = fit_ground(q, mask, K, up, GroundConfig(camera_height_m=CAM_H))
    assert fit.ok, fit.reason
    assert fit.a == pytest.approx(A_TRUE, rel=0.05)
    assert fit.b == pytest.approx(B_TRUE, abs=0.02)
    assert fit.inlier_ratio > 0.7 and fit.confidence > 0.5
    d = metric_depth(q, fit.a, fit.b, max_depth=50)
    sel = np.isfinite(z_true) & np.isfinite(d) & (z_true < 8.0) & (mask > 0)
    # evaluate on non-outlier pixels via the median relative error (outliers are random garbage by design)
    err = np.abs(d[sel] - z_true[sel]) / z_true[sel]
    assert np.median(err) < 0.05
    assert np.mean(err < 0.05) > 0.8


def test_obstacle_depth_recovered_from_ground_calibration():
    q, z_true, mask, up = synth_scene(wall_z=2.5, noise=0.005, outlier_frac=0.05)
    fit = fit_ground(q, mask, K, up, GroundConfig(camera_height_m=CAM_H))
    assert fit.ok
    d = metric_depth(q, fit.a, fit.b, max_depth=50)
    wall_px = d[120:180, 130:190]
    assert np.nanmedian(wall_px) == pytest.approx(2.5, rel=0.05)


def test_metric_depth_guards_nonpositive_denominator():
    q = np.array([[-5.0, 0.0, 1.0]], dtype=np.float32)
    d = metric_depth(q, a=0.8, b=0.05, max_depth=15)
    assert np.isnan(d[0, 0]) and np.isnan(d[0, 1])       # a*q+b <= 0 or D > max_depth -> NaN, never negative
    assert d[0, 2] == pytest.approx(1 / 0.85, rel=1e-4)


def test_too_few_pixels_or_flat_q_degrades():
    cfg = GroundConfig()
    f = fit_affine_ransac(np.ones(10), np.ones(10), cfg)
    assert not f.ok and f.degraded
    f = fit_affine_ransac(np.full(500, 0.5), np.linspace(0.1, 0.5, 500), cfg)
    assert not f.ok and f.reason == "q_spread_too_small"


def test_calibrator_reuses_then_degrades():
    cfg = GroundConfig(camera_height_m=CAM_H, reuse_frames=3)
    cal = GroundCalibrator(cfg)
    q, _, mask, up = synth_scene()
    f0 = cal.update(q, mask, K, up)
    assert f0.ok and not f0.reused
    empty = np.zeros_like(mask)
    for _ in range(3):                                   # fit fails, previous (a, b) reused briefly
        f = cal.update(q, empty, K, up)
        assert f.ok and f.reused and f.a == pytest.approx(f0.a)
    f = cal.update(q, empty, K, up)                      # then degrade -> caller must OBSERVE/STOP
    assert (not f.ok) and f.degraded
    f = cal.update(q, mask, K, up)                       # recovers when ground is visible again
    assert f.ok and not f.reused


def _absrel(fit, q, z_true, mask):
    d = metric_depth(q, fit.a, fit.b, max_depth=50)
    sel = np.isfinite(z_true) & np.isfinite(d) & (z_true < 8.0) & (mask > 0) & (d > 0)
    return float(np.mean(np.abs(d[sel] - z_true[sel]) / z_true[sel]))


def test_choose_up_prefers_type_gravity_and_falls_back_for_attitude():
    cfg = GroundConfig()
    gravity_up = up_from_gravity(gravity(0.0))
    visual_up = up_from_gravity(gravity(15.0))
    up, src = choose_up(gravity_up, (visual_up, 15.0), "gravity", cfg)
    assert src == "gravity" and np.allclose(up, gravity_up)          # TYPE_GRAVITY wins
    up, src = choose_up(gravity_up, (visual_up, 15.0), "attitude", cfg)
    assert src == "visual" and np.allclose(up, visual_up)            # large disagreement -> visual
    up, src = choose_up(gravity_up, (visual_up, 3.0), "attitude", cfg)
    assert src == "fused" and np.isclose(np.linalg.norm(up), 1.0)
    up, src = choose_up(gravity_up, (visual_up, 7.0), "attitude", cfg)
    assert src == "attitude" and np.allclose(up, gravity_up)         # middle ground: keep attitude


def test_visual_plane_is_robust_to_depth_outliers():
    cfg = GroundConfig(camera_height_m=CAM_H)
    q, _, mask, up = synth_scene(noise=0.005, outlier_frac=0.15)
    f = fit_ground(q, mask, K, up, cfg)
    assert f.ok
    vp = visual_plane_up(q, mask, K, up, cfg, f.a, f.b)
    assert vp is not None and vp[1] < 3.0     # a plain SVD plane is tilted ~15 deg by the same outliers


def test_calibrator_keeps_gravity_when_visual_disagrees_only_from_noise():
    cfg = GroundConfig(camera_height_m=CAM_H)
    q, _, mask, up = synth_scene(noise=0.005, outlier_frac=0.15)
    f = GroundCalibrator(cfg).update(q, mask, K, up, up_source="gravity")
    assert f.ok and f.up_source == "gravity" and not f.visual_fallback
    assert f.a == pytest.approx(A_TRUE, rel=0.03)


def test_calibrator_falls_back_to_visual_plane_for_wrong_attitude():
    cfg = GroundConfig(camera_height_m=CAM_H)
    q, _, mask, up = synth_scene(noise=0.005, outlier_frac=0.05)
    cal = GroundCalibrator(cfg)
    good = cal.update(q, mask, K, up, up_source="attitude")
    assert good.ok and good.up_source in ("fused", "attitude")
    wrong_up = up_from_gravity(gravity(0.0))                   # true pitch is 15 deg
    f = cal.update(q, mask, K, wrong_up, up_source="attitude")
    assert f.ok and f.visual_fallback and f.up_source == "visual"
    assert f.input_up_source == "attitude" and f.attitude_visual_deg > cfg.prefer_visual_up_deg
    assert f.a == pytest.approx(A_TRUE, rel=0.05) and f.b == pytest.approx(B_TRUE, abs=0.05)


def test_plane_ransac_survives_empty_inlier_refit():
    from blindnav_pc.ground import _plane_ransac
    P = np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 10.0]])
    n, cnt = _plane_ransac(P, np.zeros(len(P)), iters=100, min_inliers=3, seed=0)
    assert cnt >= 3 and np.isfinite(n).all()


def test_confidence_negatively_correlated_with_absrel_on_noise_sweep():
    cfg = GroundConfig(camera_height_m=CAM_H)
    confs, errs = [], []
    for noise in (0.001, 0.002, 0.004, 0.008, 0.016, 0.032, 0.064):
        q, zt, mask, up = synth_scene(noise=noise, outlier_frac=0.0)
        f = fit_ground(q, mask, K, up, cfg)
        assert f.ok, f.reason
        confs.append(f.confidence)
        errs.append(_absrel(f, q, zt, mask))
    r = confidence_vs_error(confs, errs)
    assert r["n"] == len(confs) and r["spearman"] <= -0.9


def test_confidence_negatively_correlated_with_absrel_across_degradations():
    cfg = GroundConfig(camera_height_m=CAM_H)
    confs, errs = [], []
    for noise, out in ((0.002, 0.0), (0.005, 0.05), (0.01, 0.1), (0.02, 0.15), (0.04, 0.2), (0.06, 0.3)):
        q, zt, mask, up = synth_scene(noise=noise, outlier_frac=out)
        f = fit_ground(q, mask, K, up, cfg)
        if not f.ok:
            continue
        confs.append(f.confidence)
        errs.append(_absrel(f, q, zt, mask))
    assert len(confs) >= 5
    assert spearman(confs, errs) < -0.5
