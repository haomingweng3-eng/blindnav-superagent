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
    decay: float = 0.9                   # evidence decay per frame WITH ego-motion compensation
    decay_no_ego: float = 0.3            # without ego-motion: history is nearly dropped (current frame dominates)
    footprint_half_m: float = 0.25       # the person's current footprint is known free
    footprint_len_m: float = 0.35
    ground_tol_m: float = 0.15           # |height| below this = ground surface
    obstacle_min_m: float = 0.15
    obstacle_max_m: float = 2.0          # above head clearance -> ignored
    drop_m: float = -0.25                # points lower than this = drop-off -> occupied
    pixel_stride: int = 4
    min_points: int = 2                  # points per cell per frame to count as evidence
    occ_thr: float = 0.5
    free_thr: float = 0.5
    free_clears_occ: float = 0.5         # a clear free observation shrinks occupied evidence
    free_clears_occ_ego: float = 0.2     # same, when old evidence was warped by ego-motion (ghost suppression)
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
    body_width_m: float = 0.5            # human body width
    margin_m: float = 0.15               # safety margin added on each side (inflation = body_half + margin = 0.4)
    widen_max_deg: float = 60.0          # if nothing is passable within +-heading_max_deg, widen to this before STOP
    widen_min_progress_m: float = 1.0    # a widened heading must be seen free to the end of the view with >= this forward progress
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
    def body_half_m(self) -> float:
        return self.body_width_m / 2.0

    @property
    def half_width_m(self) -> float:
        """Inflation radius: swept half-width of the footprint corridor = body half-width + safety margin."""
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


@dataclass
class EgoConfig:
    """Ego-motion (PDR) from an orientation sensor (game-rotation-vector style) + step events."""
    step_length_m: float = 0.70          # personal step length L_step
    max_dt_ms: float = 1500.0            # longer gap between frames -> motion unreliable, drop history
    max_dyaw_deg: float = 60.0           # larger heading jump per frame -> unreliable
    use_steps: bool = True               # False: rotation-only warp (gyro only)


@dataclass
class BehaviorConfig:
    """CRUISE -> AVOID_LEFT/RIGHT -> PASSING -> RECENTER -> CRUISE."""
    lookahead_m: float = 1.5             # RECENTER goal = (0, z_hat + lookahead)
    pass_len_m: float = 1.3              # PDR progress beyond the obstacle front (assumed depth 0.8 + body 0.5) before RECENTER may start
    pass_timeout_ms: float = 4500.0      # fallback when no step events exist (PASSING lasts at most this long)
    recenter_thr_m: float = 0.25         # |x_hat| above this after passing -> RECENTER
    recenter_done_m: float = 0.10        # |x_hat| below this ...
    heading_done_deg: float = 6.0        # ... with heading error below this ...
    center_free_frames: int = 3          # ... and the centre corridor free for N frames -> CRUISE
    recenter_max_deg: float = 20.0       # clip of the goal bearing
    recenter_timeout_ms: float = 8000.0  # give up (re-anchor the reference line here) after this long


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
    ego: EgoConfig = field(default_factory=EgoConfig)
    behavior: BehaviorConfig = field(default_factory=BehaviorConfig)

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
