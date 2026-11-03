"""Tiny 2D kinematic simulator closing the loop: synthetic BEV -> planner -> policy -> walker."""
from .world import Box, Corridor, Walker, SimResult, run_sim  # noqa: F401
from .scenarios import SCENARIOS, build_scenario, run_scenario  # noqa: F401
