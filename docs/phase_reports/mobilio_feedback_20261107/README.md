# Mobilio feedback reconciliation and next experiment

Date: 2026-11-07  
Evidence level: **DESKTOP_REAL_DATA** (frozen SANPO-Real replay, recorded-pose
offline proxy; not phone VIO and not a user study)

## What the execution-side feedback gets right

The four-way input table is supported by the frozen artifacts. In particular,
the current production arm has 49/481 authorized directions, 275/481 UNKNOWN,
and 17/295 `any_miss`; the ideal-input A arm still has only 29/481 direction
coverage. This is enough to reject the claim that replacing DAV2 or adding
phone VIO alone will make the current planner usable. The result supports a
joint failure involving the map/search contract and spatial evidence.

The safety interpretation is also correct: `any_miss` is a decision metric
that counts forward guidance on a frame with GT danger. It is not a detector
recall metric. Lowering it by producing UNKNOWN or refusing guidance is not
hazard-recognition success.

## What the feedback table gets wrong or leaves ambiguous

The Harvard A/D numbers in the feedback (`23/481` and `80/481` directions,
`458/481` and `401/481` UNKNOWN) are not the same metric as the final fused
authorized command in the current corrected replay. In the current frozen
`mobilio_harvard_reference_fixed_20261107/per_frame.csv`:

| condition | Surface CLEAR candidates | final authorized guidance | final safety UNKNOWN |
|---|---:|---:|---:|
| Harvard A | 23 | 8 | 168 |
| Harvard D | 80 | 71 | 347 |

Thus `23` and `80` match the surface-layer candidate count, while `8` and `71`
are the final guidance counts. The feedback's `458` and `401` are complements
of the surface candidates, not the final `safety_state=UNKNOWN` denominator.
They must not be compared directly with BlindNav's 275 UNKNOWN frames.

The current corrected Harvard replay also reports 39/295 danger misses and
21/150 confirmed-clear false stops in D. It is a useful behavior reference,
but the higher surface candidate count cannot justify replacing the safety
decision layer.

## New layer evidence from the same frozen inputs

The machine-readable layer matrix is in `layer_matrix.json`. It separates the
surface state, occupancy state, fused state, final command, and GT danger on
the same rows. The production D arm contains:

* surface state `UNKNOWN`: 134 frames;
* occupancy state `UNKNOWN`: 266 frames;
* fused final `UNKNOWN`: 275 frames;
* `surface=OBSERVE, occupancy=UNKNOWN`: 115 frames;
* `surface=OBSERVE, occupancy=CLEAR`: 130 frames;
* `surface=CLEAR, occupancy=UNKNOWN`: 26 frames;
* final authorized guidance: 49 frames.

The reason field gives a second view of the bottleneck: `surface:no_candidate_ray`
appears on 125 UNKNOWN rows, `occupancy:no_observed_clearance` on 97, and
`occupancy:no_fully_observed_clearance` on 44. These are not mutually exclusive
layer failures; the matrix is required to avoid treating them as a single
denominator.

The practical conclusion is narrower than “occupancy is the only problem”:
occupancy produces the larger UNKNOWN state footprint, while surface ray
search produces the largest single refusal reason. Both layers must be kept
separate in the next test.

## Next executable experiment

Do not change VIO, segmentation, DAV2, safety thresholds, or Android code in
this round. Use the frozen 481-frame production D input and run one map-layer
diagnostic/replay change at a time.

### E1 — layer certificate replay (required first)

Input: the same `per_frame.csv` rows and frozen evaluator. Output the matrix
already committed here, plus per-session counts for `surface_state`,
`occupancy_state`, `safety_state`, `effective_command`, `reason`, `danger_any`,
and `confirmed_clear`.

Pass condition: every row has one surface state, one occupancy state, one fused
state, and one final command; numerators and denominators remain 481/295/150
where applicable. UNKNOWN is never recoded as CLEAR or hazard recognition.

### E2 — one evidence-preserving occupancy change

Only after E1 is independently checked, implement one diagnostic-only occupancy
variant: retain the existing body-intersection and confirmed-occupancy STOP
rules, but expose the longest partially observed ray as a **detour candidate**
(`observation_hint`) instead of silently discarding it. It must not become
`authorized_motion`, TTS “向左/向右走”, or CLEAR. Keep the existing `CLEAR`
certificate unchanged.

Compare current D against the variant on the same rows:

* final authorized guidance and confirmed-clear guidance;
* `observation_hint` coverage;
* danger forward-guidance miss 17/295 denominator;
* false stops on confirmed clear 0/150 denominator;
* UNKNOWN and state flips;
* per-session and per-reason counts.

Success requires an increase in diagnostic detour candidates and/or confirmed
clear guidance without increasing danger forward guidance or false stops. If
only hints increase while final guidance is unchanged, report a usable
diagnostic improvement but do not call it navigation improvement.

### E2 result on frozen production D

The diagnostic evaluator is `pc/tools/mobilio_occupancy_hint_eval.py`; it reads
the packaged production CSV and never changes the production decision. The
machine-readable result is `e2_occupancy_hint.json`.

| metric | baseline / variant |
|---|---:|
| frames | 481 / 481 |
| observation hints | 0 / 37 |
| hints on GT danger | 0 / 19 |
| hints on confirmed-clear frames | 0 / 12 |
| authorized motion | 49 / 49 |
| danger forward-guidance miss | 17/295 / 17/295 |
| confirmed-clear false STOP | 0/150 / 0/150 |

This is a **diagnostic improvement only**: the variant exposes 37 partially
observed occupancy rays, but it does not recover a single authorized direction.
It therefore passes the safety invariants and fails the navigation-usability
improvement criterion. An independent verifier must check this result before
E3 is considered.

### E3 — one surface search change, only if E2 does not identify a bottleneck

Use the same contract for `surface:no_candidate_ray`: retain non-walkable and
road/curb evidence, but emit the best continuous candidate and its truncation
reason as a non-authorizing observation hint. Do not shorten the ray or turn
UNKNOWN into CLEAR. Compare the same metrics and stop if the change only moves
rows between UNKNOWN and OBSERVE.

## Decision gate

The current evidence supports **ADJUST / diagnostic layer work**, not a planner
replacement and not a VIO detour. A layer variant may be promoted only if the
independent verifier confirms same-input metrics and the safety invariants above.
If both E2 and E3 increase hints without improving confirmed-clear guidance,
the next bottleneck is upstream spatial evidence quality, not fusion policy.

### E3 status

E3 is currently **NOT_EVALUABLE** from the frozen CSV. The production rows keep
`surface_state`, `surface_distance_m`, `surface_unknown_cells`, and
`surface_fully_observed`, but do not keep the failed ray's truncation cell,
last walkable cell, candidate angle, or candidate distance. All 134 production
D surface-UNKNOWN rows have `surface_distance_m=0`, so a post-hoc rule would
manufacture a hint rather than expose a recorded candidate.

The next valid E3 run must add those diagnostic fields to the offline evaluator
while keeping the safety decision frozen, then regenerate the same 481 rows.
Until that run is independently checked, no Surface hint or authorization claim
is made.

The evaluator is now instrumented to export `surface_termination_distance_m`,
`surface_last_walkable_distance_m`, termination/last-walkable cell coordinates,
and the existing candidate angle. Unit-level checks cover a non-walkable ray and
the no-candidate case. The full 481-frame E3 replay is still pending because
the required SANPO/model inputs are not available in the review artifact bundle.
