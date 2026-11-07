import numpy as np
import math
import pytest

from blindnav_pc.mobilio_surface import (
    ROLE_HAZARD,
    ROLE_NON_WALKABLE,
    ROLE_WALKABLE,
    SurfaceGrid2D,
    SurfaceGridConfig,
    SurfacePose,
)


def test_surface_route_keeps_ray_termination_diagnostics():
    grid = SurfaceGrid2D(SurfaceGridConfig(min_candidate_m=0.5, blind_zone_m=0.0))
    grid.update_points(np.array([[0.1, 0.3], [0.1, 0.5], [0.1, 0.7]]),
                       np.array([ROLE_WALKABLE] * 3), frame_id=1)
    grid.update_points(np.array([[0.1, 0.9]]), np.array([ROLE_NON_WALKABLE]), frame_id=1)

    route = grid._ray_candidate(SurfacePose(0.1, 0.1, 0.0), 0.0)

    assert route.termination_distance_m > route.last_walkable_distance_m
    assert route.termination_cell is not None
    assert route.termination_cell != route.last_walkable_cell


def test_surface_no_candidate_reports_empty_diagnostics():
    grid = SurfaceGrid2D(SurfaceGridConfig(min_candidate_m=5.0))
    route = grid.raycast_route(SurfacePose(0.0, 0.0, 0.0), 0.0)

    assert route.termination_distance_m == 0.0
    assert route.last_walkable_distance_m == 0.0
    assert route.termination_cell is None
    assert route.last_walkable_cell is None


def test_short_walkable_ray_has_separate_failure_diagnostics():
    grid = SurfaceGrid2D(SurfaceGridConfig(min_candidate_m=5.0, blind_zone_m=0.0,
                                           scan_half_angle_deg=1.0, scan_step_deg=1.0))
    grid.update_points(np.array([[0.1, 0.3], [0.1, 0.5], [0.1, 0.7], [0.1, 0.9]]),
                       np.array([ROLE_WALKABLE, ROLE_WALKABLE, ROLE_WALKABLE, ROLE_NON_WALKABLE]), 1)
    pose = SurfacePose(0.1, 0.1, 0.0)
    route = grid.raycast_route(pose, 0.0)
    diagnostic = next(r for r in grid.trace_rays(pose) if r["best_rejected_diagnostic"])
    assert route.state == "UNKNOWN" and route.angle_rad is None
    assert diagnostic is not None
    assert diagnostic["observed_distance_m"] > 0
    assert diagnostic["termination_cell"] is not None
    assert diagnostic["last_walkable_cell"] is not None


def test_trace_preserves_decision_and_reports_rejected_rays():
    grid = SurfaceGrid2D(SurfaceGridConfig(min_candidate_m=5.0, blind_zone_m=0.0,
                                           scan_half_angle_deg=1.0, scan_step_deg=1.0))
    grid.update_points(np.array([[0.1, 0.3], [0.1, 0.5], [0.1, 0.7], [0.1, 0.9]]),
                       np.array([ROLE_WALKABLE, ROLE_WALKABLE, ROLE_WALKABLE, ROLE_NON_WALKABLE]), 1)
    pose = SurfacePose(0.1, 0.1, 0.0)
    before = grid.raycast_route(pose)
    trace = grid.trace_rays(pose)
    assert grid.raycast_route(pose) == before
    assert before.state == "UNKNOWN"
    assert len(trace) == 3
    assert sum(r["best_rejected_diagnostic"] for r in trace) == 1
    assert any(r["termination_reason"] == "NON_WALKABLE" for r in trace)
    assert not any(r["selected_by_original_algorithm"] for r in trace)


def test_trace_empty_map_records_unknown_gap_without_observation():
    grid = SurfaceGrid2D(SurfaceGridConfig(blind_zone_m=0.0, scan_half_angle_deg=1.0,
                                           scan_step_deg=1.0))
    trace = grid.trace_rays(SurfacePose(0.1, 0.1, 0.0))
    assert all(r["termination_reason"] == "UNKNOWN_GAP" for r in trace)
    assert all(r["walkable_cells"] == 0 and r["pending_unknown_run"] > 0 for r in trace)


