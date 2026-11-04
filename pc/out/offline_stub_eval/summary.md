# Offline stub eval rollup

Measured results for the three thin PC stubs on this branch. Committed reports are small
text/JSON/CSV only; datasets and images stay out of git.

| stub | report | key result |
|---|---|---|
| ImVisible traffic light | [imvisible_traffic_light/summary.md](../imvisible_traffic_light/summary.md) | raw mapping 100.00%, filtered accuracy 38.12%, unsafe green 0 |
| Entrances Last-10 m | [entrances_last10m/summary.md](../entrances_last10m/summary.md) | frame recall (in-range) 99.88% (845/846), refuse rate 1.05% |
| Blind-road ShadowWalk | [blindroad_shadowwalk/summary.md](../blindroad_shadowwalk/summary.md) | varied pose commands {'STRAIGHT': 55, 'LEFT': 31, 'RIGHT': 53, 'STOP': 50, 'UNKNOWN': 11}; STOP-on-open 0, UNKNOWN-when-mask-ok 0, oscillation 0 |

## 1. ImVisible traffic light

- Rows: 1603 (testing=739, validation=864); images present 1603/1603.
- Pseudo-sequences: 9 (length histogram `{'2': 1, '70': 1, '79': 1, '163': 1, '187': 1, '231': 1, '273': 1, '275': 1, '323': 1}`).
- Raw three-state mapping accuracy: **1.0000** (exact by construction -- no pixel model).
- Filtered accuracy at min_confirm=3: **0.3812**; stable rate 12.98%; abstention 61.88%.
- Unsafe green (GT red/uncertain -> green): **0**.
- Green recall 22.19%; red recall 9.54%; uncertain recall 100.00%.
- Confirmation sweep: min_confirm=1 accuracy 1.0000; min_confirm=3 accuracy 0.3812; min_confirm=5 accuracy 0.2757.

## 2. Entrances Last-10 m

- Frames: 854 (with GT doors 847; with in-range GT doors 846; empty labels 7).
- GT boxes: 922 (in range 920; unknown range 0).
- Frame recall, in-range GT: **99.88%** (845/846).
- Frame recall, all GT: 99.76% (845/847).
- Box recall, in-range: 99.57% (916/920).
- Refused: 9/854 (1.05%); reasons `{'ok': 0, 'no_candidate': 7, 'low_confidence': 0, 'distance_unknown': 0, 'out_of_range': 1, 'ambiguous': 1}`.
- Proposed candidate counts: `{'0': 9, '1': 778, '2': 63, '3': 4}`.
- Estimated range p5/p50/p95: 2.634/3.81/4.476 m.

## 3. Blind-road ShadowWalk

Varied pose (primary):

- Commands: {'STRAIGHT': 55, 'LEFT': 31, 'RIGHT': 53, 'STOP': 50, 'UNKNOWN': 11}.
- STOP on open walkable: **0**; UNKNOWN when mask ok: **0**; spurious turn: 0; oscillation: 0/113 consecutive-id pairs.
- Intended off-corridor STOP: 50 (synthetic 1.2 m offset; not a failure).

Centred pose (control):

- Commands: {'STRAIGHT': 87, 'LEFT': 36, 'RIGHT': 61, 'STOP': 0, 'UNKNOWN': 16}.
- STOP on open walkable: **0**; UNKNOWN when mask ok: **1**; spurious turn: 0; oscillation: 3/113 consecutive-id pairs.

## Blockers / not done

- No pixel model for the traffic light: GT is the dataset's own `mode` collapse, so raw
  accuracy is exact by construction. Filter numbers replay non-contiguous labels.
- Entrances has no distance labels and no detector confidences. `in-range` uses the same
  flat-ground estimator as the stub; `low_confidence`/`distance_unknown` cannot fire.
- ShadowWalk has no depth model and no real ego-motion; depth is a flat-ground plane and
  poses are synthetic. The blind-road images are not a certified continuous video.
- No Android / IMU / Pipeline wiring was added; no real-device runs.
- `sidewalk-semantic` was intentionally skipped.

## Reproduce

```bash
cd pc
.venv/bin/python tools/traffic_light_demo.py --split validation,testing --limit 0 \
    --report-dir out/imvisible_traffic_light
.venv/bin/python tools/last10m_demo.py --split all --limit 0 \
    --report-dir out/entrances_last10m
.venv/bin/python tools/shadowwalk_real_eval.py --split all --limit 0 \
    --offsets 0,0.4,-0.4,1.2 --report-dir out/blindroad_shadowwalk/varied_pose
.venv/bin/python tools/shadowwalk_real_eval.py --split all --limit 0 \
    --offsets 0 --report-dir out/blindroad_shadowwalk/centred_pose
.venv/bin/python tools/offline_stub_rollup.py
```
