"""Isolated, read-only same-path candidate for the PC comparison.

This module does not replace ``mobilio_fusion`` or update either map.  Each
candidate uses the *same* body-relative angle, origin, snapshot and swept body
band in both layers.  Existing centerline state/length gates remain additional
requirements.  Unknown cells, including the camera's near blind zone, are not
bridged by the candidate.

IMPORTANT: the historical evaluator supplies ground-height ``visible_free``
cells.  That is a horizontal visibility convention, not proof that the entire
human-height column is empty.  Certificates state that limitation explicitly;
``movement_authorized`` describes the offline map-contract decision only, and
is never a physical-safety or deployment certificate.
"""
from __future__ import annotations

import hashlib
import math
from collections import Counter
from dataclasses import dataclass, replace
from typing import Optional

from .mobilio_fusion import MobilioDecision
from .mobilio_occupancy import OccupancyGrid3D, OccupancyRoute
from .mobilio_surface import (
    ROLE_HAZARD, ROLE_NON_WALKABLE, ROLE_UNKNOWN, ROLE_WALKABLE,
    SurfaceGrid2D, SurfacePose, SurfaceRoute,
)
from .safety_states import BLOCKED, CLEAR, OBSERVE, UNKNOWN


@dataclass(frozen=True)
class JointRouteConfig:
    # The existing immediate-body check adds one occupancy cell to the radius.
    # None reuses that exact margin, rather than introducing a smaller one.
    safety_margin_m: Optional[float] = None


@dataclass(frozen=True)
class JointRouteResult:
    decision: MobilioDecision
    surface: SurfaceRoute
    occupancy: OccupancyRoute
    certificate: dict
    movement_authorized: bool


def _wrap(angle: float) -> float:
    return (float(angle) + math.pi) % (2.0 * math.pi) - math.pi


def _guidance(angle: float) -> str:
    return "STRAIGHT" if abs(math.degrees(angle)) < 15.0 else (
        "RIGHT" if angle > 0 else "LEFT")


def _point_segment_d2(px, pz, ax, az, bx, bz):
    dx, dz = bx - ax, bz - az
    length2 = dx * dx + dz * dz
    t = 0.0 if length2 == 0 else max(0.0, min(1.0,
        ((px - ax) * dx + (pz - az) * dz) / length2))
    return (px - ax - t * dx) ** 2 + (pz - az - t * dz) ** 2


def _segment_box_d2(ax, az, bx, bz, xmin, zmin, xmax, zmax):
    """Exact segment/axis-aligned-square distance, including corner contact."""
    lo, hi = 0.0, 1.0
    for origin, delta, low, high in (
            (ax, bx - ax, xmin, xmax), (az, bz - az, zmin, zmax)):
        if abs(delta) < 1e-14:
            if origin < low or origin > high:
                lo, hi = 1.0, 0.0
                break
        else:
            a, b = (low - origin) / delta, (high - origin) / delta
            lo, hi = max(lo, min(a, b)), min(hi, max(a, b))
            if lo > hi:
                break
    if lo <= hi:
        return 0.0
    distances = [_point_segment_d2(x, z, ax, az, bx, bz)
                 for x in (xmin, xmax) for z in (zmin, zmax)]
    for x, z in ((ax, az), (bx, bz)):
        distances.append(max(xmin - x, 0.0, x - xmax) ** 2
                         + max(zmin - z, 0.0, z - zmax) ** 2)
    return min(distances)


def _swept_cells(pose: SurfacePose, angle: float, distance: float,
                 radius: float, cell_m: float):
    """All squares touched by a disk swept along a finite line segment.

    A capsule includes the current footprint and the destination end cap.
    The returned distance is a conservative lower bound on first contact of
    the moving center with the square, useful for diagnostics, not a range
    measurement.  No sampling along a centerline or a few parallel rays is
    used, so thin obstacles and diagonal corner cells are not skipped.
    """
    dx, dz = math.sin(pose.yaw_rad + angle), math.cos(pose.yaw_rad + angle)
    ax, az = float(pose.x_m), float(pose.z_m)
    bx, bz = ax + dx * distance, az + dz * distance
    ix0 = math.floor((min(ax, bx) - radius) / cell_m)
    ix1 = math.floor((max(ax, bx) + radius) / cell_m)
    iz0 = math.floor((min(az, bz) - radius) / cell_m)
    iz1 = math.floor((max(az, bz) + radius) / cell_m)
    cells = []
    for ix in range(ix0, ix1 + 1):
        xmin, xmax = ix * cell_m, (ix + 1) * cell_m
        for iz in range(iz0, iz1 + 1):
            zmin, zmax = iz * cell_m, (iz + 1) * cell_m
            if _segment_box_d2(ax, az, bx, bz, xmin, zmin, xmax, zmax) <= radius * radius + 1e-12:
                first = min((x - ax) * dx + (z - az) * dz
                            for x in (xmin, xmax) for z in (zmin, zmax)) - radius
                cells.append((ix, iz, max(0.0, min(distance, first))))
    return sorted(cells, key=lambda cell: (cell[2], cell[0], cell[1]))


