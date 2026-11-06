#!/usr/bin/env python3
"""Audit SANPO-Real sessions for declared IMU hardware and observed IMU files."""
from __future__ import annotations

import argparse, json, os
from pathlib import Path

IMU_TOKENS = ("imu", "gyro", "accel", "accelerometer", "sensor")

def audit(root: str) -> dict:
    base = Path(root)
    sessions = []
    for session in sorted(p for p in base.iterdir() if p.is_dir()):
        files = [str(p.relative_to(session)) for p in session.rglob("*") if p.is_file()]
        imu_files = [p for p in files if any(t in p.lower() for t in IMU_TOKENS)]
        declared = []
        desc_path = session / "description.json"
        if desc_path.exists():
            desc = json.loads(desc_path.read_text())
            for camera in desc.get("session_camera_details", []):
                for key in ("accelerometer_params", "gyroscope_params", "magnetometer_params", "barometer_params"):
                    value = camera.get(key, {})
                    if value.get("available") is True:
                        declared.append({"serial": camera.get("serial"), "sensor": key, "params": value})
        sessions.append({"session": session.name, "declared_sensors": declared,
                         "imu_observation_files": sorted(imu_files),
                         "has_fixed_camera_poses": (session / "camera_chest/fixed_camera_poses.csv").exists()})
    return {"root": str(base.resolve()), "sessions": sessions,
            "raw_imu_observations_present": any(s["imu_observation_files"] for s in sessions)}

def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-root", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    result = audit(args.data_root)
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    Path(args.out).write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
