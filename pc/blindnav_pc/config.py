"""Configuration dataclasses for the BlindNav PC closed-loop demo (V0.1).

Camera frame convention everywhere (OpenCV): x right, y down, z forward.
Level (gravity-aligned) BEV frame: x = lateral (+right), z = forward (horizontal), height = up.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields, is_dataclass
from typing import Any, Optional

# BEV cell states
UNKNOWN = 0
FREE = 1
OCCUPIED = 2


@dataclass
class GroundConfig:
    camera_height_m: float = 1.20        # assumed hand / chest camera height
    roi_top_frac: float = 0.55           # ground samples only from rows below this fraction
    erode_px: int = 5                    # erosion kernel for the ground mask
    max_samples: int = 2500              # sub-sampled ground pixels for the fit
    up_dot_eps: float = 0.02             # need up.r < -eps for a flat-ground intersection
    max_ground_depth_m: float = 12.0     # ignore far ground pixels (flat-ground assumption degrades)
    min_ground_depth_m: float = 0.3
    ransac_iters: int = 120
    rel_tol: float = 0.08                # inlier tolerance, relative to 1/Z
    abs_tol: float = 0.01                # absolute floor (1/m)
    min_pixels: int = 150
    min_confidence: float = 0.45
    min_q_spread: float = 0.02           # std(q)/mean(q) must exceed this (else a,b ambiguous)
    reuse_frames: int = 10               # reuse previous (a,b) at most this many frames
    ema: float = 0.5                     # weight of the new fit when smoothing (1 = no smoothing)
    max_depth_m: float = 15.0
    seed: int = 0


@dataclass
class BEVConfig:
    lateral_half_m: float = 1.5
    forward_m: float = 4.0
    cell_m: float = 0.1
    decay: float = 0.9
    ground_tol_m: float = 0.15           # |height| below this = ground surface
    obstacle_min_m: float = 0.15
    obstacle_max_m: float = 2.0          # above head clearance -> ignored
    drop_m: float = -0.25                # points lower than this = drop-off -> occupied
    pixel_stride: int = 4
    min_points: int = 2                  # points per cell per frame to count as evidence
    occ_thr: float = 0.5
    free_thr: float = 0.5
    free_clears_occ: float = 0.5         # a clear free observation shrinks occupied evidence
    non_walkable_ground_is_occupied: bool = True   # road / grass / water-like ground = not walkable

    @property
    def nx(self) -> int:
        return int(round(2 * self.lateral_half_m / self.cell_m))

    @property
    def nz(self) -> int:
        return int(round(self.forward_m / self.cell_m))


@dataclass
class PlannerConfig:
    heading_max_deg: float = 35.0
    heading_step_deg: float = 5.0
    corridor_len_m: float = 1.5          # collision corridor length ahead
    body_half_m: float = 0.2
    margin_m: float = 0.1                # corridor half-width = body_half + margin = 0.3
    alpha: float = 1.0                   # clear distance (normalised by forward range)
    beta: float = 0.5                    # free width (normalised)
    gamma: float = 0.6                   # heading error vs target (normalised by max heading)
    delta: float = 0.8                   # unknown ratio
    passable_clear_m: float = 1.5        # a heading is passable if clear distance >= this
    near_unknown_ignore_m: float = 0.6   # unknown cells closer than this (camera blind zone) do not block
    unknown_block_m: float = 0.5         # an unknown run this long (beyond the blind zone) ends the clear distance
    ray_step_m: float = 0.05
    free_width_at_m: float = 1.5
    side_deg: float = 7.5                # |heading| below this counts as "center"

    @property
    def half_width_m(self) -> float:
        return self.body_half_m + self.margin_m


@dataclass
class PolicyConfig:
    stop_persist_ms: int = 300
    stop_hold_ms: int = 800
    side_frames: int = 3
    side_dwell_ms: int = 400
    center_frames: int = 2
    lost_ms: int = 500                   # ground/geometry lost: hold, then OBSERVE
    observe_to_stop_ms: int = 2000       # OBSERVE longer than this -> STOP
    imminent_dist_m: float = 0.7         # corridor obstacle closer than this -> stop evidence
    recenter_delay_ms: int = 1200        # go straight this long after a bypass before recentering
    recenter_thr_m: float = 0.25
    recenter_done_m: float = 0.10
    assumed_side_speed_mps: float = 0.25 # lateral speed assumed while a side command is followed
    recenter_heading_deg: float = 10.0


@dataclass
class CameraConfig:
    fov_deg: float = 70.0                # horizontal FOV fallback
    pitch_deg: float = 15.0              # fallback camera pitch (down positive) when no IMU
    roll_deg: float = 0.0


@dataclass
class Config:
    ground: GroundConfig = field(default_factory=GroundConfig)
    bev: BEVConfig = field(default_factory=BEVConfig)
    planner: PlannerConfig = field(default_factory=PlannerConfig)
    policy: PolicyConfig = field(default_factory=PolicyConfig)
    camera: CameraConfig = field(default_factory=CameraConfig)

    @classmethod
    def from_json(cls, path: Optional[str]) -> "Config":
        cfg = cls()
        if path:
            with open(path, "r", encoding="utf-8") as f:
                _update(cfg, json.load(f))
        return cfg

    def to_dict(self) -> dict:
        return asdict(self)


def _update(obj: Any, data: dict) -> None:
    names = {f.name for f in fields(obj)}
    for k, v in data.items():
        if k not in names:
            raise KeyError(f"unknown config key: {k}")
        cur = getattr(obj, k)
        if is_dataclass(cur) and isinstance(v, dict):
            _update(cur, v)
        else:
            setattr(obj, k, type(cur)(v) if cur is not None else v)
