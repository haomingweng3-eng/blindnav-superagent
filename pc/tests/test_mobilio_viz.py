import os
import sys

import cv2
import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "tools")))

from mobilio_viz import render_contact_sheet, render_replay_frame  # noqa: E402


def test_contact_sheet_has_four_panels(tmp_path):
    out = tmp_path / "sheet.png"
    case = {
        "rgb": np.zeros((120, 160, 3), dtype=np.uint8),
        "row": {"condition": "annotated", "arm": "mobilio", "session": "s",
                 "frame": 1, "safety_state": "UNKNOWN", "reason": "test",
                 "depth_source": "sanpo_gt", "pose_mode": "sanpo_recorded_pose"},
        "surface": {"cells": []},
        "occupancy": {"cells": []},
    }
    render_contact_sheet([case], str(out))
    image = cv2.imread(str(out))
    assert image is not None
    assert image.shape[0] >= 2 * 120
    assert image.shape[1] >= 2 * 160


def test_replay_frame_contains_both_decisions_and_fixed_layout():
    case = {
        "rgb": np.zeros((120, 160, 3), dtype=np.uint8),
        "baseline": {"effective_command": "STOP", "safety_state": "BLOCKED"},
        "candidate": {"effective_command": "RIGHT", "safety_state": "CLEAR"},
        "row": {"condition": "production", "session": "s", "frame": 7,
                 "depth_source": "dav2_calibrated"},
        "surface": {"cells": []}, "occupancy": {"cells": []},
    }
    frame = render_replay_frame(case)
    assert frame.shape == (480, 720, 3)
    assert int(frame.sum()) > 0
