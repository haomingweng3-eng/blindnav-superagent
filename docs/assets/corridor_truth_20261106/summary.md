# Four-arm corridor truth-loss diagnostic — machine summary

Generated: 2026-11-06 00:57:14
Frames: 481 across {'-PqSDmiEe2pXjmYHgxh4YEBsj0T5LU10': 121, 'M5UIabsltWCYg1D9LCXLoNPO3bMUo_5-': 184, 'UGNwBEZMxhzlzXVyl8XUfflcvjGwdpA5': 176} SANPO-Real sessions; v5 checkpoint: `/Users/mima0000/blindnav-real-eval/models/segformer_sanpo_b0_v5.pt`
Evidence level: DESKTOP_REAL_DATA (offline, CPU/MPS); not device-verified, not user-tested.

Metric definitions and the full failure analysis are in `docs/CORRIDOR_TRUTH_LOSS_20261106.md`.

## Geometry input: production

| arm | frames | Road-as-Sidewalk | curb recall | coverage>=min | UNKNOWN | wrong corrections |
|---|---:|---:|---:|---:|---:|---:|
| A | 481 | 0/52197 (0.0%) | 0/35199 (0.0%) | 394/481 (81.9%) | 18.3% | 1/160 |
| B | 481 | 26944/52197 (51.6%) | 0/35199 (0.0%) | 375/481 (78.0%) | 22.2% | 1/173 |
| C | 481 | 0/52197 (0.0%) | 35199/35199 (100.0%) | 377/481 (78.4%) | 22.0% | 1/152 |
| D | 481 | 19602/52197 (37.6%) | 28069/35199 (79.7%) | 366/481 (76.1%) | 23.9% | 2/152 |

## Geometry input: oracle-geometry

| arm | frames | Road-as-Sidewalk | curb recall | coverage>=min | UNKNOWN | wrong corrections |
|---|---:|---:|---:|---:|---:|---:|
| A | 481 | 0/52197 (0.0%) | 0/35199 (0.0%) | 470/481 (97.7%) | 2.3% | 2/201 |
| B | 481 | 26944/52197 (51.6%) | 0/35199 (0.0%) | 468/481 (97.3%) | 2.7% | 2/197 |
| C | 481 | 0/52197 (0.0%) | 35199/35199 (100.0%) | 463/481 (96.3%) | 3.7% | 2/303 |
| D | 481 | 17808/52197 (34.1%) | 33456/35199 (95.0%) | 464/481 (96.5%) | 3.5% | 3/283 |


wrong corrections column: `rotation->LEFT/RIGHT` / `band-driven LEFT/RIGHT`.

## First-layer attribution (production geometry)

| arm | failing frames | segmentation | geometry | motion | state machine | failure events |
|---|---:|---:|---:|---:|---:|---|
| A | 102 | 0 | 53 | 1 | 48 | curb_in_path_ignored=24, rotation_wrong_correction=1, unknown_when_gt_walkable=77 |
| B | 114 | 3 | 59 | 1 | 51 | curb_in_path_ignored=15, road_in_path_walkable=2, rotation_wrong_correction=1, unknown_when_gt_walkable=96 |
| C | 125 | 0 | 65 | 1 | 59 | curb_in_path_ignored=29, rotation_wrong_correction=1, unknown_when_gt_walkable=95 |
| D | 122 | 3 | 64 | 2 | 53 | curb_in_path_ignored=14, road_in_path_walkable=2, rotation_wrong_correction=2, unknown_when_gt_walkable=104 |