def _snapshot(surface: SurfaceGrid2D, occupancy: OccupancyGrid3D, frame_id: int):
    """Copy scalar evidence once, so all search candidates use one snapshot."""
    ground = {key: (cell.resolve(), max(cell.scores.values(), default=0.0), cell.last_frame)
              for key, cell in surface._cells.items()}
    hits = {}
    for key, state in occupancy._cells.items():
        _, _, height = occupancy.cell_to_world(key)
        if (state.evidence >= occupancy.cfg.remove_below
                and occupancy.cfg.min_obstacle_height_m <= height <= occupancy.cfg.person_height_m):
            hits.setdefault((key.ix, key.iz), []).append(
                (key.ih, bool(occupancy.is_confirmed(key)), state.last_frame))
    active = set(occupancy._frame_history)
    free = {}
    for key, seen in occupancy._visible_free.items():
        if seen in active and seen <= frame_id:
            free.setdefault((key.ix, key.iz), {})[key.ih] = seen
    identity = hashlib.sha256(repr((frame_id, sorted(ground.items()),
        sorted((key, sorted(value)) for key, value in hits.items()),
        sorted((key, sorted(value.items())) for key, value in free.items()))).encode()).hexdigest()
    return {"ground": ground, "hits": hits, "free": free, "id": identity,
            "surface_frame": surface._last_frame, "occupancy_frame": occupancy._last_frame,
            "active_occupancy_frames": sorted(active)}


def _base_certificate(pose, angle, frame_id, radius, margin, ground_distance, occupancy_distance, snap):
    return {
        "schema": "joint-route-certificate/1",
        "scope": "OFFLINE_EXISTING_HORIZONTAL_MAP_CONTRACT",
        "physical_safety_certified": False, "deployment_authorized": False,
        "full_volume_visibility_certified": False,
        "volume_limitation": "Observed ground columns do not certify free space at every human-height voxel.",
        "frame_id": int(frame_id), "snapshot_sha256": snap["id"],
        "surface_snapshot_frame": snap["surface_frame"],
        "occupancy_snapshot_frame": snap["occupancy_frame"],
        "occupancy_active_frame_ids": snap["active_occupancy_frames"],
        "evidence_age_unit": "recorded_frame_index; not a wall-clock latency measurement",
        "origin_map_m": [float(pose.x_m), float(pose.z_m)],
        "pose_yaw_rad": float(pose.yaw_rad),
        "angle_frame": "body_relative_yaw_positive_right",
        "candidate_angle_rad": float(angle), "selected_angle_rad": None,
        "world_angle_rad": _wrap(pose.yaw_rad + angle),
        "surface_angle_rad": float(angle), "occupancy_angle_rad": float(angle),
        "surface_checked_interval_m": [0.0, float(ground_distance)],
        "occupancy_checked_interval_m": [0.0, float(occupancy_distance)],
        "shared_checked_interval_m": [0.0, float(min(ground_distance, occupancy_distance))],
        "body_radius_m": float(radius - margin), "safety_margin_m": float(margin),
        "swept_half_width_m": float(radius), "swept_width_m": float(2 * radius),
        "footprint": "disk swept along the segment, including both end caps",
        "near_blind_zone_exempted": False, "movement_authorized": False,
    }


