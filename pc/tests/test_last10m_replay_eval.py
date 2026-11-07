import json
import unittest
from pathlib import Path

from blindnav_pc.last10m_replay_eval import evaluate_scenarios


ROOT = Path(__file__).parents[2]
SCENARIOS = ROOT / "app" / "src" / "main" / "assets" / "demo" / "last10m" / "scenarios.json"


class Last10mReplayEvalTest(unittest.TestCase):
    def test_replay_counts_relation_and_motion_gates_separately(self):
        result = evaluate_scenarios(json.loads(SCENARIOS.read_text(encoding="utf-8")))

        self.assertEqual(result["frames"], 7)
        self.assertEqual(result["target_lock"]["correct"], 6)
        self.assertEqual(result["entrance_relation"]["evaluated"], 5)
        self.assertEqual(result["entrance_relation"]["correct_same_target"], 4)
        self.assertEqual(result["entrance_relation"]["neighbor_rejected"], 1)
        self.assertEqual(result["ground"]["obstructed"], 2)
        self.assertEqual(result["motion_authorization"]["authorized"], 0)

    def test_unlabeled_frames_are_not_counted_as_wrong_entrance(self):
        result = evaluate_scenarios(json.loads(SCENARIOS.read_text(encoding="utf-8")))

        self.assertEqual(result["entrance_relation"]["unlabeled_refusal_context"], 2)
        self.assertEqual(result["entrance_relation"]["wrong_entrance"], 0)


if __name__ == "__main__":
    unittest.main()
