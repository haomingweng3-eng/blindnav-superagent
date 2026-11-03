import math

import numpy as np

from blindnav_pc.config import EgoConfig
from blindnav_pc.ego import (CAM_OPENGL, CAM_OPENCV, EgoTracker, orientation_to_gravity_heading, quat_to_matrix,
                             steps_from_accel, wrap_pi)


def quat_axis_angle(axis, ang):
    a = np.asarray(axis, float)
    a /= np.linalg.norm(a)
    return list(a * math.sin(ang / 2)) + [math.cos(ang / 2)]


def test_first_heading_defines_psi_ref_and_pdr_integrates():
    t = EgoTracker(EgoConfig(step_length_m=0.7))
    t.update(0, 1.0, 0)
    assert t.psi_ref == 1.0 and t.yaw_rel == 0.0
    d = t.update(500, 1.0, 2)                                  # two steps straight
    assert d.mode == "full" and d.steps == 2 and np.isclose(d.dz_m, 1.4) and abs(d.dx_m) < 1e-9
    assert np.isclose(t.z_hat, 1.4) and abs(t.x_hat) < 1e-9
    t.update(1000, 1.0 + math.radians(50), 3)                  # turn right 50 deg during one step
    assert np.isclose(t.x_hat, 0.7 * math.sin(math.radians(25))) and np.isclose(t.z_hat, 1.4 + 0.7 * math.cos(math.radians(25)))
    assert np.isclose(math.degrees(t.yaw_rel), 50.0)


def test_rot_only_when_no_steps_available_or_disabled():
    t = EgoTracker(EgoConfig(use_steps=False))
    t.update(0, 0.0, 0)
    d = t.update(500, 0.1, 5)
    assert d.mode == "rot_only" and d.dz_m == 0.0 and t.z_hat == 0.0 and np.isclose(d.dyaw_rad, 0.1)
    t2 = EgoTracker()
    t2.update(0, 0.0, None)
    d = t2.update(500, 0.1, None)
    assert d.mode == "rot_only" and d.dz_m == 0.0


def test_no_orientation_is_mode_none():
    t = EgoTracker()
    d = t.update(0, None, 3)
    assert d.mode == "none" and not t.has_orientation


def test_unreliable_on_time_gap_and_huge_rotation():
    t = EgoTracker(EgoConfig(max_dt_ms=1500, max_dyaw_deg=60))
    t.update(0, 0.0, 0)
    assert not t.update(5000, 0.0, 3).reliable
    assert not t.update(5100, math.radians(100), 3).reliable
    assert t.update(5200, math.radians(101), 3).reliable


def test_wrap_pi():
    assert np.isclose(wrap_pi(math.radians(350)), math.radians(-10))
    t = EgoTracker()
    t.update(0, math.radians(179), 0)
    d = t.update(100, math.radians(-179), 0)                  # crossing +-180 is a +2 deg turn
    assert np.isclose(math.degrees(d.dyaw_rad), 2.0)


def mat_to_quat(R):
    """Shepperd's method -> (x, y, z, w)."""
    tr = np.trace(R)
    if tr > 0:
        S = math.sqrt(tr + 1) * 2
        return [(R[2, 1] - R[1, 2]) / S, (R[0, 2] - R[2, 0]) / S, (R[1, 0] - R[0, 1]) / S, 0.25 * S]
    i = int(np.argmax(np.diag(R)))
    j, k = (i + 1) % 3, (i + 2) % 3
    S = math.sqrt(1 + R[i, i] - R[j, j] - R[k, k]) * 2
    q = [0.0] * 4
    q[i] = 0.25 * S
    q[j] = (R[j, i] + R[i, j]) / S
    q[k] = (R[k, i] + R[i, k]) / S
    q[3] = (R[k, j] - R[j, k]) / S
    return q


def rz(a):
    c, s = math.cos(a), math.sin(a)
    return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1.0]])


def test_quat_matrix_roundtrip():
    R = rz(0.7)
    assert np.allclose(quat_to_matrix(mat_to_quat(R)), R, atol=1e-9)


def test_opencv_level_camera_gravity_and_heading_changes_with_turn_right():
    # OpenCV camera looking along world +Y, level; world Z up:  cam x->+X, cam y(down)->-Z, cam z(fwd)->+Y
    R0 = np.array([[1, 0, 0], [0, 0, 1], [0, -1, 0]], float).T.T
    R0 = np.column_stack([[1, 0, 0], [0, 0, -1], [0, 1, 0]])         # columns: images of cam x, y, z
    g, h0 = orientation_to_gravity_heading(mat_to_quat(R0), cam_axes=CAM_OPENCV)
    assert np.allclose(g, [0, 1, 0], atol=1e-9)                       # gravity points to camera +y (down)
    # turn right (clockwise seen from above) by 30 deg = rotate world about Z by -30 deg
    R1 = rz(-math.radians(30)) @ R0
    g1, h1 = orientation_to_gravity_heading(mat_to_quat(R1), cam_axes=CAM_OPENCV)
    assert np.allclose(g1, [0, 1, 0], atol=1e-9)
    assert np.isclose(wrap_pi(h1 - h0), math.radians(30), atol=1e-6)  # heading increases when turning right


def test_opengl_camera_convention_gives_same_result():
    R0 = np.column_stack([[1, 0, 0], [0, 0, -1], [0, 1, 0]])          # OpenCV pose
    Rgl = R0 @ CAM_OPENGL                                              # same physical camera, OpenGL axes
    g_cv, h_cv = orientation_to_gravity_heading(mat_to_quat(R0), cam_axes=CAM_OPENCV)
    g_gl, h_gl = orientation_to_gravity_heading(mat_to_quat(Rgl), cam_axes=CAM_OPENGL)
    assert np.allclose(g_cv, g_gl, atol=1e-9) and np.isclose(wrap_pi(h_cv - h_gl), 0.0, atol=1e-9)


def test_pitched_camera_gravity_tilts():
    pitch = math.radians(20)                                           # look 20 deg down
    Rp = np.array([[1, 0, 0], [0, math.cos(pitch), -math.sin(pitch)], [0, math.sin(pitch), math.cos(pitch)]])
    R0 = np.column_stack([[1, 0, 0], [0, 0, -1], [0, 1, 0]])
    g, _ = orientation_to_gravity_heading(mat_to_quat(R0 @ Rp))
    assert np.isclose(np.degrees(np.arctan2(g[2], g[1])), 20.0, atol=1e-6) or \
        np.isclose(abs(np.degrees(np.arctan2(g[2], g[1]))), 20.0, atol=1e-6)


def test_step_detector_counts_peaks():
    t = np.arange(0, 10000, 20.0)
    acc = np.zeros((len(t), 3))
    acc[:, 2] = 9.81 + 2.5 * np.maximum(0, np.sin(2 * np.pi * t / 1000.0 * 1.8))   # 1.8 steps / s
    st = steps_from_accel(t, acc)
    assert 15 <= len(st) <= 19
