import os
import sys

import cv2
import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "tools")))

from mobilio_viz import render_contact_sheet  # noqa: E402


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
