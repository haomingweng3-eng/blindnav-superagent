"""Real-grid contract tests; no model accuracy or real walking claim."""
import copy
import math

import numpy as np
import pytest

from blindnav_pc.mobilio_fusion import fuse
from blindnav_pc.mobilio_joint_route import (
    JointRouteConfig, query_fixed_route, query_joint_route,
)
from blindnav_pc.mobilio_occupancy import OccupancyGrid3D, OccupancyGridConfig
from blindnav_pc.mobilio_surface import (
    ROLE_NON_WALKABLE, ROLE_WALKABLE, SurfaceGrid2D, SurfaceGridConfig, SurfacePose,
)


POSE = SurfacePose(0.0, 0.0, 0.0)


def _maps(*, ground=None, visible=None, hits=(), blind=0.0, pose=POSE):
    surface = SurfaceGrid2D(SurfaceGridConfig(
        cell_m=0.1, max_range_m=3.0, min_candidate_m=1.0,
        blind_zone_m=blind, scan_half_angle_deg=30.0, scan_step_deg=30.0))
    occupancy = OccupancyGrid3D(OccupancyGridConfig(
        cell_m=0.1, max_range_m=3.0, padded_range_m=1.0,
        immediate_range_m=0.3, person_radius_m=0.15,
        blind_zone_m=blind, scan_half_angle_deg=30.0, scan_step_deg=30.0))
    points, roles, free = [], [], []
    # Paint a dense measured patch, not the tested module's capsule rasterizer.
    for ix in range(-31, 31):
        for iz in range(-31, 31):
            x, z = (ix + 0.5) * 0.1, (iz + 0.5) * 0.1
            if math.hypot(x, z) > 2.95:
                continue
            role = ROLE_WALKABLE if ground is None else ground(x, z)
            if role is not None:
                points.append((x, z))
                roles.append(role)
            if visible is None or visible(x, z):
                free.append(occupancy.world_to_cell(x, z, 0.0))
    for frame in (1, 2):
        surface.update_points(np.asarray(points).reshape(-1, 2), np.asarray(roles), frame)
        occupancy.update_body_points(np.asarray(hits).reshape(-1, 3), pose, frame, free)
    return surface, occupancy


def _beam(angle_deg, half_width=0.4):
    angle = math.radians(angle_deg)
    def inside(x, z):
        forward = x * math.sin(angle) + z * math.cos(angle)
        lateral = x * math.cos(angle) - z * math.sin(angle)
        return forward >= -0.5 and abs(lateral) < half_width
    return inside


def test_common_observed_body_band_passes_and_limits_the_claim():
    surface, occupancy = _maps()
    result = query_joint_route(surface, occupancy, POSE, frame_id=2)
    assert result.movement_authorized
    assert result.decision.guidance == "STRAIGHT"
    cert = result.certificate
    assert cert["surface_angle_rad"] == cert["occupancy_angle_rad"] == result.decision.angle_rad
    assert cert["shared_checked_interval_m"] == [0.0, 1.0]
    assert cert["swept_half_width_m"] == pytest.approx(0.25)
    assert cert["surface_check_passed"] and cert["occupancy_check_passed"]
    assert cert["unobserved_human_height_voxels"] > 0
    assert not cert["full_volume_visibility_certified"]
    assert not cert["physical_safety_certified"]
    assert not cert["deployment_authorized"]


def test_disjoint_ground_and_free_directions_no_longer_combine():
    left, right = _beam(-30), _beam(30)
    surface, occupancy = _maps(
        ground=lambda x, z: ROLE_WALKABLE if left(x, z) else None,
        visible=right)
    old_surface = surface.raycast_route(POSE)
    old_occupancy = occupancy.raycast_clearance(POSE)
    assert old_surface.state == old_occupancy.state == "CLEAR"
    assert old_surface.angle_rad != old_occupancy.angle_rad
    assert fuse(old_surface, old_occupancy).state == "CLEAR"
    result = query_joint_route(surface, occupancy, POSE, frame_id=2)
    assert not result.movement_authorized
    assert result.decision.guidance is None
    assert result.certificate["jointly_clear_candidate_count"] == 0


def test_search_finds_common_side_path_when_center_is_blocked():
    surface, occupancy = _maps(
        ground=lambda x, z: ROLE_NON_WALKABLE if x < -0.3 and z > 0.7 else ROLE_WALKABLE,
        hits=[(0.01, 1.51, 0.8)])
    center = query_fixed_route(surface, occupancy, POSE, 0.0, frame_id=2)
    assert not center.movement_authorized
    result = query_joint_route(surface, occupancy, POSE, frame_id=2)
    assert result.movement_authorized
    assert result.decision.guidance == "RIGHT"
    assert result.decision.angle_rad == pytest.approx(math.radians(30))


def test_no_direction_when_all_original_candidates_blocked():
    hits = []
    for angle in (-30, 0, 30):
        x, z = 1.5 * math.sin(math.radians(angle)), 1.5 * math.cos(math.radians(angle))
        hits.extend((x + dx, z + dz, 0.8) for dx in (-0.03, 0.03) for dz in (-0.03, 0.03))
    surface, occupancy = _maps(hits=hits)
    result = query_joint_route(surface, occupancy, POSE, frame_id=2)
    assert not result.movement_authorized
    assert result.decision.state == "BLOCKED"
    assert result.decision.guidance is None


