"""Overlay visualisation: walkable mask, depth inset, BEV grid with planner headings, CJK text."""
from __future__ import annotations

import glob
import os
import shutil
import subprocess
from functools import lru_cache
from typing import Optional, Sequence, Tuple

import cv2
import numpy as np

_FONT_DIRS = ("/usr/share/fonts", "/usr/local/share/fonts", os.path.expanduser("~/.fonts"),
              "/System/Library/Fonts", "/Library/Fonts", "C:/Windows/Fonts")
_FONT_HINTS = ("notosanscjk", "notoserifcjk", "wqy", "wenquanyi", "simhei", "msyh", "pingfang", "sourcehan", "heiti")


@lru_cache(maxsize=1)
def find_cjk_font() -> Optional[str]:
    """Path of the first CJK-capable font found (fc-match, then well-known directories) or None."""
    if shutil.which("fc-match"):
        try:
            p = subprocess.check_output(["fc-match", "-f", "%{file}", ":lang=zh"], text=True,
                                        stderr=subprocess.DEVNULL, timeout=5).strip()
            if p and os.path.exists(p) and any(h in p.lower().replace("-", "").replace(" ", "") for h in _FONT_HINTS):
                return p
        except Exception:
            pass
    for d in _FONT_DIRS:
        if not os.path.isdir(d):
            continue
        for p in glob.glob(os.path.join(d, "**", "*.[tTo][tTp][cCfF]"), recursive=True):
            if any(h in os.path.basename(p).lower().replace("-", "") for h in _FONT_HINTS):
                return p
    return None


@lru_cache(maxsize=8)
def _font(size: int):
    from PIL import ImageFont
    p = find_cjk_font()
    return ImageFont.truetype(p, size) if p else None


def put_text(img: np.ndarray, text: str, org: Tuple[int, int], color=(255, 255, 255), size: int = 20,
             ascii_fallback: Optional[str] = None) -> np.ndarray:
    """Draw (Chinese) text in place on a BGR image.  Never raises; ASCII fallback if no CJK font."""
    try:
        font = _font(int(size))
        if font is not None:
            from PIL import Image, ImageDraw
            x, y = int(org[0]), int(org[1])
            w, h = img.shape[1], img.shape[0]
            x0, y0, x1, y1 = max(0, x), max(0, y), min(w, x + int(size * (len(text) + 2))), min(h, y + int(size * 1.6))
            if x1 <= x0 or y1 <= y0:
                return img
            patch = Image.fromarray(cv2.cvtColor(img[y0:y1, x0:x1], cv2.COLOR_BGR2RGB))
            draw = ImageDraw.Draw(patch)
            draw.text((x - x0 + 1, y - y0 + 1), text, font=font, fill=(0, 0, 0))     # shadow
            draw.text((x - x0, y - y0), text, font=font, fill=tuple(int(c) for c in color[::-1]))
            img[y0:y1, x0:x1] = cv2.cvtColor(np.asarray(patch), cv2.COLOR_RGB2BGR)
        else:
            t = ascii_fallback if ascii_fallback is not None else text.encode("ascii", "replace").decode()
            cv2.putText(img, t, (int(org[0]), int(org[1]) + int(size)), cv2.FONT_HERSHEY_SIMPLEX,
                        size / 30.0, color, 1, cv2.LINE_AA)
    except Exception:
        pass
    return img


_COLORS = {0: (60, 60, 60), 1: (70, 170, 70), 2: (50, 50, 210)}      # unknown / free / occupied (BGR)


