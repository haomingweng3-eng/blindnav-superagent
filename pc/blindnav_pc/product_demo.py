"""One supervised demo of the product path.  No dataset and no model download.

``python -m blindnav_pc demo --out out/product_demo``

The walk is synthetic and stays inside the locked rules:

* ShadowWalk virtual corridor: steer back toward the centre, or stop when the
  corridor is unseen or the walker has left it.  It does not localise or drive.
* Last 10 m: at most three door proposals.  A missing distance refuses.
* Traffic light: red / green / uncertain only.  ``auto_go`` stays false.

Pure monocular use here is a low-speed demo with a safety officer beside the
walker.  W1 curb recall and near-field hazard recall are not scored.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from typing import Optional, Sequence

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from .last10m import MAX_CANDIDATES, DoorCandidate, propose_doors
from .shadowwalk import (LEFT, RIGHT, STOP, STRAIGHT, UNKNOWN, HINT_TEXT,
                         ShadowWalkConfig, ShadowWalkTracker, get_corridor)
from .traffic_light import (GREEN, RED, STATES, TEXT, UNCERTAIN, TrafficLightFilter,
                            TrafficLightObservation)

WIDTH, HEIGHT = 1280, 720
FPS_DEFAULT = 4
HOLD_DEFAULT = 4
_FONTS = (
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf",
)
_BAND_OFFSETS = tuple(float(v) for v in np.linspace(-1.0, 1.0, 11))


def _band(corridor, s_walker: float, cfg: ShadowWalkConfig) -> np.ndarray:
    offsets = np.asarray(_BAND_OFFSETS, dtype=np.float64)
    chunks = []
    for s in np.arange(s_walker + cfg.near_m, s_walker + cfg.far_m + 1e-9, 0.2):
        x, z, rx, rz = corridor.point_at(float(s))
        chunks.append(np.column_stack([x + rx * offsets, z + rz * offsets]))
    return np.vstack(chunks) if chunks else np.zeros((0, 2))


def _corridor_frames(cfg: Optional[ShadowWalkConfig] = None) -> list:
    """Scripted poses.  The hint text comes from ShadowWalkTracker, not from this table."""
    config = cfg or ShadowWalkConfig()
    corridor = get_corridor("straight60")
    tracker = ShadowWalkTracker(corridor, config)
    script = (
        (2.0, 0.0, "band", "贴着走廊中线走"),
        (3.0, 0.0, "band", "贴着走廊中线走"),
        (4.0, 0.55, "band", "人偏到走廊右侧"),
        (5.0, 0.70, "band", "人还在右侧，修正还在"),
        (6.0, 0.05, "band", "回到中线附近"),
        (7.0, 0.0, "none", "前方可行走地面不够"),
        (8.0, 1.2, "band", "人已经离开走廊"),
    )
    rows = []
    for index, (s_m, offset_m, points, note) in enumerate(script):
        pose = corridor.pose_at(s_m, offset_m=offset_m)
        cloud = _band(corridor, s_m, config) if points == "band" else np.zeros((0, 2))
        hint = tracker.update(cloud, pose)
        if hint.hint not in HINT_TEXT:
            raise RuntimeError(f"corridor hint left the allowed set: {hint.hint!r}")
        rows.append({
            "index": index,
            "phase": "corridor",
            "note": note,
            "hint": hint.hint,
            "text": hint.text,
            "reason": hint.reason,
            "offset_m": None if not math.isfinite(hint.offset_m) else round(float(hint.offset_m), 4),
            "coverage": round(float(hint.coverage), 4),
            "n_points": int(hint.n_points),
            "auto_go": False,
        })
    return rows


def _door(distance, cx: float) -> DoorCandidate:
    return DoorCandidate(0, cx, 0.8, 0.1, 0.2, confidence=0.9, distance_m=distance)


def _door_frames(start: int) -> list:
    scenes = (
        ("门口看到多扇门", [_door(d, 0.2 + 0.1 * i) for i, d in enumerate((8.0, 3.0, 6.0, 2.0, 9.0))]),
        ("有一扇门没有距离", [_door(4.0, 0.45), _door(None, 0.62)]),
    )
    rows = []
    for note, doors in scenes:
        proposal = propose_doors(doors)
        if len(proposal.candidates) > MAX_CANDIDATES:
            raise RuntimeError("door proposal exceeded 3")
        distances = [None if door.distance_m is None else round(float(door.distance_m), 3)
                     for door in proposal.candidates]
        if proposal.refused:
            text = f"门候选拒答：{proposal.reason}"
        else:
            shown = "、".join(f"{d:.1f} m" for d in distances)
            text = f"最后 10 米提议 {len(proposal.candidates)} 个门：{shown}"
        rows.append({
            "index": start + len(rows),
            "phase": "doors",
            "note": note,
            "hint": None,
            "text": text,
            "reason": proposal.reason,
            "refused": bool(proposal.refused),
            "candidate_count": len(proposal.candidates),
            "distances_m": distances,
            "auto_go": False,
        })
    return rows


def _light_frames(start: int) -> list:
    filt = TrafficLightFilter(min_confirm_frames=3)
    observations = (
        ("看到红灯", RED),
        ("红灯还在", RED),
        ("红灯连续确认", RED),
        ("灯被挡住", UNCERTAIN),
        ("看到绿灯", GREEN),
        ("绿灯还在", GREEN),
        ("绿灯连续确认", GREEN),
    )
    rows = []
    for note, state in observations:
        decision = filt.update(TrafficLightObservation(state))
        if decision.auto_go or decision.state not in STATES:
            raise RuntimeError("traffic light left the three-state rule")
        rows.append({
            "index": start + len(rows),
            "phase": "traffic",
            "note": note,
            "hint": None,
            "text": decision.text,
            "reason": decision.reason,
            "state": decision.state,
            "stable": bool(decision.stable),
            "observed": state,
            "auto_go": False,
        })
    return rows


def script_frames() -> list:
    """Replay corridor, doors, then the lamp.  Every line is produced by the existing stubs."""
    rows = _corridor_frames()
    rows.extend(_door_frames(len(rows)))
    rows.extend(_light_frames(len(rows)))
    for row in rows:
        blob = json.dumps(row, ensure_ascii=False)
        if "可以过街" in blob or "CROSS" in blob.upper():
            raise RuntimeError("demo text granted a crossing")
    return rows


def _load_font(size: int) -> ImageFont.ImageFont:
    for path in _FONTS:
        if os.path.isfile(path):
            return ImageFont.truetype(path, size=size)
    return ImageFont.load_default()


def _wrap(text: str, font: ImageFont.ImageFont, width: int) -> list:
    lines, current = [], ""
    for ch in text:
        trial = current + ch
        if font.getlength(trial) <= width or not current:
            current = trial
        else:
            lines.append(current)
            current = ch
    if current:
        lines.append(current)
    return lines


def _paint(row: dict, font_title, font_body) -> np.ndarray:
    image = Image.new("RGB", (WIDTH, HEIGHT), (22, 26, 31))
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, WIDTH, 64), fill=(32, 40, 48))
    draw.text((32, 16), "BlindNav 受控演示  ·  安全员在旁  ·  低速  ·  提示不是控制",
              font=font_title, fill=(232, 226, 210))

    phase = row["phase"]
    if phase == "corridor":
        _paint_corridor(draw, row)
    elif phase == "doors":
        _paint_doors(draw, row)
    else:
        _paint_light(draw, row)

    x0 = 700
    draw.text((x0, 96), row["note"], font=font_title, fill=(244, 236, 214))
    y = 156
    for line in _wrap(row["text"], font_body, WIDTH - x0 - 40):
        draw.text((x0, y), line, font=font_body, fill=(236, 236, 236))
        y += 40
    detail = f"阶段 {phase}    原因 {row['reason']}    auto_go false"
    draw.text((x0, HEIGHT - 72), detail, font=font_body, fill=(168, 176, 184))
    rgb = np.asarray(image)
    return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)


def _paint_corridor(draw: ImageDraw.ImageDraw, row: dict) -> None:
    left, right, top, bottom = 180, 520, 120, 640
    draw.rectangle((left, top, right, bottom), fill=(58, 74, 58))
    mid = (left + right) / 2.0
    draw.line((mid, top, mid, bottom), fill=(210, 198, 140), width=2)
    offset = float(row["offset_m"] or 0.0)
    half = (right - left) / 2.0
    x = int(mid + max(-1.0, min(1.0, offset / 1.25)) * (half - 24))
    y = 180 + int(70 * (row["index"] % 8))
    color = {"STRAIGHT": (180, 198, 150), "LEFT": (150, 186, 220), "RIGHT": (150, 186, 220),
             "STOP": (196, 96, 96), "UNKNOWN": (210, 170, 90)}.get(row["hint"], (200, 200, 200))
    draw.ellipse((x - 18, y - 18, x + 18, y + 18), fill=color)
    draw.text((48, 96), "虚拟走廊", font=_load_font(28), fill=(210, 206, 190))


def _paint_doors(draw: ImageDraw.ImageDraw, row: dict) -> None:
    draw.text((48, 96), "最后 10 米", font=_load_font(28), fill=(210, 206, 190))
    if row["refused"]:
        draw.rectangle((140, 220, 560, 420), outline=(196, 96, 96), width=4)
        draw.text((180, 290), "拒答", font=_load_font(64), fill=(220, 120, 110))
        return
    for i, distance in enumerate(row["distances_m"]):
        x = 150 + i * 150
        draw.rectangle((x, 200, x + 110, 460), outline=(214, 206, 180), width=3)
        draw.text((x + 18, 300), f"{distance:.0f}m", font=_load_font(32), fill=(236, 230, 210))


def _paint_light(draw: ImageDraw.ImageDraw, row: dict) -> None:
    draw.text((48, 96), "灯色", font=_load_font(28), fill=(210, 206, 190))
    colors = {RED: (196, 72, 72), GREEN: (72, 150, 96), UNCERTAIN: (196, 154, 64)}
    draw.ellipse((250, 200, 470, 420), fill=colors[row["state"]])
    label = {RED: "红", GREEN: "绿", UNCERTAIN: "不确定"}[row["state"]]
    draw.text((314, 280), label, font=_load_font(48), fill=(28, 28, 28))


def _summary(rows: Sequence[dict]) -> str:
    corridor = [row for row in rows if row["phase"] == "corridor"]
    doors = [row for row in rows if row["phase"] == "doors"]
    lights = [row for row in rows if row["phase"] == "traffic"]
    lines = [
        "# BlindNav 受控演示",
        "",
        "这是一条事先定好的低速路线。安全员在一臂距离内，使用者仍拿盲杖。",
        "纯单目只作这次受控演示。系统给提示，不驱动人，也不代替判断。",
        "本演示不覆盖 W1 路缘召回和近场危险召回，这两条门槛尚未在这里通过。",
        "",
        "## 走廊",
        "",
    ]
    for row in corridor:
        lines.append(f"- {row['note']}：{row['text']}（{row['hint']} / {row['reason']}）")
    lines.extend(["", "## 最后 10 米", ""])
    for row in doors:
        lines.append(f"- {row['note']}：{row['text']}")
    lines.extend(["", "## 灯", ""])
    for row in lights:
        lines.append(f"- {row['note']}：{row['text']}（{row['state']}，稳定={row['stable']}）")
    lines.extend([
        "",
        "灯只有红、绿、不确定。绿灯只表示灯色，过街要人自己确认左右。",
        "auto_go 为 false。",
        "",
    ])
    return "\n".join(lines)


def run_demo(out: str, fps: int = FPS_DEFAULT, hold: int = HOLD_DEFAULT) -> dict:
    """Write ``demo.json``, Chinese ``summary.md``, key frames, and ``demo.avi``."""
    if fps < 1 or hold < 1:
        raise ValueError("fps and hold must be >= 1")
    rows = script_frames()
    os.makedirs(out, exist_ok=True)
    frame_dir = os.path.join(out, "frames")
    os.makedirs(frame_dir, exist_ok=True)
    font_title, font_body = _load_font(28), _load_font(26)
    video_path = os.path.join(out, "demo.avi")
    writer = cv2.VideoWriter(video_path, cv2.VideoWriter_fourcc(*"MJPG"), float(fps), (WIDTH, HEIGHT))
    if not writer.isOpened():
        raise RuntimeError(f"cannot open video writer for {video_path}")
    saved = set()
    for row in rows:
        bgr = _paint(row, font_title, font_body)
        key = os.path.join(frame_dir, f"{row['index']:02d}_{row['phase']}.jpg")
        cv2.imwrite(key, bgr)
        saved.add(row["phase"])
        for _ in range(hold):
            writer.write(bgr)
    writer.release()
    report = {
        "title": "BlindNav 受控演示",
        "corridor": "straight60",
        "pure_monocular": "supervised_low_speed_constrained_demo",
        "auto_go": False,
        "w1_curb_recall_gate": "not_claimed",
        "w1_near_field_recall_gate": "not_claimed",
        "n_frames": len(rows),
        "video": os.path.abspath(video_path),
        "video_frames": len(rows) * hold,
        "fps": fps,
        "phases_present": sorted(saved),
        "frames": rows,
    }
    with open(os.path.join(out, "demo.json"), "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    with open(os.path.join(out, "summary.md"), "w", encoding="utf-8") as fh:
        fh.write(_summary(rows))
    if not os.path.isfile(video_path) or os.path.getsize(video_path) < 1000:
        raise RuntimeError("demo video was not written")
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="BlindNav 受控演示：走廊、最后 10 米、三态灯。不下载数据。")
    parser.add_argument("--out", default="out/product_demo", help="写入 demo.json、summary.md、demo.avi 的目录")
    parser.add_argument("--fps", type=int, default=FPS_DEFAULT)
    parser.add_argument("--hold", type=int, default=HOLD_DEFAULT, help="每个逻辑帧在视频里重复的次数")
    args = parser.parse_args(argv)
    try:
        report = run_demo(args.out, fps=args.fps, hold=args.hold)
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"演示失败: {exc}", file=sys.stderr)
        return 2
    hints = [row["hint"] for row in report["frames"] if row["phase"] == "corridor"]
    print(f"frames={report['n_frames']} corridor={hints} video={report['video']} out={args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
