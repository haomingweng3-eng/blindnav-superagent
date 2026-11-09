#!/usr/bin/env python3
"""Download a bounded real SANPO subset and verify the recorded file hashes.

This does not download model weights or fabricate missing inputs.  By default it
selects the first 12 recorded frames from each session in the existing inventory.
The subset is explicitly not the complete 481-frame frozen production replay.
Network access uses urllib's inherited proxy and TLS settings.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INVENTORY = ROOT / "docs/phase_reports/e3_input_audit_20261107/input_inventory.json"
PUBLIC_BASE = "https://storage.googleapis.com/gresearch/sanpo_dataset/v0/sanpo-real/"
FRAME_RE = re.compile(r"^([^/]+)/camera_chest/left/video_frames/(\d{6})\.png$")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def selected_inputs(inventory: dict, per_session: int, sessions: list[str] | None):
    if inventory.get("schema") != "e3-input-inventory/1" or inventory.get("missing"):
        raise ValueError("expected the complete, existing e3-input-inventory/1 source inventory")
    available: dict[str, list[int]] = {}
    for entry in inventory["files"]:
        path = PurePosixPath(entry["path"])
        if path.is_absolute() or ".." in path.parts:
            raise ValueError(f"unsafe relative input path: {path}")
        match = FRAME_RE.fullmatch(entry["path"])
        if match:
            available.setdefault(match[1], []).append(int(match[2]))
    chosen_sessions = sorted(sessions or available)
    if set(chosen_sessions) - set(available):
        raise ValueError("requested session is absent from the source inventory")
    selected = {sid: sorted(set(available[sid]))[:per_session] for sid in chosen_sessions}
    expected_paths: set[str] = set()
    for sid, frames in selected.items():
        expected_paths.update((f"{sid}/description.json", f"{sid}/camera_chest/fixed_camera_poses.csv"))
        for index in frames:
            expected_paths.update((
                f"{sid}/camera_chest/left/video_frames/{index:06d}.png",
                f"{sid}/camera_chest/left/segmentation_masks/{index:06d}.png",
                f"{sid}/camera_chest/left/depth_maps/{index:06d}.float16.gz",
            ))
    entries = [entry for entry in inventory["files"] if entry["path"] in expected_paths]
    if len(entries) != len(expected_paths):
        raise ValueError("subset is missing an RGB/depth/segmentation/metadata member")
    return selected, entries


def fetch_one(entry: dict, data_root: Path, verify_only: bool) -> dict:
    relative = entry["path"]
    destination = data_root / relative
    expected_size = int(entry["bytes"])
    expected_hash = entry["sha256"]
    url = PUBLIC_BASE + urllib.parse.quote(relative, safe="/-_.")
    result = {"path": relative, "source_url": url,
              "expected_bytes": expected_size, "expected_sha256": expected_hash}
    try:
        if destination.is_file() and destination.stat().st_size == expected_size:
            actual_hash = sha256_file(destination)
            if actual_hash == expected_hash:
                return {**result, "status": "verified_existing", "bytes": expected_size,
                        "sha256": actual_hash}
        if verify_only:
            raise ValueError("file is missing or differs from the frozen source inventory")
        destination.parent.mkdir(parents=True, exist_ok=True)
        partial = destination.with_name(destination.name + ".partial")
        digest = hashlib.sha256()
        size = 0
        request = urllib.request.Request(url, headers={"User-Agent": "BlindNav-bounded-replay/1"})
        with urllib.request.urlopen(request, timeout=60) as response, partial.open("wb") as output:
            for block in iter(lambda: response.read(1024 * 1024), b""):
                size += len(block)
                if size > expected_size:
                    raise ValueError("response exceeds the inventoried size")
                digest.update(block)
                output.write(block)
        actual_hash = digest.hexdigest()
        if size != expected_size or actual_hash != expected_hash:
            raise ValueError(f"integrity mismatch: bytes={size}, sha256={actual_hash}")
        os.replace(partial, destination)
        return {**result, "status": "downloaded_verified", "bytes": size,
                "sha256": actual_hash}
    except Exception as exc:
        return {**result, "status": "failed", "error": f"{type(exc).__name__}: {exc}"}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--inventory", type=Path, default=DEFAULT_INVENTORY)
    parser.add_argument("--per-session", type=int, default=12)
    parser.add_argument("--sessions", nargs="+", default=None)
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--manifest-out", type=Path, required=True)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args(argv)
    if args.per_session <= 0 or not 1 <= args.workers <= 12:
        parser.error("per-session must be positive and workers must be between 1 and 12")
    start = time.monotonic()
    inventory_bytes = args.inventory.read_bytes()
    inventory = json.loads(inventory_bytes)
    selected, entries = selected_inputs(inventory, args.per_session, args.sessions)
    total_bytes = sum(int(entry["bytes"]) for entry in entries)
    print(json.dumps({"selected_frames": sum(map(len, selected.values())),
                      "files": len(entries), "expected_bytes": total_bytes,
                      "sessions": selected}, sort_keys=True), flush=True)
    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        jobs = [pool.submit(fetch_one, entry, args.data_root, args.verify_only) for entry in entries]
        for completed in concurrent.futures.as_completed(jobs):
            result = completed.result()
            results.append(result)
            if result["status"] == "failed":
                print(json.dumps(result, sort_keys=True), flush=True)
            elif len(results) % 10 == 0 or len(results) == len(entries):
                print(json.dumps({"completed_files": len(results), "total_files": len(entries),
                                  "elapsed_seconds": round(time.monotonic() - start, 1)}, sort_keys=True), flush=True)
    results.sort(key=lambda row: row["path"])
    failed = [row for row in results if row["status"] == "failed"]
    try:
        commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        commit = None
    manifest = {
        "schema": "blindnav-public-slice-download/1",
        "source_commit": commit,
        "source_inventory": str(args.inventory.resolve()),
        "source_inventory_sha256": hashlib.sha256(inventory_bytes).hexdigest(),
        "data_root": str(args.data_root.resolve()),
        "source_frame_count": inventory["frame_keys"],
        "selected_frame_count": sum(map(len, selected.values())),
        "selected_frames": selected,
        "selection_rule": "first N inventoried RGB frames per selected session, with matching depth and segmentation",
        "complete_frozen_production_replay": False,
        "ground_truth_and_recorded_pose_available": True,
        "raw_imu_available": False,
        "expected_bytes": total_bytes,
        "verified_files": len(results) - len(failed),
        "failed_files": len(failed),
        "elapsed_seconds": round(time.monotonic() - start, 3),
        "files": results,
    }
    args.manifest_out.parent.mkdir(parents=True, exist_ok=True)
    args.manifest_out.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"verified_files": manifest["verified_files"], "failed_files": len(failed),
                      "manifest": str(args.manifest_out.resolve()),
                      "elapsed_seconds": manifest["elapsed_seconds"]}, sort_keys=True), flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