def render_bev(states: np.ndarray, cell_m: float, lateral_half_m: float, plan=None,
               corridor: Optional[Tuple[float, float]] = None, scale_px: int = 8) -> np.ndarray:
    """Top-down BEV: walker at bottom centre, forward = up.  ``corridor`` = (length_m, half_width_m)."""
    s = np.asarray(states, dtype=np.uint8)
    nz, nx = s.shape
    img = np.zeros((nz, nx, 3), np.uint8)
    for k, c in _COLORS.items():
        img[s == k] = c
    img = cv2.resize(img[::-1], (nx * scale_px, nz * scale_px), interpolation=cv2.INTER_NEAREST)  # row 0 -> bottom
    H = img.shape[0]
    px_per_m = scale_px / cell_m
    cx = int(round(lateral_half_m * px_per_m))
    org = (cx, H - 1)

    def tip(deg: float, length_m: float):
        a = np.deg2rad(deg)
        return int(cx + np.sin(a) * length_m * px_per_m), int(H - 1 - np.cos(a) * length_m * px_per_m)

    if corridor is not None:
        length, half = corridor
        cv2.rectangle(img, (int(cx - half * px_per_m), int(H - 1 - length * px_per_m)),
                      (int(cx + half * px_per_m), H - 1), (255, 255, 0), 1)
    if plan is not None:
        scores = np.asarray(plan.scores, dtype=float)
        norm = (scores - scores.min()) / (np.ptp(scores) + 1e-9)
        for h, v in zip(plan.headings_deg, norm):
            g = int(70 + 150 * v)
            cv2.line(img, org, tip(float(h), 1.5), (g, g, g), 1, cv2.LINE_AA)
        cv2.arrowedLine(img, org, tip(float(plan.best_heading_deg), 2.0), (0, 255, 255), 3, cv2.LINE_AA, tipLength=0.2)
    cv2.circle(img, org, 4, (255, 255, 255), -1)
    return img


def overlay_frame(frame_bgr: np.ndarray, mask: np.ndarray, depth_metric: Optional[np.ndarray] = None,
                  text_lines: Optional[Sequence[str]] = None, alpha: float = 0.4) -> np.ndarray:
    """Green tint on the walkable mask, optional metric-depth colormap inset (top right), text (top left)."""
    out = frame_bgr.copy()
    m = np.asarray(mask) > 0
    if m.shape != out.shape[:2]:
        m = cv2.resize(m.astype(np.uint8), (out.shape[1], out.shape[0]), interpolation=cv2.INTER_NEAREST) > 0
    tint = np.array((0, 200, 0), np.float32)
    out[m] = ((1 - alpha) * out[m].astype(np.float32) + alpha * tint).astype(np.uint8)
    if depth_metric is not None:
        d = np.asarray(depth_metric, dtype=np.float32)
        ok = np.isfinite(d)
        if ok.any():
            norm = np.zeros(d.shape, np.uint8)
            norm[ok] = np.clip(255 * (1 - np.minimum(d[ok], 8.0) / 8.0), 0, 255).astype(np.uint8)  # near = bright
            cm = cv2.applyColorMap(norm, cv2.COLORMAP_TURBO)
            cm[~ok] = 0
            h = max(1, out.shape[0] // 4)
            w = max(1, int(cm.shape[1] * h / cm.shape[0]))
            out[:h, out.shape[1] - w:] = cv2.resize(cm, (w, h))
    for i, line in enumerate(text_lines or []):
        put_text(out, str(line), (8, 6 + i * 26), (255, 255, 255), 22)
    return out


def compose(frame_overlay: np.ndarray, bev_img: np.ndarray) -> np.ndarray:
    """Side by side; the BEV is scaled to the frame height."""
    h = frame_overlay.shape[0]
    w = max(1, int(round(bev_img.shape[1] * h / bev_img.shape[0])))
    return np.hstack([frame_overlay, cv2.resize(bev_img, (w, h), interpolation=cv2.INTER_NEAREST)])


class VideoWriter:
    """mp4v writer; ``frame_size`` = (width, height)."""

    def __init__(self, path: str, fps: float, frame_size: Tuple[int, int]):
        self._w = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), float(fps or 10.0), tuple(frame_size))
        if not self._w.isOpened():
            raise RuntimeError(f"cannot open video writer for {path}")

    def write(self, frame: np.ndarray) -> None:
        self._w.write(frame)

    def close(self) -> None:
        self._w.release()
