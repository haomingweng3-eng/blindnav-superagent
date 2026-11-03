"""The four V0.1 scenarios."""
from __future__ import annotations

from typing import Callable, Dict, Optional

from blindnav_pc.config import Config

from .world import Box, Corridor, SimResult, run_sim

SCENARIOS = ["clear", "right_free", "left_free", "dead_end"]


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
    else:
        raise KeyError(f"unknown scenario {name!r}; choose from {SCENARIOS}")
    return c


def run_scenario(name: str, cfg: Optional[Config] = None, steps: Optional[int] = None) -> SimResult:
    world = build_scenario(name)
    return run_sim(world, cfg, steps=steps or (200 if name == "dead_end" else 300))
