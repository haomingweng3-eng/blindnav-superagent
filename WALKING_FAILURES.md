# Walking Real-Data Failures — 2026-11-03

Dataset: SANPO-Real, 3 test sessions, 45 frames with semantic masks.
All 45 frames are **W2 (sidewalk without tactile paving)**; 34 also W3 (wide),
30 also W6 (pedestrians present). No W1/W5/W8/W9 frames in this subset.

## Baseline numbers

| Signal | Result |
|---|---|
| GT walkable available | 45/45 |
| Current `BlindPathDetector` valid corridor | **0/45 = 0%** |
| SegFormer-B0 Cityscapes strict corridor | 22.2% |
| SegFormer-B0 inclusive (road+sidewalk+terrain) | 100% |
| SegFormer-B0 sidewalk pixel recall | 6.7% |
| Sidewalk predicted as road | **90.1%** |
| Centerline error median / P90 | 0.614 / 0.700 (normalized) |
| Temporal jitter (B0, per session) | 0.33 / 0.16 |
| SegFormer-B2 strict corridor | 11.1% |
| SegFormer-B2 sidewalk recall | 3.5% |

The inclusive 100% is not a win: the model finds “walkable ground” but cannot
separate sidewalk from road. Treating road as sidewalk would be unsafe at curbs
and intersections.

## Failure buckets (45 frames)

| Bucket | Count | Meaning |
|---|---:|---|
| NO_WALKABLE_MASK | 45 | BlindPath produces no valid corridor |
| SEGMENTATION_FAILURE | 35 | SegFormer strict mask absent where GT walkable exists |
| SIDEWALK_AS_ROAD | 45 | Sidewalk pixels classified as road >50% |
| CENTERLINE_FAILURE | 28 | Predicted centerline off by >0.25 normalized |
| PEDESTRIAN_OCCLUSION | 20 | Pedestrians present and strict mask failed |
| MOTION_BLUR | 0 | No low-variance frames in this subset |
| LIGHTING | 0 | No extreme brightness frames |

## Root cause

`BlindPathDetector` is a tactile-paving/crosswalk detector. It has no class or
training signal for “ordinary walkable sidewalk”. Off-the-shelf Cityscapes
SegFormer has the opposite domain problem: brick/patterned sidewalks are read as
road. Threshold tuning cannot fix a label/domain gap.

## Decision

`NEED_DECISION` — do not ship a production corridor based on inclusive road
classification. The next P0 experiment should compare a Mapillary-Vistas-trained
or SANPO-fine-tuned walkable model against the current tactile detector, with
curb/road false-positive as a required metric. No production walking change was
made in this round.

## Evidence level

`DESKTOP_REAL_DATA` for SANPO segmentation; `NOT_TESTED` for device and for W1
tactile frames.
