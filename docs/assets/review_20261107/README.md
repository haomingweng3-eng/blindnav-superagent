# Review artifacts (2026-11-07)

This directory contains the machine-readable reports and videos needed to
review the current BlindNav state without access to the development computer.

## Last10m

* `last10m_scenarios_20261107/audit.json` — audit of the three real LSVT
  storefront scenarios, seven frames, and target-to-door relation labels.
* `last10m_scenarios_20261107/replay_eval.json` — separate target-lock,
  entrance-relation, ground-evidence, and motion-authorization gate results.

## Mobilio / SANPO

* `mobilio_four_way_20261106/` — A/B/C/D input ablation.
* `mobilio_harvard_reference_20261106/` — first Harvard reference replay.
* `mobilio_harvard_reference_fixed_20261107/` — corrected coordinate/height
  reference replay.
* `mobilio_harvard_fillgrid_fixed_20261107/` — true/false FillGrid replay.
* `mobilio_sanpo_ab_20261106/` — frozen SANPO A/B videos, metrics, transition
  matrix, IMU audit, and freeze manifest.
* `mobilio_fusion_split_20261106/` — risk-state / observation-hint split
  replay and video.

The Harvard/Mobilio artifacts are research evidence, not device or user-study
results. SANPO recorded pose is labeled as an offline VIO proxy, and UNKNOWN is
never counted as successful hazard recognition.
