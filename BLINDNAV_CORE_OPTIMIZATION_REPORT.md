# BlindNav Core Optimization Report — 2026-11-03

## 0. Scope and evidence level

Tonight changed one production chain: the camera walking corridor now owns both
path alignment and obstacle occupancy. Scene/Text were audited but not wired.

All new numbers are **DESKTOP_SIMULATION** on frozen synthetic cases
(`eval/core_v1`). There is no walking/obstacle video dataset in the repo, so no
real-video recall is claimed. Existing Last10m tests are JVM tests, not device
ground truth.

Test baseline: 166 existing unit tests, 0 failures.
After: **173 tests, 0 failures** (166 + 1 frozen core eval + 6 regressions).

## 1. Current architecture

### Walking

```text
CameraX -> frame gate/quality -> ObstacleDetector (every 150 ms)
        -> CameraActivity.show -> WalkingSafetyPolicy -> TTS
slowExecutor 3 s -> BlindPathDetector -> PathObservation
        -> CameraActivity.showPath -> corridor state -> WalkingSafetyPolicy
```

Route heading still uses a separate `NavigationService -> DriftGuard` chain.

### Obstacle

```text
CameraX -> ObstacleDetector (YOLOv8n COCO)
        -> central 30–70% corridor overlap + bottom threshold
        -> Hit.corridorOverlap -> WalkingSafetyPolicy
        -> STOP/WARN/CLEAR -> hazard TTS
```

### Last10m

```text
CameraX -> Last10mSession -> OcrEngine -> PerceptionOrchestrator
        -> Last10mStateMachine (TargetLock / EntranceTracker / Relation / Ground)
        -> ArrivalAuthority -> Last10mSpeechPolicy -> TTS
```

### Scene / Text

`SceneAwarenessEngine -> SceneMemory -> SpeechPolicy` exists but has no
production caller. `OcrEngine` is connected only inside Last10m.

## 2. Baseline (before tonight's change)

| Item | Baseline |
|---|---|
| Existing unit tests | 166 / 166 pass |
| Obstacle false STOP on negative synthetic cases | 4 / 5 = 80% |
| Blocking-obstacle recall | 4 / 4 = 100% |
| Single-frame noise causes STOP | 1 frame |
| Walking command flips (oscillation) | 190 / min |
| Rotation-only false side commands | 2 frames |
| True drift detection | immediate, no dwell |
| Recovery to KEEP | immediate |
| Last10m | 166 tests pass; no change tonight |
| Scene/Text production | NOT_CONNECTED |

The baseline false-STOP cases were: static desk, close wall, close door/wall,
and single-frame detector noise.

## 3. Walking

### Issue

`PathStability` only required one valid frame for a new side key. Phone rotation
could move the mask to another side and immediately produce LEFT/RIGHT. The
obstacle and path signals had no shared corridor state.

### Change

- Added `WalkingSafetyPolicy`:
  - side change requires 3 consecutive frames and 400 ms dwell;
  - when phone rotation is suspected (heading change ≥6° within 1 s), requires
    4 frames and 700 ms;
  - center requires 2 frames before KEEP;
  - corridor unavailable for 500 ms -> REOBSERVE, never LEFT/RIGHT.
- Added `WalkingState` from accelerometer variance to distinguish walking from
  standing still (heuristic, not a step counter).
- `CameraActivity.showPath` now only updates corridor state; `show` emits path
  speech from the same per-frame decision used for obstacles.
- True drift is still detected: 3 frames ≈ 450 ms at 150 ms/frame.

### Before / After

```text
Issue: path command flips and rotation false drift

Before:
command flips = 190 / min
rotation-only side frames = 2
true drift detection = frame 0 (unstable)
recovery to KEEP = 0 ms

Cause:
one-frame PathStability key; no heading compensation; no dwell

Change:
WalkingSafetyPolicy side dwell 3 frames/400 ms (4/700 ms when rotated),
center 2 frames, REOBSERVE after 500 ms corridor loss

After:
command flips = 0 / min
rotation-only side frames = 0
true drift detected at frame 3 (~450 ms)
recovery to KEEP = 150 ms

Decision: KEEP, pending device verification
```

