# Harvard Mobilio reference versus BlindNav implementation

## Source and license

The isolated reference ports are based on Harvard-Slade-Lab/Mobilio
`scripts/Vision.cs` and `scripts/DepthImage.cs` from the MIT-licensed upstream
repository: <https://github.com/Harvard-Slade-Lab/Mobilio>.

The ports are kept separate from BlindNav's safety policy and Android code.
They consume already prepared world-frame inputs and do not reproduce
ARFoundation, ARKit/ARCore, Sentis, or the original segmentation model.

## Algorithm differences

| area | Harvard reference arm | BlindNav Mobilio-inspired arm |
|---|---|---|
| surface grid | 0.20 m boolean world cells, `FillGrid`-style updates, 5 m minimum path, two skipped cells, ±90°/1° ray search, 30° target disparity | 0.20 m evidence cells, role priority, decay/staleness, explicit UNKNOWN and fully-observed route selection |
| surface classes | upstream `LaxWalkable` behavior is represented only in the offline reference adapter | BlindNav role map keeps road/curb policy separate from safe motion authorization |
| occupancy grid | 0.10 m point-count grid, 0.9 visible-cell decay, 1.5 m body-path test, 4 m alternating local ray search | height-layered occupancy evidence, visibility/free cells, temporal confirmation, explicit CLEAR/OBSERVE/UNKNOWN/BLOCKED states |
| risk and motion | upstream returns obstacle direction and distance; it has no BlindNav authorization contract | `risk_state`, `observation_hint`, and `authorized_motion`; only CLEAR authorizes motion |
| inputs | expects AR world pose and AR environment depth/confidence in the original app | SANPO recorded pose proxy, GT or DAV2 depth, v5 or GT roles, and explicit evidence labels |

## Four input ablation

All conditions use the same 481 SANPO-Real frame keys and recorded pose.

| condition | segmentation | depth | Mobilio-inspired authorized motion | UNKNOWN | any-danger miss |
|---|---|---|---:|---:|---:|
| A | GT | SANPO reference | 29/481 (6.0%) | 37/481 (7.7%) | 10/295 (3.4%) |
| B | v5 | SANPO reference | 15/481 (3.1%) | 61/481 (12.7%) | 8/295 (2.7%) |
| C | GT | DAV2 | 57/481 (11.9%) | 276/481 (57.4%) | 18/295 (6.1%) |
| D | v5 | DAV2 | 49/481 (10.2%) | 275/481 (57.2%) | 17/295 (5.8%) |

The Harvard reference arms were run on the same frame set:

| condition | reference input | authorized motion | UNKNOWN | any-danger miss |
|---|---|---:|---:|---:|
| `harvard_A` | GT + SANPO reference depth | 23/481 (4.8%) | 458/481 (95.2%) | 6/295 (2.0%) |
| `harvard_D` | v5 + DAV2 | 80/481 (16.6%) | 401/481 (83.4%) | 40/295 (13.6%) |

These are desktop replay measurements. UNKNOWN is never counted as a correct
danger recognition, and no result is device-verified or user-tested.

## Input substitution limits

The local SANPO sessions contain corrected `fixed_camera_poses.csv` files and
declare IMU hardware/calibration in `description.json`, but the local and
public GCS object listings for these session prefixes contain no IMU observation
objects. Real RGB+IMU VIO is therefore NOT_EVALUABLE for this checkout. The
recorded pose remains explicitly labeled as an offline VIO proxy.

## Reproduction

```bash
cd pc
.venv/bin/pytest -q
.venv/bin/python tools/mobilio_sanpo_ab.py \
  --conditions A_gt_gt B_v5_gt C_gt_dav2 D_v5_dav2 \
  --v5-checkpoint /path/to/segformer_sanpo_b0_v5.pt \
  --out out/mobilio_four_way_20261106
.venv/bin/python tools/mobilio_sanpo_ab.py \
  --conditions harvard_A harvard_D \
  --v5-checkpoint /path/to/segformer_sanpo_b0_v5.pt \
  --out out/mobilio_harvard_reference_20261106
```
