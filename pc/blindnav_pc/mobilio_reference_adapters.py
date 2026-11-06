"""Adapters from BlindNav geometry products to the isolated Harvard ports."""
from __future__ import annotations
import math
import numpy as np

from .bev import depth_to_level_points
from .mobilio_reference import HarvardSurfaceReference
from .mobilio_occupancy_reference import HarvardOccupancyReference
from .mobilio_surface import SurfacePose


def world_surface_points(roles, depth_m, K, up, camera_height_m, pose, walkable_role=1,
                         pixel_stride=4):
    """Project only walkable ground samples into Harvard's boolean world grid."""
    lat, fwd, height, (vs, us) = depth_to_level_points(
        np.asarray(depth_m), np.asarray(K), np.asarray(up), float(camera_height_m),
        stride=int(pixel_stride))
    roles = np.asarray(roles)
    keep = (roles[vs, us] == int(walkable_role)) & np.isfinite(height) & (np.abs(height) <= 0.15)
    cos_y, sin_y = math.cos(pose.yaw_rad), math.sin(pose.yaw_rad)
    x = pose.x_m + lat[keep] * cos_y + fwd[keep] * sin_y
    z = pose.z_m - lat[keep] * sin_y + fwd[keep] * cos_y
    return np.column_stack([x, z]) if np.any(keep) else np.zeros((0, 2))


def obstacle_body_points(roles, depth_m, K, up, camera_height_m, min_height=0.15,
                         max_height=1.7, pixel_stride=4):
    """Return gravity-aligned obstacle points for Harvard's 3-D reference grid."""
    lat, fwd, height, (vs, us) = depth_to_level_points(
        np.asarray(depth_m), np.asarray(K), np.asarray(up), float(camera_height_m),
        stride=int(pixel_stride))
    role = np.asarray(roles)[vs, us]
    keep = ((role != 1) & np.isfinite(height) & (height >= float(min_height))
            & (height <= float(max_height)))
    return np.column_stack([lat[keep], height[keep], fwd[keep]]) if np.any(keep) else np.zeros((0, 3))


def update_reference_maps(surface: HarvardSurfaceReference, occupancy: HarvardOccupancyReference,
                          roles, depth_m, K, up, camera_height_m, pose, pixel_stride=4):
    """Update both isolated reference maps from one shared input frame."""
    surface_points = world_surface_points(roles, depth_m, K, up, camera_height_m, pose,
                                          pixel_stride=pixel_stride)
    surface.grid.update({(int(round(x / surface.cfg.node_size)),
                          int(round(z / surface.cfg.node_size))): True
                         for x, z in surface_points})
    body = obstacle_body_points(roles, depth_m, K, up, camera_height_m,
                                pixel_stride=pixel_stride)
    world = occupancy_reference_body_to_world(body, pose)
    occupancy.add_to_grid(world)
    return len(surface_points), len(body)


def occupancy_reference_body_to_world(points_body, pose):
    points = np.asarray(points_body, dtype=float).reshape(-1, 3)
    if not len(points): return []
    lat, height, fwd = points.T
    c, s = math.cos(pose.yaw_rad), math.sin(pose.yaw_rad)
    x = pose.x_m + fwd * s + lat * c
    z = pose.z_m + fwd * c - lat * s
    return list(zip(x.tolist(), height.tolist(), z.tolist()))
