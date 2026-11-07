# P0 data integrity and reproducibility audit

Status: **P0_PARTIAL_NOT_PASSED**.

This phase records what is locally available without downloading large data or
installing new frameworks. It confirms the frozen SANPO replay inventory,
records the three-session IMU gap, indexes existing evidence, and captures the
current machine constraints. It does not start VIO, Basalt, or a new Mobilio
run.

The next gate is P1 only after an independent Linux/WSL experiment environment
and a one-sequence public VIO dataset have verified source, license, checksum,
calibration and ground-truth availability. P0.1 is complete for the three
SANPO prefixes; P0.2 and P0.3 remain partial because the independent environment
and exact TUM-VI room1 file checksum are not yet available.