def _blank(reason: str, pose: SurfacePose, target: float, frame_id: int, *, state=UNKNOWN,
           immediate=None) -> JointRouteResult:
    s = SurfaceRoute(UNKNOWN, None, 0.0, 0, ROLE_UNKNOWN, 0, False, reason)
    o = OccupancyRoute(state, target, float(immediate[0]) if immediate else 0.0,
                       1 if immediate else 0, 0, state == BLOCKED, reason)
    d = MobilioDecision(state, None, None, reason, UNKNOWN, state)
    return JointRouteResult(d, s, o, {
        "schema": "joint-route-certificate/1", "status": reason,
        "scope": "OFFLINE_EXISTING_HORIZONTAL_MAP_CONTRACT",
        "frame_id": int(frame_id), "candidate_angle_rad": target,
        "selected_angle_rad": None, "movement_authorized": False,
        "physical_safety_certified": False, "deployment_authorized": False,
        "full_volume_visibility_certified": False, "path_checks_completed": False,
        "immediate_body_obstacle": bool(immediate),
    }, False)


def _check(surface, occupancy, pose, angle, frame_id, margin, snap):
    # Preserve the old exact-angle centerline states as *additional* gates.
    # Never call raycast_clearance here: it searches a different angle itself.
    old_s = surface._ray_candidate(pose, angle)
    old_o = occupancy._ray_candidate(pose, angle)
    radius = occupancy.cfg.person_radius_m + margin
    od = float(occupancy.cfg.padded_range_m)
    sd = max(float(surface.cfg.min_candidate_m), od)
    cert = _base_certificate(pose, angle, frame_id, radius, margin, sd, od, snap)
    ground_cells = _swept_cells(pose, angle, sd, radius, surface.cfg.cell_m)
    occ_cells = _swept_cells(pose, angle, od, radius, occupancy.cfg.cell_m)

    ground_counts = Counter()
    ground_ages, bad_ground, missing_ground_near = [], [], 0
    for ix, iz, first in ground_cells:
        role, weight, last = snap["ground"].get((ix, iz), (ROLE_UNKNOWN, 0.0, -1))
        age = frame_id - last if last >= 0 else None
        if (weight <= 0 or age is None or age < 0
                or age > surface.cfg.stale_after_frames):
            role = ROLE_UNKNOWN
        else:
            ground_ages.append(age)
        ground_counts[role] += 1
        if role != ROLE_WALKABLE:
            bad_ground.append((ix, iz, first, role))
            missing_ground_near += int(role == ROLE_UNKNOWN and first <= surface.cfg.blind_zone_m)
    surface_ok = (not bad_ground and old_s.state == CLEAR
                  and old_s.observed_distance_m >= surface.cfg.min_candidate_m)
    s_unknown = ground_counts[ROLE_UNKNOWN]
    if surface_ok:
        ss, sr = CLEAR, "same_path_swept_ground"
    elif s_unknown:
        ss, sr = UNKNOWN, "swept_ground_unknown"
    elif bad_ground:
        ss, sr = OBSERVE, "swept_ground_hazard_or_boundary"
    else:
        ss, sr = old_s.state, "centerline_surface:" + old_s.reason
    first_bad = bad_ground[0] if bad_ground else None
    s_distance = first_bad[2] if first_bad else min(sd, old_s.observed_distance_m)
    s = SurfaceRoute(ss, angle, float(s_distance), int(s_unknown),
        int(first_bad[3]) if first_bad else old_s.terminated_role, 1,
        not bool(s_unknown), sr,
        termination_distance_m=float(s_distance),
        termination_cell=(first_bad[0], first_bad[1]) if first_bad else old_s.termination_cell)

    unknown, occupied, confirmed, occ_ages, missing_occ_near = [], [], [], [], 0
    height_bins = range(math.floor(occupancy.cfg.min_obstacle_height_m / occupancy.cfg.height_cell_m),
                        math.floor(occupancy.cfg.person_height_m / occupancy.cfg.height_cell_m) + 1)
    missing_height_voxels = 0
    for ix, iz, first in occ_cells:
        hits = snap["hits"].get((ix, iz), [])
        observed = snap["free"].get((ix, iz), {})
        missing_height_voxels += sum(ih not in observed for ih in height_bins)
        if hits:
            occupied.append((ix, iz, first, len(hits)))
            if any(hit[1] for hit in hits):
                confirmed.append((ix, iz, first))
        elif not observed:
            unknown.append((ix, iz, first))
            missing_occ_near += int(first <= occupancy.cfg.blind_zone_m)
        else:
            occ_ages.extend(frame_id - seen for seen in observed.values())
    occupancy_ok = (not occupied and not unknown and old_o.state == CLEAR
                    and old_o.clear_distance_m >= od)
    if confirmed:
        os, oreason = BLOCKED, "confirmed_swept_body_occupancy"
    elif occupied:
        os, oreason = OBSERVE, "unconfirmed_swept_body_occupancy"
    elif unknown:
        os, oreason = UNKNOWN, "swept_occupancy_unobserved_columns"
    elif occupancy_ok:
        os, oreason = CLEAR, "same_path_observed_ground_columns"
    else:
        os, oreason = old_o.state, "centerline_occupancy:" + old_o.reason
    failures = [(x[2], x[0], x[1]) for x in occupied + unknown]
    o_distance = min((f[0] for f in failures), default=min(od, old_o.clear_distance_m))
    o = OccupancyRoute(os, angle, float(o_distance),
        sum(x[3] for x in occupied) or old_o.blocking_cells,
        len(unknown), bool(confirmed) or occupancy_ok, oreason)

    authorized = surface_ok and occupancy_ok
    if authorized:
        state, reason = CLEAR, "same_path_ground_and_occupancy"
    elif os == BLOCKED:
        state, reason = BLOCKED, oreason
    elif ss == UNKNOWN or os == UNKNOWN:
        state, reason = UNKNOWN, sr if ss == UNKNOWN else oreason
    else:
        state, reason = OBSERVE, sr if ss != CLEAR else oreason
    decision = MobilioDecision(state, angle if authorized else None,
        _guidance(angle) if authorized else None, reason, ss, os)
    cert.update({
        "status": "SAME_PATH_MAP_CONTRACT_PASSED" if authorized else "REJECTED",
        "path_checks_completed": True, "state": state, "reason": reason,
        "selected_angle_rad": angle if authorized else None,
        "movement_authorized": authorized,
        "surface_check_passed": surface_ok, "occupancy_check_passed": occupancy_ok,
        "centerline_surface_state": old_s.state, "centerline_occupancy_state": old_o.state,
        "surface_cells_checked": len(ground_cells),
        "surface_role_counts": {str(k): int(v) for k, v in ground_counts.items()},
        "surface_unknown_near_blind_zone": missing_ground_near,
        "surface_first_failure": list(first_bad) if first_bad else None,
        "surface_evidence_age_frames": [min(ground_ages), max(ground_ages)] if ground_ages else None,
        "occupancy_columns_checked": len(occ_cells),
        "occupancy_unknown_columns": len(unknown),
        "occupancy_unknown_near_blind_zone": missing_occ_near,
        "occupancy_blocking_columns": len(occupied),
        "occupancy_first_failure": list(min(failures)) if failures else None,
        "occupancy_evidence_age_frames": [min(occ_ages), max(occ_ages)] if occ_ages else None,
        "unobserved_human_height_voxels": missing_height_voxels,
        "full_volume_visibility_certified": missing_height_voxels == 0 and not occupied,
        "occupancy_evidence_mode": "existing_observed_ground_column_convention",
    })
    return JointRouteResult(decision, s, o, cert, authorized)


