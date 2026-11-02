# BlindNav Core Pipeline Audit — 2026-11-03

Scope: read the current production code, not prior reports. No new features added
except the corridor policy and target-aware text policy explicitly required by the
core optimization brief.

## Status legend

- `DESKTOP_VERIFIED` — exercised by JVM tests on this Mac.
- `DEVICE_VERIFIED` — exercised on the Xiaomi device.
- `PROTOTYPE` — real code path exists, but only partial evidence or a proxy signal.
- `NOT_CONNECTED` — class exists, no production caller.
- `NOT_TESTED` — no evidence yet.

## 1. Walking

Production chain today:

```text
CameraX ImageAnalysis (CameraActivity.analyze)
  -> frame gate + quality (150 ms budget)
  -> ObstacleDetector (YOLOv8n, every frame)
  -> CameraActivity.show
  -> WalkingSafetyPolicy.evaluate        [new]
  -> TtsEngine.speakVisual

slowExecutor every ~3 s:
  -> BlindPathDetector (yolo-seg-int8, tactile paving/crosswalk)
  -> PathObservation / PathGeometry
  -> CameraActivity.showPath
  -> corridorSide/corridorScore
  -> WalkingSafetyPolicy.evaluate        [new]
```

Separate route-heading chain still exists:

```text
Location + compass -> NavigationService -> DriftGuard -> TTS
```

Status:

- Visual corridor + obstacle fusion: `DESKTOP_VERIFIED` (synthetic policy tests).
- General traversability / walkable mask: `NOT_CONNECTED`. `BlindPathDetector`
  detects tactile paving and crosswalks, not arbitrary walkable ground.
- Route-heading walking chain: `PROTOTYPE` (GPS/compass, separate from camera).
- Device walking: `NOT_TESTED` after this change.

## 2. Obstacle

Production chain today:

```text
CameraX frame
  -> ObstacleDetector (COCO YOLOv8n, blocking class set)
  -> central corridor band = 30%..70% of image width
  -> bottom > 0.58, person stricter (0.72 + 65% confidence + 25% height)
  -> Hit.corridorOverlap                [new field]
  -> CameraActivity.show
  -> WalkingSafetyPolicy                [new gate]
  -> hazard TTS + hazardHoldUntil
```

Before this change, `CameraActivity.show` also had:

```text
LENS_FOCUS_DISTANCE close for >900 ms
  -> nearFocusAt
  -> if hits empty: "前面很近，有东西挡着，先停下" (hazard)
  -> blockingNow = obstacleClearPolicy(hits) || closeFocus
```

That is the `near -> STOP` path. It bypasses corridor overlap, walking state and
persistence.

Status: `DESKTOP_VERIFIED` for the policy, `PROTOTYPE` on device.
Single-frame noise was only protected by `ObstacleClearPolicy` after a stop,
not before it.

## 3. Last10m

Production chain today:

```text
CameraActivity.onFrame
  -> Last10mSession.onFrame
  -> OcrEngine (bundled ML Kit, 300 ms gate, KEEP_ONLY_LATEST)
  -> PerceptionOrchestrator (OCR + optional VLM/replay/manual)
  -> Last10mStateMachine
       TargetLock (multi-frame / two independent sources)
       EntranceTracker + EntranceProposal
       TargetDoorRelationEngine
       GroundEvidence
  -> ArrivalAuthority (six guards)
  -> Last10mSpeechPolicy
  -> TtsEngine
```

Stage status:

- SEARCH: `DESKTOP_VERIFIED`; OCR/VLM/replay/manual paths exist.
- LOCK: `DESKTOP_VERIFIED`; FUZZY/weak aliases do not lock alone; stale locks expire.
- ENTRANCE: `PROTOTYPE`. `EntranceProposal` is an interface; no automatic door
  detector is connected. Real candidates come from manual panel taps and VLM
  facts. Recall is therefore not measurable from production code yet.
- ARRIVAL: `DESKTOP_VERIFIED`; six-condition guard is tested, including
  neighbor-door conflict and stale evidence.
- Device Last10m: `PROTOTYPE`; prior manual device run only covered mount/TTS,
  not a full four-stage ground-truth pass.

## 4. Scene / Text

Scene chain:

```text
SceneAwarenessEngine -> SceneMemory -> SpeechPolicy -> SceneNarrator
```

Status: `NOT_CONNECTED`. There is no production caller in `CameraActivity`.
The 15 existing scene tests use the engine directly.

Text chain:

```text
Last10mSession -> OcrEngine -> target matching (last10m only)
```

General always-watching OCR with TextMemory/TextSalience: `NOT_CONNECTED`.
`TextSaliencePolicy` was added as a pure policy and tested, but is not wired into
the camera loop.

Consequences:

- Static desk safety can be fixed by not treating near-focus as STOP.
- Static desk *recognition* ("前方是一台电脑显示器") is still not available in
  production.
- Target-dependent KFC salience is policy-tested but not live.

## 5. What is actually connected?

| Capability | Production caller | Status |
|---|---|---|
| Obstacle detector | `CameraActivity.analyze -> show` | CONNECTED |
| Tactile path detector | `CameraActivity.showPath` | CONNECTED |
| Unified corridor gate | `CameraActivity.show/showPath` | CONNECTED (new) |
| Walking accelerometer state | `CameraActivity` sensor listener | CONNECTED (new, heuristic) |
| TargetLock / Last10m | `Last10mSession.onFrame` | CONNECTED when session mounted |
| EntranceProposal automatic | none | NOT_CONNECTED |
| SceneAwarenessEngine | none | NOT_CONNECTED |
| General TextMemory/OCR | none | NOT_CONNECTED |
| TextSaliencePolicy | none | NOT_CONNECTED (policy only) |

## 6. Data available tonight

- No walking/obstacle video dataset in the repo.
- Existing assets: `test_obstacle.png`, `test_blind.png`, six Last10m demo JPEGs.
- Existing frozen test data: `app/src/test/resources/last10m/*` (TSV/JSON/images).
- New frozen policy set: `eval/core_v1/*.json`.
- Therefore all new walking/obstacle numbers are `DESKTOP_SIMULATION`, not real
  video recall.
