# BlindNav Real-Data Round 1 — 2026-11-03

## 1 Dataset Inventory

See `DATASET_INVENTORY.md`. Local real data: SANPO-Real (45 masked frames),
UrbanEgo run7 (958 one-second frames + 30 s clip), BLV-Road-Nav-Accessibility
(1795 frames with presence labels). GuideDog is HF-gated; Signboard/DeepDoors2
data is on Google Drive and was not downloaded. No fake metrics for absent GT.

## 2 Walking Baseline

| Signal | Result |
|---|---|
| SANPO frames | 45, all W2 (no tactile); 34 wide; 30 with pedestrians |
| GT walkable available | 45/45 |
| Current BlindPath valid corridor | **0/45 = 0%** |
| SegFormer-B0 strict sidewalk/terrain corridor | 22.2% |
| SegFormer-B0 inclusive road+sidewalk+terrain | 100% |
| Sidewalk pixel recall | 6.7% |
| Sidewalk classified as road | 90.1% |
| SegFormer-B2 strict corridor | 11.1% |
| Centerline error median / P90 | 0.614 / 0.700 |
| Temporal jitter | 0.33 / 0.16 per session |

`with tactile`: not present in this SANPO subset; existing app tests are
synthetic. `without tactile`: 0% valid corridor from the current production
detector. This is the P0 blocker.

## 3 Walking Failures

`WALKING_FAILURES.md`. Buckets: NO_WALKABLE_MASK 45, SEGMENTATION_FAILURE 35,
SIDEWALK_AS_ROAD 45, CENTERLINE_FAILURE 28, PEDESTRIAN_OCCLUSION 20. Root cause
is a label/domain gap, not a threshold: BlindPath only knows tactile paving;
Cityscapes SegFormer calls brick sidewalk “road”.

## 4 Walking Changes

No production change. The previous `WalkingSafetyPolicy` (synthetic-verified)
remains, but real-data yaw/drift was not validated. A general corridor based on
SegFormer inclusive road was rejected: it would mark road as sidewalk and break
curb safety.

## 5 Walking Before / After

```text
Issue: no-tactile sidewalk has no corridor
Before: BlindPath 0/45, SegFormer strict 22.2%
Change: none (inclusive road rejected; NEED_DECISION)
After: unchanged 0/45 production, 22.2% prototype
Regression: 173 tests pass
Decision: NEED_DECISION
```

## 6 Obstacle Baseline

`OBSTACLE_FAILURES.md`. UrbanEgo current 36.4% object recall / 32.1% precision /
6.8% frame false positive. Threshold sweep: recall config 38.4% recall, 8.7%
false positive; precision config 32.3% / 5.4%. No config reaches 90% recall.
BLV COCO-covered macro recall 41.8%; person 96.4%, car 78.1%, bicycle 31.1%,
bench 13.2%.

## 7 BLV-Relevant Failure Types

23 categories have no COCO class, including curb, stairs, doorway, pole,
railing, foldout sign, raised entryway, overhanging branches, sidewalk pits.
This is an `ARCHITECTURE_GAP`: a class-independent corridor occupancy signal is
missing. Ground/high obstacles were not measurable from local GT.

## 8 Obstacle Changes

None. Threshold tuning failed the dual-metric rule (recall gain cost FP; no
config near target). Current detector retained.

## 9 Obstacle Before / After

```text
Issue: real-video blocking recall
Before: 36.4% recall, 6.8% frame false positive
Change: none; threshold sweep rejected
After: unchanged
Regression: 173 tests pass; synthetic near=STOP regression remains
Decision: NEED_DECISION
```

## 10 Last10m Door Proposal Baseline

`UNAVAILABLE`. Automatic `EntranceProposal` is not connected. DeepDoors2 data is
not local, and no door recall is reported.

## 11 Target Search

No real Signboard subset was downloaded. Existing TargetLock tests pass; no
real KFC/Starbucks/Luckin recall numbers are claimed. Target Search remains
`DESKTOP_VERIFIED` by JVM tests only.

## 12 Target-Door Relation Data Gap

No public target→door relation GT. `relation_seed_v1` was not fabricated.
Status: `MISSING_GT`.

## 13 Regressions

173 JVM tests, 0 failures. Core regressions REG-001–REG-006 pass. No product
safety policy changed.

## 14 Remaining RED

1. General traversability without tactile paving.
2. Real-video obstacle recall and BLV object coverage.
3. Automatic entrance proposal.
4. Target→door relation GT.
5. Scene/Text production wiring.
6. Device verification of the corridor policy.

## 15 Device Test Priority

1. Static desk: no sustained STOP.
2. Straight corridor: command flips.
3. Phone yaw: no false drift.
4. True drift: detection.
5. Chair blocking: STOP and release.

Full 8-action list: `DEVICE_TEST_20261103.md`.

## Final Verdict

- Walking: **RED**
- Obstacle: **RED**
- Last10m Search: **YELLOW**
- Last10m Entrance: **RED**

APK: `app/build/outputs/apk/debug/app-debug.apk`
SHA-256: `2d19236e352b1729ba650e5f161349431068962a4af187b47ac9e66a76a48e21`
Tests: 173 / 0 failures.