def _prepare(surface, occupancy, pose, angle, frame_id, cfg):
    if not all(math.isfinite(float(x)) for x in (pose.x_m, pose.z_m, pose.yaw_rad, angle)):
        return None, None, _blank("INVALID_GEOMETRY", pose, 0.0, frame_id)
    margin = occupancy.cfg.cell_m if cfg.safety_margin_m is None else float(cfg.safety_margin_m)
    if not math.isfinite(margin) or margin < occupancy.cfg.cell_m:
        raise ValueError("candidate margin must preserve the existing one-cell body margin")
    # Reuse, rather than weaken, the historical immediate-body priority.
    immediate = occupancy._body_intersection(pose)
    if immediate is not None:
        state = BLOCKED if immediate[2] else OBSERVE
        return None, None, _blank(
            "confirmed_body_occupancy" if immediate[2] else "unconfirmed_body_occupancy",
            pose, angle, frame_id, state=state, immediate=immediate)
    snap = _snapshot(surface, occupancy, frame_id)
    if snap["surface_frame"] != frame_id or snap["occupancy_frame"] != frame_id:
        result = _blank("MAP_SNAPSHOT_FRAME_MISMATCH", pose, angle, frame_id)
        result.certificate.update({"surface_snapshot_frame": snap["surface_frame"],
                                   "occupancy_snapshot_frame": snap["occupancy_frame"]})
        return None, None, result
    if (max(surface.cfg.min_candidate_m, occupancy.cfg.padded_range_m) > surface.cfg.max_range_m
            or occupancy.cfg.padded_range_m > occupancy.cfg.max_range_m):
        return None, None, _blank("INSUFFICIENT_CONFIGURED_HORIZON", pose, angle, frame_id)
    return margin, snap, None


