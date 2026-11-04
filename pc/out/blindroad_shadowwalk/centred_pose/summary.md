# Blind-road ShadowWalk offline eval

Real GT walkable masks are back-projected with a flat-ground depth plane and the corridor
pose. There is no depth network and no real pose here; the pose is synthetic unless a
pose CSV is supplied. The stub emits guidance only, never a crossing/go command.

## Inputs

- Dataset: `/Users/mima0000/blindnav-app/pc/data/eval/blind_road_crosswalk/extracted/Blind road and crosswalk dataset`
- Splits: test, train, val
- Frames: 200 (test=25, train=150, val=25)
- Corridor: `straight60` (length 60.0 m, width 2.5 m)
- Synthetic offsets: `0` m, step 0.5 m; pose source: synthetic

## Command distribution

| command | count | percent |
|---|---:|---:|
| STRAIGHT | 87 | 43.50% |
| LEFT | 36 | 18.00% |
| RIGHT | 61 | 30.50% |
| STOP | 0 | 0.00% |
| UNKNOWN | 16 | 8.00% |

Per split:

| split | frames | STRAIGHT | LEFT | RIGHT | STOP | UNKNOWN |
|---|---:|---:|---:|---:|---:|---:|
| test | 25 | 10 | 5 | 8 | 0 | 2 |
| train | 150 | 65 | 26 | 47 | 0 | 12 |
| val | 25 | 12 | 5 | 6 | 0 | 2 |

## Clearly-failed frames

Criteria are stated in `summary.json` (`failures.criteria`). `intended_off_corridor_stop`
is **not** a failure: the synthetic schedule deliberately places the walker at 1.2 m,
outside the 2.5 m-wide corridor, and the stub is supposed to stop.

| criterion | count |
|---|---:|
| STOP on open walkable (`stop_on_open_walkable`) | **0** |
| UNKNOWN when mask ok (`unknown_when_mask_ok`) | **1** |
| spurious turn while centred (`spurious_turn`) | 0 |
| LEFT/RIGHT oscillation on consecutive ids | **3** |
| intended off-corridor STOP (not a failure) | 0 |
| yaw-too-large STOP | 0 |

STOP reasons: `{}`; UNKNOWN reasons: `{'corridor_unseen': 11, 'walkable_unseen': 5}`.

Failure frames:

| split | frame | hint | reason | offset (m) | yaw (deg) | raw walkable frac |
|---|---|---|---|---:|---:|---:|
| train | 35 | UNKNOWN | corridor_unseen | 0.00 | 0.0 | 0.487 |

Oscillation base: 113 consecutive-id pairs (train=112, val=1); 3 LEFT/RIGHT flips on those pairs; 3 with a stable pose.

## Mask-ok threshold sensitivity

| mask_ok fraction | mask-ok frames | UNKNOWN when mask ok | STOP on open walkable |
|---:|---:|---:|---:|
| 0.15 | 151 | 3 | 0 |
| 0.2 | 131 | 1 | 0 |
| 0.25 | 111 | 1 | 0 |
| 0.3 | 90 | 1 | 0 |
| 0.35 | 62 | 1 | 0 |
| 0.4 | 35 | 1 | 0 |

## Caveats / blockers

- No depth model: metric depth is a flat-ground plane. Absolute ranges and the 1.5-6 m
  corridor band are therefore geometric assumptions, not measurements.
- No real ego-motion: poses are synthetic. Consecutive frames are paired with a synthetic
  step, so the command stream is not a real trajectory.
- The dataset images are not certified as video: train ids are mostly contiguous, val/test
  are random samples. Oscillation is therefore only counted on `id_gap == 1` pairs.
- `mask_ok` is a coarse image-space ROI; walkable pixels outside the predefined corridor
  make UNKNOWN defensible, so the failure count is a lower bound.
- LEFT vs RIGHT skew reflects the visible walkable-band offset in the dataset, not a
  heading controller.
