#!/usr/bin/env python3
"""Offline inventory stub for the Week1 eval datasets.

Reads local dataset paths only: counts files, reports image resolution
distribution. No model inference, no network.

Usage:
    python dataset_inventory.py <path> [<path> ...]
"""
from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

IMG_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


def describe(root: Path) -> None:
    if not root.exists():
        print(f"{root}\n  MISSING")
        return

    files = [p for p in root.rglob("*") if p.is_file() and ".git" not in p.parts]
    images = [p for p in files if p.suffix.lower() in IMG_EXTS]
    print(f"{root}")
    print(f"  files={len(files)} images={len(images)} other={len(files) - len(images)}")
    if not images:
        return
    try:
        from PIL import Image
    except ImportError:
        print("  (PIL unavailable; skipping resolution histogram)")
        return

    sizes: Counter[tuple[int, int]] = Counter()
    for p in images:
        try:
            with Image.open(p) as im:
                sizes[im.size] += 1
        except Exception:
            sizes[("unreadable", 0)] += 1
    for (w, h), n in sizes.most_common(5):
        print(f"  {w}x{h}: {n}")


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    for arg in argv[1:]:
        describe(Path(arg))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