def query_fixed_route(surface: SurfaceGrid2D, occupancy: OccupancyGrid3D,
                      pose: SurfacePose, angle_rad: float = 0.0, *, frame_id: int,
                      cfg: Optional[JointRouteConfig] = None) -> JointRouteResult:
    """Check exactly this direction.  This function never searches neighbors."""
    angle = _wrap(angle_rad)
    margin, snap, rejected = _prepare(surface, occupancy, pose, angle, frame_id, cfg or JointRouteConfig())
    return rejected if rejected is not None else _check(
        surface, occupancy, pose, angle, frame_id, margin, snap)


def query_joint_route(surface: SurfaceGrid2D, occupancy: OccupancyGrid3D,
                      pose: SurfacePose, target_angle_rad: float = 0.0, *, frame_id: int,
                      cfg: Optional[JointRouteConfig] = None) -> JointRouteResult:
    """Search the original candidate-angle intersection and certify one path.

    The caller updates separate old/new maps with identical inference products.
    No image, GT label, pose trajectory, model or decay operation is imported.
    """
    target = _wrap(target_angle_rad)
    margin, snap, rejected = _prepare(surface, occupancy, pose, target, frame_id, cfg or JointRouteConfig())
    if rejected is not None:
        return rejected

    def angles(config):
        half, step = math.radians(config.scan_half_angle_deg), math.radians(config.scan_step_deg)
        return [_wrap(target - half + i * step) for i in range(int(round(2 * half / step)) + 1)]

    occ_angles = {round(angle, 10) for angle in angles(occupancy.cfg)}
    candidates = list(dict.fromkeys(angle for angle in angles(surface.cfg)
                                   if round(angle, 10) in occ_angles))
    if not candidates:
        return _blank("NO_COMMON_CANDIDATE_ANGLES", pose, target, frame_id)
    results = [_check(surface, occupancy, pose, angle, frame_id, margin, snap) for angle in candidates]
    clear = [result for result in results if result.movement_authorized]
    # Each authorized candidate already meets both unchanged distance gates.
    # Prefer the target among jointly certified paths, deterministically.
    if clear:
        selected = min(clear, key=lambda r: (abs(_wrap(r.decision.angle_rad - target)), r.decision.angle_rad))
    else:
        selected = min(results, key=lambda r: (
            r.decision.state == BLOCKED,
            r.surface.unknown_cells + r.occupancy.unknown_cells,
            abs(_wrap(r.certificate["candidate_angle_rad"] - target)),
            r.certificate["candidate_angle_rad"]))
        # A blocked side ray does not prove every direction is blocked.
        state = BLOCKED if all(r.decision.state == BLOCKED for r in results) else (
            UNKNOWN if any(r.decision.state == UNKNOWN for r in results) else OBSERVE)
        selected = replace(selected, decision=MobilioDecision(state, None, None,
            "no_jointly_observed_body_path:" + selected.decision.reason,
            selected.surface.state, selected.occupancy.state))
    selected.certificate.update({
        "state": selected.decision.state, "reason": selected.decision.reason,
        "search_candidate_count": len(results), "jointly_clear_candidate_count": len(clear),
        "search_state_counts": dict(Counter(r.decision.state for r in results)),
        "search_candidates": [{"angle_rad": r.certificate["candidate_angle_rad"],
            "state": r.decision.state, "reason": r.decision.reason,
            "surface_unknown_cells": r.surface.unknown_cells,
            "occupancy_unknown_columns": r.occupancy.unknown_cells,
            "movement_authorized": r.movement_authorized} for r in results],
    })
    return selected
