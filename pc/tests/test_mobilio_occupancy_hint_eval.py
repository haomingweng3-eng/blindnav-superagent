import unittest

from blindnav_pc.mobilio_occupancy_hint_eval import evaluate_rows


def row(**overrides):
    base = {
        "condition": "production", "arm": "mobilio", "safety_state": "UNKNOWN",
        "effective_command": "UNKNOWN", "authorized_motion": "", "danger_any": "False",
        "confirmed_clear": "False", "occupancy_state": "UNKNOWN",
        "occupancy_clear_distance_m": "2.0", "occupancy_blocking_cells": "0",
        "occupancy_unknown_cells": "2", "reason": "occupancy:no_fully_observed_clearance",
    }
    base.update(overrides)
    return base


class OccupancyHintEvalTest(unittest.TestCase):
    def test_partial_clearance_becomes_hint_only(self):
        result = evaluate_rows([row()])
        self.assertEqual(result["hint_frames"], 1)
        self.assertEqual(result["authorized_motion_frames"], 0)
        self.assertEqual(result["danger_forward_guidance_misses"]["denominator"], 0)

    def test_blocked_or_unobserved_rows_do_not_emit_hint(self):
        result = evaluate_rows([
            row(occupancy_clear_distance_m="0.0"),
            row(occupancy_blocking_cells="1"),
            row(occupancy_state="CLEAR", effective_command="STRAIGHT"),
        ])
        self.assertEqual(result["hint_frames"], 0)
        self.assertEqual(result["authorized_motion_frames"], 1)


if __name__ == "__main__":
    unittest.main()