Status: `DESKTOP_VERIFIED`, `PROTOTYPE` for real camera corridor.
The corridor is still derived from tactile paving, not a general walkable mask.

## 4. Obstacle

### Was `near = STOP`?

Yes. `LENS_FOCUS_DISTANCE` close for >900 ms set `nearFocusAt`; when no detector
hit existed, `show()` still played “前面很近，有东西挡着，先停下” and set
`blockingNow` through `closeFocus`. That is exactly the static-desk false STOP.

### Change

- `nearFocus` is now only an input. It can produce WARN only when:
  walking + corridor available + near-focus persists ≥1.2 s.
- STOP requires a detector hit with `corridorOverlap ≥ 0.25`, at least 2 frames
  and 300 ms persistence.
- STOP is held for 800 ms after the hit disappears, then cleared.
- Single-frame detector noise cannot STOP.
- `corridorOverlap` is now carried on every `ObstacleDetector.Hit`.

### Before / After

```text
Issue: REG-001/004 static near-focus false STOP

Before:
false STOP = 4 / 5 negative cases (80%)
blocking recall = 4 / 4
single-frame STOP = 1 frame
recovery = 0 ms

Cause:
nearFocus OR detector hit bypassed corridor occupancy, walking state,
and temporal persistence

Change:
STOP requires corridor overlap + 2 frames/300 ms persistence;
nearFocus alone gives at most WARN when walking + corridor + 1.2 s

After:
false STOP = 0 / 5 (0%)
blocking recall = 4 / 4 (unchanged)
single-frame STOP = 0
recovery = 750 ms (within 2 s)

Decision: KEEP
```

Status: `DESKTOP_VERIFIED`, `PROTOTYPE` on device.

## 5. Last10m — SEARCH / LOCK / ENTRANCE / ARRIVAL

No code change tonight; the existing chain is already conservative and covered by
166 tests. Status:

- **SEARCH**: `DESKTOP_VERIFIED`. OCR + explicit alias + VLM/replay/manual inputs.
  Target profile includes 肯德基/KFC, 星巴克/STARBUCKS, 瑞幸/LUCKIN style aliases.
- **LOCK**: `DESKTOP_VERIFIED`. Two distinct frames or two independent sources;
  FUZZY and weak derived aliases never lock alone; stale target expires.
- **ENTRANCE**: `PROTOTYPE` / partially `NOT_CONNECTED`. `EntranceProposal` is an
  interface; no automatic door detector is wired. Production candidates come
  from manual panel taps and VLM facts. Door recall cannot be measured from
  production yet.
- **ARRIVAL**: `DESKTOP_VERIFIED`. Six guards; neighbor door and stale evidence
  are rejected. Ground is `visible/step/curb/obstruction/unknown`, never `SAFE`;
  `DEBUG_MANUAL` is labeled as manual.

Remaining Last10m problem: without automatic entrance proposals, the product can
say “看到肯德基，但入口还不能确认” but cannot yet propose the right door by itself.

## 6. Scene Awareness

Status: `NOT_CONNECTED`, verdict **RED**.

- `SceneAwarenessEngine`, `SceneMemory`, `SpeechPolicy` are unit-tested (15 tests)
  but no production caller.
- Static desk safety is improved: it no longer repeatedly says “前面很近，停下”.
- Static desk *recognition* (“前方是一台电脑显示器”) is still absent from the
  production camera. The user gets silence, not a correct scene description.
- No target-aware scene salience is live.

## 7. Text Awareness

Status: general OCR `NOT_CONNECTED`, verdict **RED**.

- `OcrEngine` runs only inside Last10m at 300 ms gate.
- Added `TextSaliencePolicy` (TARGET / WAYFINDING / GENERAL / NOISE) with
  10 s duplicate cooldown and “store general text, do not speak”.
