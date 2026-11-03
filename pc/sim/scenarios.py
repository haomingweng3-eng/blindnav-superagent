"""Closed-loop scenarios (V0.1 four + V0.2 additions: slalom, unknown near field, memory/ego-motion variants)."""
from __future__ import annotations

from typing import Callable, Dict, Optional

from blindnav_pc.config import Config

from .world import Box, Corridor, SimResult, run_sim

SCENARIOS = ["clear", "right_free", "left_free", "dead_end", "slalom", "gap_ok", "gap_too_narrow"]


def build_scenario(name: str) -> Corridor:
    c = Corridor(half_width_m=1.2, length_m=12.0)
    if name == "clear":
        pass
    elif name == "right_free":      # box centre-left of the corridor -> right side is freer
        c.boxes.append(Box(-0.55, 0.05, 4.5, 5.1))
    elif name == "left_free":       # mirror image
        c.boxes.append(Box(-0.05, 0.55, 4.5, 5.1))
    elif name == "dead_end":        # wall-to-wall barrier
        c.boxes.append(Box(-1.3, 1.3, 5.0, 5.6))
    elif name == "slalom":          # two obstacles, the second on the other side: avoid, recentre, avoid again
        c.boxes.append(Box(-0.55, 0.05, 4.0, 4.6))
        c.boxes.append(Box(-0.05, 0.55, 10.5, 11.1))
        c.length_m = 16.0
    elif name == "gap_ok":          # 1.1 m opening in a wall-to-wall barrier: wider than body + margin (0.8 m)
        c.boxes.append(Box(-1.3, -0.65, 5.0, 5.6))
        c.boxes.append(Box(0.45, 1.3, 5.0, 5.6))
    elif name == "gap_too_narrow":  # 0.5 m opening: must STOP, not squeeze through
        c.boxes.append(Box(-1.3, -0.25, 5.0, 5.6))
        c.boxes.append(Box(0.25, 1.3, 5.0, 5.6))
    else:
        raise KeyError(f"unknown scenario {name!r}; choose from {SCENARIOS}")
    return c


def run_scenario(name: str, cfg: Optional[Config] = None, steps: Optional[int] = None, **kw) -> SimResult:
    """kw is passed to ``run_sim`` (memory='oracle'|'full'|'rot_only'|'none', gyro_drift_dps, blind_m, fov_half_deg...)."""
    world = build_scenario(name)
    return run_sim(world, cfg, steps=steps or (200 if name in ("dead_end", "gap_too_narrow") else 400), **kw)
