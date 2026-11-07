# Last10m real storefront demo report

The demo uses the existing production chain and replay source:

`ReplayVisionReasoner -> PerceptionOrchestrator -> Last10mStateMachine -> ArrivalAuthority -> Last10mSpeechPolicy`

Replay provenance remains `REPLAY`; it is not presented as live phone
recognition. Images and structured answers come from the restored LSVT public
street-scene set in `app/src/main/assets/demo/last10m/`.

## Scenarios

| scenario | frames | relation evidence | expected behavior |
|---|---:|---|---|
| `s1_arrival` | 3 | `door_1=SAME_TARGET`, `door_2/3=NEIGHBOR` | lock the fruit-market target, select the related door, request ground/door confirmation before arrival |
| `s2_neighbor` | 2 | `door_1=NEIGHBOR` | refuse to authorize the visible neighboring doorway and continue relation search |
| `s3_hazard` | 2 | `door_1=SAME_TARGET` plus ground obstruction | identify the target entrance, request ground view, report the obstruction, keep motion authorization separate |

The audit contains 7 frames across 3 scenarios. Frames with no door or no
target relation are retained as refusal/re-observation evidence and are not
counted as correct entrance identification.

## Acceptance measurements

The scenario JSON contains the required ground truth for entrance relation
(`SAME_TARGET` / `NEIGHBOR`) and target visibility. The Android runner exposes
state, target lock, candidate IDs, relation verdict, `needView`, speech output,
and provenance in its detail panel and log trail.

The following metrics are the ones to record from a device or desktop replay:

* target lock accuracy;
* correct entrance relation accuracy only on frames with a labeled relation;
* wrong-entrance rate;
* target-absent refusal/re-observation rate;
* views required before relation confirmation;
* ground obstruction detection;
* motion authorization after entrance and ground evidence.

No entrance accuracy number is inferred from the Entrances door-box archive,
because that archive does not label target-store-to-door relations.

## Reproduction

```bash
python pc/tools/last10m_scenario_audit.py \
  --scenarios app/src/main/assets/demo/last10m/scenarios.json \
  --out pc/out/last10m_scenarios_20261107/audit.json
```

The Android presentation entry point is
`com.blindnav.app.shadow.Last10mDemoActivity`. The current M1 environment does
not have the local `app/libs/sherpa-onnx.aar` dependency, so the Gradle Android
test task is still blocked in this checkout. The asset/schema audit and the
pure-Python replay gate audit are independent of that missing binary.

## Replay gate audit

The replay gate audit is deliberately stricter than a door-box count. It keeps
target lock, target-to-door relation, ground evidence, and motion authorization
as separate metrics. An unlabeled frame is refusal context, not a wrong
entrance; `REPLAY` evidence never becomes a claim about live phone perception.

Frozen result for the seven real LSVT replay frames:

| metric | result |
|---|---:|
| target lock | 6/7 |
| relation-evaluable frames | 5 |
| `SAME_TARGET` entrance | 4/5 |
| neighboring entrance rejected | 1 |
| wrong entrance | 0 |
| obstruction evidence | 2/7 |
| clear ground evidence | 0/7 |
| motion authorized | 0/7 |

The zero authorization count is expected for this fixture: every candidate
frame has unresolved or obstructed ground evidence. It is a safety-gate result,
not a usability success. The machine-readable per-frame output is committed at
`docs/assets/review_20261107/last10m_scenarios_20261107/replay_eval.json`.

Reproduce it with:

```bash
PYTHONPATH=pc python3 -m blindnav_pc.last10m_replay_eval \
  --scenarios app/src/main/assets/demo/last10m/scenarios.json \
  --out docs/assets/review_20261107/last10m_scenarios_20261107/replay_eval.json
```