def test_diagonal_trace_uses_ray_cell_exit_distance():
    grid = SurfaceGrid2D(SurfaceGridConfig(cell_m=1.0, max_range_m=3.0,
        blind_zone_m=0.0, scan_half_angle_deg=1.0, scan_step_deg=1.0))
    grid.update_points(np.array([[0.5, 0.5]]), np.array([ROLE_WALKABLE]), 1)
    trace = grid.trace_rays(SurfacePose(0.5, 0.5, 0.0), math.pi / 4)
    center = trace[1]
    assert center["continuous_walkable_length_m"] == pytest.approx(math.sqrt(0.5))
    assert center["termination_reason"] == "UNKNOWN_GAP"


def test_corner_touch_is_recorded_separately_from_positive_length_unknown():
    grid = SurfaceGrid2D(SurfaceGridConfig(cell_m=1.0, max_range_m=3.0,
        blind_zone_m=0.0, max_gap_cells=0, scan_half_angle_deg=1.0, scan_step_deg=1.0))
    grid.update_points(np.array([[0.5, 0.5], [1.5, 1.5]]), np.array([ROLE_WALKABLE] * 2), 1,
                       center=(0.5, 0.5))
    pose = SurfacePose(0.5, 0.5, 0.0)
    before = grid.raycast_route(pose, math.pi / 4)
    center = grid.trace_rays(pose, math.pi / 4)[1]
    assert grid.raycast_route(pose, math.pi / 4) == before
    assert center["termination_reason"] == "UNKNOWN_GAP"
    assert center["termination_cell_zero_length"] is True
    assert center["zero_length_cells"] >= 1
    assert center["positive_length_unknown_cells"] == 0


def test_tolerated_corner_touch_does_not_split_physical_walkable_segment():
    grid = SurfaceGrid2D(SurfaceGridConfig(cell_m=1.0, max_range_m=2.0,
        blind_zone_m=0.0, max_gap_cells=2, scan_half_angle_deg=1.0, scan_step_deg=1.0))
    grid.update_points(np.array([[0.5, 0.5], [1.5, 1.5]]), np.array([ROLE_WALKABLE] * 2), 1,
                       center=(0.5, 0.5))
    center = grid.trace_rays(SurfacePose(0.5, 0.5, 0.0), math.pi / 4)[1]
    assert center["zero_length_cells"] >= 1
    assert center["continuous_walkable_length_m"] == pytest.approx(2.0)


def test_trace_distinguishes_hazard_from_unknown_gap():
    grid = SurfaceGrid2D(SurfaceGridConfig(cell_m=1.0, max_range_m=3.0,
        blind_zone_m=0.0, max_gap_cells=1, scan_half_angle_deg=1.0, scan_step_deg=1.0))
    grid.update_points(np.array([[0.5, 0.5], [0.5, 2.5]]),
                       np.array([ROLE_WALKABLE, ROLE_HAZARD]), 1)
    center = grid.trace_rays(SurfacePose(0.5, 0.5, 0.0))[1]
    assert center["termination_reason"] == "HAZARD"
    assert center["pending_unknown_run"] == 1
    assert center["terminal_role"] == ROLE_HAZARD


def test_trace_max_range_is_not_a_terminal_obstacle():
    grid = SurfaceGrid2D(SurfaceGridConfig(cell_m=1.0, max_range_m=2.0,
        blind_zone_m=0.0, scan_half_angle_deg=1.0, scan_step_deg=1.0))
    grid.update_points(np.array([[0.5, 0.5], [0.5, 1.5], [0.5, 2.5]]),
                       np.array([ROLE_WALKABLE] * 3), 1)
    center = grid.trace_rays(SurfacePose(0.5, 0.5, 0.0))[1]
    assert center["termination_reason"] == "MAX_RANGE"
    assert center["termination_distance_m"] == 2.0
    assert center["terminal_role"] is None
