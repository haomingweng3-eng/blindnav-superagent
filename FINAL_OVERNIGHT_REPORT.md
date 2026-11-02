# FINAL OVERNIGHT REPORT — 2026-11-03

## 一句话

真实视频上最大的瓶颈仍是“没有盲道时的通用走廊”；障碍真实召回只有 36–38%；Last10m 的目标/到达安全逻辑稳，但自动正确门候选仍未接生产。

## Summary Table

| Capability | Before | After | Evidence | Status |
|---|---|---|---|---|
| Walking | BlindPath 0/45 no-tactile SANPO frames | unchanged; SegFormer strict 22.2% prototype | DESKTOP_REAL_DATA | RED |
| Obstacle | 36.4% recall / 6.8% frame FP on UrbanEgo | unchanged; best sweep 38.4% recall | DESKTOP_REAL_DATA (model GT) | RED |
| Last10m Search | TargetLock/alias tests pass | unchanged | DESKTOP_VERIFIED | YELLOW |
| Last10m Entrance | no automatic proposal | unchanged | NOT_CONNECTED | RED |
| Scene | engine exists, no caller | unchanged | NOT_CONNECTED | RED |
| Text | policy tests only | unchanged | PROTOTYPE | RED |
| Android Experience | no user-facing shell | not built (blocked by P0/P1) | NOT_DONE | RED |

## Real-data results

- SANPO-Real: 45/45 no-tactile frames, current detector valid corridor **0%**;
  SegFormer-B0 strict sidewalk **22.2%**, inclusive 100% but 90.1% of sidewalk
  pixels are classified as road. Inclusive corridor is unsafe.
- UrbanEgo run7: 36.4% object recall, 32.1% precision, 6.8% frame false
  positive. Threshold sweep cannot reach 90% recall.
- BLV: COCO-covered macro recall 41.8%; person 96.4%, car 78.1%, bicycle 31.1%,
  bench 13.2%. 23 BLV-important categories have no COCO class.
- Last10m: existing 166 tests pass; no new real target/door data, so no fake
  accuracy is reported.

## Top 5 real failures

1. **No-tactile corridor**: current detector produces 0/45 valid corridors.
2. **Obstacle recall**: 36–38% real-video recall; 23 BLV categories uncovered.
3. **Sidewalk/road confusion**: 90% of sidewalk pixels read as road by the
   off-the-shelf segmentation model.
4. **Automatic entrance proposal**: still manual/VLM only, no production model.
5. **Scene/Text**: no production caller; always-watching OCR/text memory absent.

## What changed

- Froze `BASELINE_BEFORE_REAL_DATA.md`, `eval/real_v1/`, and dataset inventory.
- Added reproducible SANPO/UrbanEgo/BLV baseline scripts and results.
- Clean build, 173 tests / 0 failures, new APK.
- No production algorithm change: threshold tuning failed the dual-metric rule;
  the inclusive “walkable” fallback would be unsafe at curbs.
- Superagent setup is operational (bot bridge → Ticket → execution → Issue
  reply), but it is not used for this core decision.

## What is still NOT done

- General traversability model (the P0 blocker).
- Real-video obstacle detector with class-independent occupancy.
- Automatic entrance proposal and relation GT.
- Scene/Text production wiring.
- Android Experience Mode (intentionally not built while P0/P1 are RED).
- Device verification of the corridor policy.

## Tomorrow's first action

Install the new APK, stand still facing a laptop/desk for 10 seconds, and confirm
the app does **not** repeat “前面很近，先停下”. Then follow the 8 actions in
`DEVICE_TEST_20261103.md`.

## Final three answers

1. **Strongest on real data**: Last10m TargetLock/Arrival safety guards and
   person/car detection; the system is conservative and refuses to guess doors.
2. **Largest real failure**: no-tactile general walkable corridor (0/45).
3. **Next most valuable fix**: build a real walkable-mask model on
   Mapillary/SANPO and measure curb/road false positives, not just availability.

APK: `app/build/outputs/apk/debug/app-debug.apk`
SHA-256: `2d19236e352b1729ba650e5f161349431068962a4af187b47ac9e66a76a48e21`
Tests: 173 / 0 failures.
