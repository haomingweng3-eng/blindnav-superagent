"""Small deterministic visual diagnostics for the Mobilio SANPO replay."""
from __future__ import annotations

import math
import os
from typing import Iterable, Sequence, Tuple

import cv2
import numpy as np


PANEL_W = 360
PANEL_H = 240


def _panel(image: np.ndarray, title: str) -> np.ndarray:
    arr = np.asarray(image, dtype=np.uint8)
    if arr.ndim == 2:
        arr = cv2.cvtColor(arr, cv2.COLOR_GRAY2BGR)
    if arr.shape[:2] != (PANEL_H, PANEL_W):
        arr = cv2.resize(arr, (PANEL_W, PANEL_H), interpolation=cv2.INTER_AREA)
    out = arr.copy()
    cv2.rectangle(out, (0, 0), (PANEL_W - 1, 25), (0, 0, 0), -1)
    cv2.putText(out, str(title), (8, 18), cv2.FONT_HERSHEY_SIMPLEX,
                0.52, (255, 255, 255), 1, cv2.LINE_AA)
    return out


def _surface_panel(snapshot: dict, row: dict) -> np.ndarray:
    canvas = np.full((PANEL_H, PANEL_W, 3), 245, dtype=np.uint8)
    cells = snapshot.get("cells", []) if isinstance(snapshot, dict) else []
    cell_m = float(snapshot.get("cell_m", 0.2)) if isinstance(snapshot, dict) else 0.2
    points = []
    for cell in cells:
        x = float(cell.get("x_m", cell.get("ix", 0) * cell_m))
        z = float(cell.get("z_m", cell.get("iz", 0) * cell_m))
        points.append((x, z, int(cell.get("role", 0))))
    scale = 22.0
    ox, oz = PANEL_W // 2, PANEL_H - 28
    for x, z, role in points:
        px, py = int(round(ox + x * scale)), int(round(oz - z * scale))
        if 0 <= px < PANEL_W and 25 <= py < PANEL_H:
            color = {0: (180, 180, 180), 1: (70, 180, 70),
                     2: (0, 165, 255), 3: (60, 60, 220)}.get(role, (120, 120, 120))
            cv2.rectangle(canvas, (px - 3, py - 3), (px + 3, py + 3), color, -1)
    cv2.circle(canvas, (ox, oz), 5, (0, 0, 0), -1)
    angle = row.get("surface_angle_rad")
    if angle is not None:
        length = 90
        px = int(round(ox + math.sin(float(angle)) * length))
        py = int(round(oz - math.cos(float(angle)) * length))
        cv2.arrowedLine(canvas, (ox, oz), (px, py), (255, 0, 0), 2, tipLength=0.15)
    return _panel(canvas, "Surface Grid / selected ray")


def _occupancy_panel(snapshot: dict, row: dict) -> np.ndarray:
    canvas = np.full((PANEL_H, PANEL_W, 3), 245, dtype=np.uint8)
    cells = snapshot.get("cells", []) if isinstance(snapshot, dict) else []
    points = []
    for cell in cells:
        points.append((float(cell.get("x_m", 0.0)), float(cell.get("z_m", 0.0)),
                       bool(cell.get("confirmed", False))))
    scale = 38.0
    ox, oz = PANEL_W // 2, PANEL_H - 28
    for x, z, confirmed in points:
        px, py = int(round(ox + x * scale)), int(round(oz - z * scale))
        if 0 <= px < PANEL_W and 25 <= py < PANEL_H:
            color = (50, 50, 220) if confirmed else (0, 165, 255)
            cv2.circle(canvas, (px, py), 5, color, -1)
    cv2.ellipse(canvas, (ox, oz), (int(0.2 * scale), int(0.2 * scale)),
                0, 0, 360, (0, 0, 0), 2)
    angle = row.get("occupancy_angle_rad")
    if angle is not None:
        length = 110
        px = int(round(ox + math.sin(float(angle)) * length))
        py = int(round(oz - math.cos(float(angle)) * length))
        cv2.arrowedLine(canvas, (ox, oz), (px, py), (255, 0, 0), 2, tipLength=0.15)
    return _panel(canvas, "3D occupancy / body footprint")


