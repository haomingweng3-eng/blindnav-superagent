# Blind-road ShadowWalk summary

Two offline runs on the same 200 real GT walkable masks (train+val+test):

- `varied_pose/` -- synthetic offsets `0, 0.4, -0.4, 1.2` m, which deliberately puts the
  walker outside the 2.5 m corridor every fourth frame.
- `centred_pose/` -- offset `0` m, the stable-pose control that isolates mask geometry from
  the synthetic trajectory.

Both use `straight60`, flat-ground depth and synthetic ego-motion. See the per-run
`summary.md` files for full caveats and CSVs.

## Command distribution

| command | varied pose | centred pose |
|---|---:|---:|
| STRAIGHT | 55 (27.50%) | 87 (43.50%) |
| LEFT | 31 (15.50%) | 36 (18.00%) |
| RIGHT | 53 (26.50%) | 61 (30.50%) |
| STOP | 50 (25.00%) | 0 (0.00%) |
| UNKNOWN | 11 (5.50%) | 16 (8.00%) |

Frames: 200 (varied), 200 (centred).

## Clearly-failed frames

| criterion | varied pose | centred pose |
|---|---:|---:|
| STOP on open walkable | 0 | 0 |
| UNKNOWN when mask ok | 0 | 1 |
| spurious turn while centred | 0 | 0 |
| intended off-corridor STOP (not a failure) | 50 | 0 |
| yaw-too-large STOP | 0 | 0 |
| LEFT/RIGHT oscillation on consecutive ids | 0 / 113 pairs | 3 / 113 pairs |

STOP reasons -- varied: `{'off_corridor': 50}`; centred: `{}`.

UNKNOWN reasons -- varied: `{'corridor_unseen': 6, 'walkable_unseen': 5}`; centred: `{'corridor_unseen': 11, 'walkable_unseen': 5}`.

`intended_off_corridor_stop` is expected: the varied schedule places the walker at 1.2 m,
beyond the 0.8 x half-width stop threshold. It is not counted as a failure.

## Key caveats

- No depth model: metric depth is a flat-ground plane, so the 1.5-6 m band is geometric.
- No real ego-motion: both runs use synthetic poses, not PDR/odometry.
- The blind-road images are not a certified continuous video; train ids are mostly
  contiguous, val/test are random, so oscillation only counts `id_gap == 1` pairs.
- `mask_ok` is a coarse image-space ROI; walkable pixels outside the corridor make
  UNKNOWN defensible, so failure counts are a lower bound.

Full reports: [varied_pose/summary.md](varied_pose/summary.md), [centred_pose/summary.md](centred_pose/summary.md).
