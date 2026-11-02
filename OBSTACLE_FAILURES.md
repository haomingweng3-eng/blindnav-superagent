# Obstacle Real-Data Failures — 2026-11-03

## Data

- UrbanEgo run7 (pedestrian-heavy), 958 one-second frames. GT is
  YOLO11x+BoT-SORT, a model proxy, not human labels.
- BLV-Road-Nav-Accessibility, 1795 real frames, 90 categories, frame-level
  object presence only (no boxes).
- UrbanEgo 375–400 s clip at 10 fps for temporal stop/recovery.

## UrbanEgo threshold sweep

| Config (conf/need/person) | Object recall | Object precision | Frame false-positive rate |
|---|---:|---:|---:|
| Current 0.30 / 0.25 / 0.65,0.72 | 36.4% | 32.1% | 6.8% |
| Recall 0.25 / 0.20 / 0.60,0.70 | **38.4%** | 27.7% | 8.7% |
| Precision 0.35 / 0.30 / 0.70,0.75 | 32.3% | **34.4%** | 5.4% |

No threshold reaches the 90% blocking-recall target. Changing to the recall
config buys +2 points of recall and costs precision/FP. This is not an
acceptable production trade.

## Temporal clip (375–400 s)

- GT blocking frames: 44
- Model STOP: true positive 43, false positive 30, false negative 1
- Time-to-STOP: 0 ms for all GT segments
- Recovery: 100–4300 ms; one segment exceeds the 2 s target
- STOP flaps: 6

## BLV object coverage (COCO classes only)

Macro recall over 9 covered categories: **41.8%**.

| Category | Recall |
|---|---:|
| Person | 96.4% |
| Car | 78.1% |
| Bus | 76.3% |
| Bicycle | 31.1% |
| Bench | 13.2% |
| Chair | 0% |
| Table | 0% |

23 BLV-important categories have no COCO class at all, including: curb, sloped
curb, stairs, uneven stairs, doorway, pillar, pole, barrier post, fence, railing,
trash bins, wheelchair, white cane, tactile paving, foldout sign, raised
entryway, overhanging tree branches, sidewalk pits, wet surface, puddle, snow,
closed sidewalk, clear ice.

## Failure buckets

| Bucket | Evidence |
|---|---|
| DETECTOR_MISS | 63 object misses on UrbanEgo proxy |
| FALSE_CORRIDOR_OVERLAP | 76 object false positives |
| UNKNOWN_OBJECT_MISS | No class-independent occupancy detector; architecture gap |
| HIGH_OBSTACLE_MISS | No high-obstacle GT in local data; not measured |
| GROUND_HAZARD_MISS | No ground-hazard GT in local data; not measured |
| TEMPORAL_TOO_LONG | One 4.3 s recovery in the real clip |
| DYNAMIC_TOO_LATE | 0 ms in the sampled clip; not enough events to claim |
| STATIC_FALSE_STOP | Fixed in synthetic regression; not measured on real static video |

## Decision

`NEED_DECISION` — keep the current detector. Threshold tuning cannot reach 90%
recall, and a class-independent corridor-occupancy model is required for
unknown/high/ground objects. No production obstacle change was made in this
round. Synthetic 0/5 false-STOP result is not used to claim real-world safety.

Evidence level: `DESKTOP_REAL_DATA`; GT is model-generated for UrbanEgo and
presence-only for BLV.