def _text_panel(row: dict) -> np.ndarray:
    canvas = np.full((PANEL_H, PANEL_W, 3), 32, dtype=np.uint8)
    lines = [
        f"{row.get('condition', '?')} / {row.get('arm', '?')}",
        f"session={str(row.get('session', '?'))[:12]} frame={row.get('frame', '?')}",
        f"state={row.get('safety_state', '?')} command={row.get('effective_command', '?')}",
        f"reason={row.get('reason', '')}",
        f"depth={row.get('depth_source', '?')}",
        "pose=SANPO recorded pose (offline VIO proxy)",
        f"surface={row.get('surface_state', '?')} occ={row.get('occupancy_state', '?')}",
    ]
    for i, line in enumerate(lines):
        cv2.putText(canvas, str(line)[: fifty_chars()], (8, 28 + i * 28),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.44, (230, 230, 230), 1, cv2.LINE_AA)
    return _panel(canvas, "Decision diagnostics")


def fifty_chars() -> int:
    return 58


def _one_case(case: dict) -> np.ndarray:
    row = dict(case.get("row", {}))
    rgb = np.asarray(case.get("rgb", np.zeros((PANEL_H, PANEL_W, 3), dtype=np.uint8)))
    if rgb.ndim != 3 or rgb.shape[2] != 3:
        rgb = np.zeros((PANEL_H, PANEL_W, 3), dtype=np.uint8)
    # The evaluator stores RGB/BGR consistently as OpenCV BGR arrays; no colour conversion is
    # performed here so a saved panel is byte-stable with the input frame.
    rgb_panel = _panel(rgb, "Input RGB / role context")
    return np.vstack([
        np.hstack([rgb_panel, _surface_panel(case.get("surface", {}), row)]),
        np.hstack([_occupancy_panel(case.get("occupancy", {}), row), _text_panel(row)]),
    ])


def render_replay_frame(case: dict) -> np.ndarray:
    """Render one fixed-size A/B replay frame for the SANPO video."""
    row = dict(case.get("row", {}))
    baseline = dict(case.get("baseline", {}))
    candidate = dict(case.get("candidate", {}))
    text = np.full((PANEL_H, PANEL_W, 3), 32, dtype=np.uint8)
    lines = [
        f"{row.get('condition', '?')} session={str(row.get('session', '?'))[:10]}",
        f"frame={row.get('frame', '?')} depth={row.get('depth_source', '?')}",
        f"baseline: {baseline.get('effective_command', '?')} / {baseline.get('safety_state', '?')}",
        f"mobilio:  {candidate.get('effective_command', '?')} / {candidate.get('safety_state', '?')}",
        "pose=SANPO recorded pose (offline VIO proxy)",
        "maps persist across frames; arrows show selected route",
    ]
    for i, line in enumerate(lines):
        cv2.putText(text, str(line)[:58], (8, 30 + i * 32),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, (230, 230, 230), 1, cv2.LINE_AA)
    return np.vstack([
        np.hstack([_panel(np.asarray(case.get("rgb", np.zeros((PANEL_H, PANEL_W, 3), dtype=np.uint8))),
                          "SANPO input"),
                   _surface_panel(case.get("surface", {}), candidate)]),
        np.hstack([_occupancy_panel(case.get("occupancy", {}), candidate),
                   _panel(text, "Frame A/B decisions")]),
    ])


def render_contact_sheet(cases: Sequence[dict], out_path: str) -> None:
    """Render one 2x2 diagnostic tile per case in a deterministic vertical sheet."""
    if not cases:
        raise ValueError("at least one case is required")
    tiles = [_one_case(case) for case in cases]
    sheet = np.vstack(tiles)
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    if not cv2.imwrite(out_path, sheet):
        raise OSError(f"could not write contact sheet {out_path}")
