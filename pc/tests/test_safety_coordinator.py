"""Safety Coordinator (Issue #18): fusion invariants.

* the fused state is the most severe responsibility state (monotone fusion);
* BLOCKED can only come from a confirmed Obstacle Guardian ``BLOCKED``;
* a Path Keeper NON_WALKABLE/curb/drift OBSERVE can never become BLOCKED/STOP;
* guidance is passed through only on CLEAR;
* every state has a distinct effective command for the rebuilt measurement.
"""
import itertools

import pytest

from blindnav_pc.obstacle_guardian import ObstacleRisk
from blindnav_pc.path_keeper import PathState
from blindnav_pc.safety_coordinator import LEFT, RIGHT, STRAIGHT, SafetyCoordinator, fuse
from blindnav_pc.safety_states import BLOCKED, CLEAR, OBSERVE, UNKNOWN, most_severe

PATH_STATES = (CLEAR, OBSERVE, UNKNOWN)
OBSTACLE_RISKS = (
    ObstacleRisk(CLEAR, "no_occupancy"),
    ObstacleRisk(OBSERVE, "obstacle_unconfirmed_in_path"),
    ObstacleRisk(UNKNOWN, "depth_quality_insufficient"),
    ObstacleRisk(BLOCKED, "obstacle_confirmed_in_path"),
)


def path_state(status, guidance="STRAIGHT"):
    return PathState(status=status, guidance=guidance if status == CLEAR else None,
                     reason=f"reason_{status}", coverage=1.0, n_points=400,
                     walkable_offset_m=0.0, walker_offset_m=0.0, yaw_error_rad=0.0)


@pytest.mark.parametrize("path_status,obstacle",
                         list(itertools.product(PATH_STATES, OBSTACLE_RISKS)))
def test_fusion_is_monotone_and_blocked_requires_confirmed_obstacle(path_status, obstacle):
    coord = SafetyCoordinator()
    decision = coord.decide(path_state(path_status), obstacle)
    assert decision.state == most_severe(path_status, obstacle.state)
    if decision.state == BLOCKED:
        assert obstacle.state == BLOCKED, "only the Obstacle Guardian may produce BLOCKED"
    if path_status in (OBSERVE, UNKNOWN):
        assert decision.state != BLOCKED or obstacle.state == BLOCKED


@pytest.mark.parametrize("guidance", [STRAIGHT, LEFT, RIGHT])
def test_clear_passes_guidance_through(guidance):
    decision = fuse(path_state(CLEAR, guidance), ObstacleRisk(CLEAR, "no_occupancy"))
    assert decision.state == CLEAR
    assert decision.guidance == guidance
    assert decision.effective_command() == guidance
    assert decision.is_pass()


@pytest.mark.parametrize("path_status", [OBSERVE, UNKNOWN])
def test_non_clear_withholds_guidance(path_status):
    decision = fuse(path_state(path_status, "LEFT"), ObstacleRisk(CLEAR, "no_occupancy"))
    assert decision.guidance is None and not decision.is_pass()
    assert decision.effective_command() in (OBSERVE, UNKNOWN)


def test_effective_command_vocabulary_is_distinct():
    coord = SafetyCoordinator()
    commands = set()
    for path_status, obstacle in itertools.product(PATH_STATES, OBSTACLE_RISKS):
        commands.add(coord.decide(path_state(path_status), obstacle).effective_command())
    assert commands == {"STRAIGHT", "OBSERVE", "UNKNOWN", "STOP"}


def test_surface_risk_alone_never_stop():
    decision = fuse(path_state(OBSERVE), ObstacleRisk(CLEAR, "no_occupancy"))
    assert decision.state == OBSERVE and decision.effective_command() != "STOP"
    assert "path:" in decision.reason


def test_confirmed_obstacle_beats_unknown_path():
    decision = fuse(path_state(UNKNOWN), ObstacleRisk(BLOCKED, "obstacle_confirmed_in_path"))
    assert decision.state == BLOCKED and decision.effective_command() == "STOP"
    assert "obstacle:" in decision.reason


def test_fusion_rejects_unknown_state_names():
    with pytest.raises(ValueError):
        most_severe(CLEAR, "SAFE")