- Policy tests cover: KFC spoken only when target is KFC; KFC silent when target
  is library; EXIT spoken; general web text silent; user query returns stored text.
- Not wired into `CameraActivity`; no always-watching 2–3 Hz OCR in production.

## 8. Regression cases

Added under `app/src/test/java/com/blindnav/app/regression/CoreRegressionTest.kt`:

| ID | Assertion | Result |
|---|---|---|
| REG-001 | static desk never STOP | PASS |
| REG-002 | KFC locks within 2 frames | PASS |
| REG-003 | KFC silent when target is library | PASS |
| REG-004 | close wall never STOP | PASS |
| REG-005 | unknown object in corridor STOPs | PASS |
| REG-006 | neighbor door never ARRIVAL | PASS |

Frozen data: `eval/core_v1/obstacle_cases.json`, `walking_cases.json`,
`metrics_after.json`.

## 9. Synthetic timeline (core_v1)

```text
00:00 corridor AVAILABLE, KEEP
00:03 person at corridor edge, overlap 0.10 -> no STOP
00:06 person crosses, overlap 0.70, 2 frames/300 ms -> STOP
00:09 person clears -> STOP hold 800 ms -> KEEP
00:12 phone rotates, mask jumps left -> no LEFT (rotation dwell)
00:15 true drift left, 3 frames/450 ms -> LEFT
00:18 returns center, 2 frames -> KEEP
```

This is policy simulation, not a real video replay.

## 10. Remaining RED

1. **No general traversability mask.** Walking corridor is tactile-paving based.
   On pavement without a blind path, corridor availability is 0; obstacle fusion
   falls back to detector hits only. This blocks a true “walkable corridor”
   product claim.
2. **Automatic entrance proposal not connected.** Last10m can reject wrong doors
   but cannot propose the right one without manual/VLM input.
3. **Scene awareness not connected.** Static desk is safe but not described.
4. **Text always-watching not connected.** General OCR/text memory is policy-only.
5. **No real walking/obstacle video benchmark.** All new numbers are synthetic.
   Device false-positive rate is unknown.
6. **Walking state is a heuristic.** Accelerometer variance threshold 0.55 has
   not been calibrated on the phone.

## 11. Tomorrow device test (max 10 actions)

1. Install the new APK; open 看路.
2. Stand still facing a desk/laptop for 10 s; confirm no repeated “前面很近，先停下”.
3. Walk a clear straight corridor 30 s; count LEFT/RIGHT flips; target ≤3/min.
4. Stand still and rotate the phone ±30°; confirm no false LEFT/RIGHT.
5. Walk with a real 0.5–1 m lateral offset; confirm LEFT/RIGHT within ~1 s.
6. Put a chair in the corridor; approach it; confirm STOP ≤1 s.
7. Remove the chair; confirm release ≤2 s.
8. Stand close to a wall/door; confirm no STOP.
9. Put an unknown box in the corridor; confirm STOP.
10. Mount KFC_A Last10m; verify target lock, two doors -> “不能确认”, no arrival;
    press “门前地面已看” and confirm `OBSERVED_MANUAL`, never `SAFE`.

Collect logcat and screenshots for each. Do not claim device verification until
these pass.

## FINAL VERDICT

- Walking Corridor: **YELLOW** — flips and rotation false drift removed in simulation; no general traversability, no device run.
- Obstacle Risk: **YELLOW** — near=STOP removed, false STOP 80%→0 in frozen cases; real-video/device false-positive unknown.
- Last10m Target Search: **YELLOW** — desktop verified, device not re-verified.
- Last10m Entrance: **RED** — automatic entrance proposal not connected.
- Scene Awareness: **RED** — not connected; desk recognized as safe silence, not as a computer.
- Text Awareness: **RED** — general OCR/text memory not connected.

**今晚真正改善了什么：** 静止对着电脑或贴墙时不再被误报成“前面很近先停下”；走直线时手机原地转动不再触发左右提示，左右指令也不再每 1–2 帧来回翻。