def test_center_ray_cannot_authorize_a_gap_narrower_than_body():
    surface, occupancy = _maps(hits=[(0.21, 0.81, 1.6), (-0.21, 0.81, 1.6)])
    assert occupancy._ray_candidate(POSE, 0.0).state == "CLEAR"
    result = query_fixed_route(surface, occupancy, POSE, 0.0, frame_id=2)
    assert not result.movement_authorized
    assert result.decision.state == "BLOCKED"
    assert result.certificate["occupancy_blocking_columns"] >= 2


def test_ground_edge_under_body_blocks_even_when_centerline_clear():
    surface, occupancy = _maps(ground=lambda x, z:
        ROLE_NON_WALKABLE if 0.2 < x < 0.3 and 0.6 < z < 0.8 else ROLE_WALKABLE)
    assert surface._ray_candidate(POSE, 0.0).state == "CLEAR"
    result = query_fixed_route(surface, occupancy, POSE, frame_id=2)
    assert not result.movement_authorized
    assert result.decision.state == "OBSERVE"
    assert result.decision.guidance is None


def test_blind_zone_with_no_near_evidence_is_not_filled_free():
    surface, occupancy = _maps(
        ground=lambda x, z: ROLE_WALKABLE if z > 0.7 else None,
        visible=lambda x, z: z > 0.7, blind=0.8)
    assert fuse(surface.raycast_route(POSE), occupancy.raycast_clearance(POSE)).state == "CLEAR"
    result = query_fixed_route(surface, occupancy, POSE, frame_id=2)
    assert result.decision.state == "UNKNOWN"
    assert not result.movement_authorized
    assert result.certificate["surface_unknown_near_blind_zone"] > 0
    assert result.certificate["occupancy_unknown_near_blind_zone"] > 0
    assert not result.certificate["near_blind_zone_exempted"]


def test_unknown_column_at_side_of_body_is_not_ignored():
    surface, occupancy = _maps(visible=lambda x, z:
        not (0.2 < x < 0.3 and 0.6 < z < 0.8))
    assert occupancy._ray_candidate(POSE, 0.0).state == "CLEAR"
    result = query_fixed_route(surface, occupancy, POSE, frame_id=2)
    assert result.decision.state == "UNKNOWN"
    assert result.occupancy.unknown_cells > 0
    assert not result.movement_authorized


def test_immediate_body_obstacle_keeps_global_stop_priority():
    surface, occupancy = _maps(hits=[(0.01, 0.21, 0.8)])
    result = query_joint_route(surface, occupancy, POSE, math.radians(30), frame_id=2)
    assert result.decision.state == "BLOCKED"
    assert result.decision.reason == "confirmed_body_occupancy"
    assert not result.movement_authorized
    assert result.certificate["immediate_body_obstacle"]


def test_unconfirmed_immediate_hit_has_no_cautious_motion_authorization():
    surface, occupancy = _maps()
    surface.update_points(np.empty((0, 2)), np.empty(0), 3)
    occupancy.update_body_points(np.array([[0.01, 0.21, 0.8]]), POSE, 3)
    result = query_joint_route(surface, occupancy, POSE, frame_id=3)
    assert result.decision.state == "OBSERVE"
    assert result.decision.guidance is None
    assert not result.movement_authorized


def test_stale_map_snapshot_cannot_issue_a_direction():
    surface, occupancy = _maps()
    result = query_joint_route(surface, occupancy, POSE, frame_id=3)
    assert result.decision.state == "UNKNOWN"
    assert result.decision.reason == "MAP_SNAPSHOT_FRAME_MISMATCH"
    assert result.decision.guidance is None


def test_rotated_pose_checks_body_sweep_in_map_frame():
    rotated = SurfacePose(0.0, 0.0, math.pi / 2)
    surface, occupancy = _maps(hits=[(0.21, 0.81, 0.8)], pose=rotated)
    assert occupancy._ray_candidate(rotated, 0.0).state == "CLEAR"
    result = query_fixed_route(surface, occupancy, rotated, frame_id=2)
    assert not result.movement_authorized
    assert result.decision.state == "BLOCKED"
    assert result.certificate["world_angle_rad"] == pytest.approx(math.pi / 2)


def test_query_does_not_update_evidence_or_decay_maps():
    surface, occupancy = _maps()
    before = copy.deepcopy((surface.__dict__, occupancy.__dict__))
    first = query_joint_route(surface, occupancy, POSE, frame_id=2)
    second = query_joint_route(surface, occupancy, POSE, frame_id=2)
    assert before == (surface.__dict__, occupancy.__dict__)
    assert first.certificate == second.certificate


def test_margin_cannot_be_lowered_below_existing_body_policy():
    surface, occupancy = _maps()
    with pytest.raises(ValueError):
        query_fixed_route(surface, occupancy, POSE, frame_id=2,
                          cfg=JointRouteConfig(safety_margin_m=0.01))
