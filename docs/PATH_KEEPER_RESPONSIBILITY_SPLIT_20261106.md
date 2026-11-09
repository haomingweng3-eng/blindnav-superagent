# P1 Path Keeper — responsibility split and rebuilt measurement (Issue #18)

Status: **PR open for operator adoption — not merged.** One structural change (Path Keeper /
Obstacle Guardian / Safety Coordinator) plus a full re-measurement and a fresh independent
re-test. No threshold search, no segmentation retraining, no DAV2 replacement.

Evidence level: **DESKTOP_REAL_DATA** for the SANPO-Real A/B/C tables; **PROTOTYPE** for the
GT-free proxy statistics on the local phone clips. Not DEVICE_VERIFIED, not USER_TESTED, not a
safety claim: BlindNav is not a certified mobility aid and does not replace the cane. The local
clip labels cannot support the safety metrics (see §7) and are not presented as validation.

This is by an automated agent, not a human.

## 1. Problem

PR #17 (open, same base branch) showed that the PR #15 four-channel negative-evidence fusion
closes the *representation* defect under good inputs, but its evaluated policy is very
conservative: annotated guidance coverage 190/481 (39.5 %), production 255/481 (53.0 %), with
production false-free-any 130/295 (44.1 %). The operator reopened this as a structural problem:

* the fused rule "any credible negative channel -> STOP" mixes four different physical
  responsibilities (road surface, curb, 3D object occupancy, unknown evidence) into one stop;
* "STOP" was used for `NON_WALKABLE` surface evidence, though a road/terrain surface is not a
  confirmed collision;
* the collision criterion was "at least 5 points in a 1 m forward bin", which is sampling count,
  not physical occupancy;
* the reported "false stop" denominator counted frames where an object/vegetation sat in the path,
  so it was not a true false-stop denominator at all.

Issue #18 asks for one structural change (Path Keeper / Obstacle Guardian / Safety Coordinator
fusing to CLEAR / BLOCKED / UNKNOWN / OBSERVE), the hard rules below, and a rebuilt measurement
with numerator/denominator per metric and per-session grouping, marking NOT_EVALUABLE where the
data cannot decide.

## 2. Evidence Before

Frozen PR #15 / PR #17 assets (unchanged, never edited):

* `docs/CORRIDOR_TRUTH_LOSS_20261106.md` — 481 SANPO-Real frames (sessions
  `-PqSDmiE…` 121, `M5UIabs…` 184, `UGNwBEZ…` 176); 23-frame fixed path-surface regression set.
* `docs/PATH_KEEPER_NEGATIVE_EVIDENCE_20261106.md` — PR #17 A/B/C on the same 481 frames:
  * annotated: A coverage 470/481 (97.7 %), false-free ground 92/103 (89.3 %);
    B coverage 190/481 (39.5 %), false-free ground 0/103, false-stop-ground 188/378 (49.7 %);
  * production: A coverage 374/481 (77.8 %), false-free ground 47/103 (45.6 %);
    B coverage 251/481 (52.2 %), false-free ground 19/103 (18.4 %), false-free any 130/295 (44.1 %).
* Issue #18's decisive reading of that table: the 49.7 % "false stop" is mostly *correct*
  blocking of GT object/vegetation occupancy, so the real over-conservatism was unmeasured.

This PR re-runs the same 481 frames, same corridor/pose setup, same GT scoring once per frame.
Arm A is the PR #17 policy code **as-is** (`ShadowWalkTracker.update_evidence`, four channels,
`temporal_confirm_frames = 0`); on the annotated inputs arm A reproduces the committed PR #17
per-frame reference exactly (481/481 rows, 0 mismatches, `reference_check.json`).

## 3. Change

### 3.1 Responsibility split (new modules, PC decision layer only)

| module | responsibility | output |
|---|---|---|
| `pc/blindnav_pc/path_keeper.py` | pavement / road / curb / boundary / drift | CLEAR / OBSERVE / UNKNOWN + lateral guidance |
| `pc/blindnav_pc/obstacle_guardian.py` | objects that occupy the human path (3D occupancy) | CLEAR / OBSERVE / BLOCKED / UNKNOWN |
| `pc/blindnav_pc/safety_coordinator.py` | fusion, severity-monotone | CLEAR / BLOCKED / UNKNOWN / OBSERVE (+ guidance only on CLEAR) |
| `pc/blindnav_pc/safety_states.py` | shared 4-state vocabulary + severity | — |
| `pc/blindnav_pc/corridor_evidence.py` | adds `build_split_corridor_evidence`: the frozen four-channel evidence **plus** the curb-ground vs 3D-obstacle decomposition | unchanged 4-channel contract |

The four-evidence structure is retained: `evidence.hazard` is exactly
`curb_ground + 3D obstacle points`, so the split changes responsibility assignment, never the
amount of negative evidence.

### 3.2 Hard rules -> implementation

| Issue #18 hard rule | implementation | test |
|---|---|---|
| NON_WALKABLE alone never STOP | Path Keeper maps non-walkable/curb surface evidence to **OBSERVE**, never BLOCKED; only the Obstacle Guardian can produce BLOCKED | `test_path_keeper.py`, `test_safety_coordinator.py` |
| do not suppress real obstacles | fusion is severity-monotone, BLOCKED always wins over path CLEAR/UNKNOWN | `test_obstacle_guardian.py::test_obstacle_suppression_is_impossible_in_the_fusion` |
| distinguish curb / road / grass / wall / vertical obstacle / unconfirmed space | separate channels: `curb_ground`, `non_walkable` ground, 3D obstacle-height points, `unknown` ground; roles `ROLE_HAZARD` vs `ROLE_NON_WALKABLE` vs `ROLE_IGNORE` | `test_corridor_evidence_split.py` |
| risk only with swept-body intersection + depth quality + height + continuity + distance + actual clearance | spatial mode: body swept rectangle 1.5–6.0 m × ±0.4 m (a priori body constants), height band 0.15–2.0 m, signed clearance, calibration-confidence floor (existing `GroundConfig.min_confidence = 0.45`), depth 0.3–12 m, temporal continuity 2-of-3 frames | `test_obstacle_guardian.py` |
| remove fixed N-point collision criterion | spatial mode uses connected 3D occupancy components with physical extent (≥0.08 m ground-plane, ≥0.15 m height), not a point count; the legacy 5-point rule survives only in the coarse ablation arm B | `test_obstacle_guardian.py::test_spatial_mode_does_not_use_the_legacy_five_point_rule` |
| insufficient evidence => UNKNOWN, never a pass | Path Keeper UNKNOWN on unseen/unknown surface; Obstacle Guardian UNKNOWN on failed calibration / insufficient depth quality; Coordinator UNKNOWN wins over CLEAR/OBSERVE | `test_safety_coordinator.py` |
| no debounce lengthening | no debounce/STOP-hold change; arm C's temporal continuity is over obstacle evidence (2 of 3 frames), not a STOP→UNKNOWN debounce | config diff / tests |

The coarse (B) and spatial (C) obstacle modes exist only for the ablation: B is the minimal
responsibility split (legacy support rule, no new gate), C adds spatial fusion (physical
occupancy, swept intersection, clearance, depth-quality gate, continuity). No STOP Safety Policy,
MotionAuthority or ArrivalAuthority file is touched, and `app/` is NOT_CONNECTED/unchanged.

### 3.3 Rebuilt measurement

`pc/tools/responsibility_split_eval.py` + pure, unit-tested `responsibility_split_metrics.py`:

1. **curb/road danger miss** — GT road ground inside the body sweep (1.5–6.0 m) / GT curb ground
   (2.5–6.0 m, chest camera cannot see nearer curb), ≥5 GT points = event. Reported three ways:
   conservative any-forward-guidance miss, STRAIGHT-only (definite) miss, and LEFT/RIGHT guidance
   as **NOT_EVALUABLE** (a single frame cannot establish that the guided turn avoids the surface).
   GT road / curb denominators are separate; GT non-walkable-ground danger is reported alongside.
2. **3D occupancy danger miss** — GT object-class pixels at 0.15–2.0 m height inside the same body
   sweep; same three views; plus any-GT-danger secondary view (no one-sided metric).
3. **true false-stop** — counted only on a **confirmed clear path**: oracle plane valid, all five
   1.5–6.0 m walkable bins observed (≥5 GT points each), no GT danger of any kind, no unlabelled
   GT ground, and **no GT non-walkable 3D point of any class in the body swept volume**
   (vegetation/tree/wall included; this is what excludes the one vegetation-in-path frame from the
   denominator). Unnecessary OBSERVE and UNKNOWN on the same
   denominator are reported separately; the weak "no GT danger + walkable points" view is kept
   only as a legacy-comparison label.
4. **effective guidance coverage** — frames with usable forward guidance over all frames, over
   frames without GT danger, and over confirmed-clear frames.
5. **UNKNOWN share** — plus OBSERVE/BLOCKED/CLEAR/STOP shares.
6. **decision flips and latency** — consecutive-frame effective-command flips (count/transitions,
   per minute) and latency from a GT hazard onset to the first non-forward warning within 10
   frames (p50/p95 in frames and in desktop frame-spacing ms; NOT_EVALUABLE with no hazard onset).

Every metric carries numerator, denominator, rate, `EVALUABLE`/`NOT_EVALUABLE` status and a
`low_sample` flag (<10 denominator frames); every table is grouped per session. The fixed
PR #15 23-frame set is recovered and verified (23/23) as a reporting label only, never fed to the
algorithm.

## 4. Evidence After — SANPO-Real, same 481 frames

Geometry/setup identical to PR #15/#17; one Path Keeper/Guardian/Coordinator pair per (session,
condition, arm); all arms see byte-identical inputs within a condition; GT scored once per frame.
Full numbers: `docs/assets/path_keeper_responsibility_split_20261106/` (`summary.json`,
`metrics.csv`, `per_frame.csv`, `reference_check.json`, `summary.md`; `failures/` holds six
raw+overlay failing-frame cases — production road/curb/occupancy misses for arms B/C and one
annotated occupancy miss).

### 4.1 `annotated` — human segmentation + oracle geometry (proves the decision logic)

| metric | arm A (PR #17) | arm B (split) | arm C (split + spatial) |
|---|---:|---:|---:|
| road danger miss (conservative) | 0/15 | 0/15 | 0/15 |
| curb danger miss (conservative) | 0/84 | 0/84 | 0/84 |
| curb/road miss, STRAIGHT-only / turn NOT_EVALUABLE | 0/103 / 0/103 | 0/103 / 0/103 | 0/103 / 0/103 |
| **3D occupancy miss (conservative)** | 6/218 (2.8 %) | 6/218 (2.8 %) | **1/218 (0.5 %)** |
| 3D occupancy miss, STRAIGHT-only | 4/218 (1.8 %) | 4/218 (1.8 %) | **0/218** |
| occupancy turn-guidance NOT_EVALUABLE | 2/218 | 2/218 | 1/218 |
| any-danger miss (secondary) | 6/295 (2.0 %) | 6/295 (2.0 %) | **1/295 (0.3 %)** |
| **true false-stop (confirmed clear path)** | **0/150** | **0/150** | **0/150** |
| unnecessary OBSERVE (confirmed clear) | 0/150 | 0/150 | 0/150 |
| UNKNOWN on confirmed clear | 0/150 | 0/150 | 0/150 |
| guidance on confirmed clear | 150/150 (100 %) | 150/150 (100 %) | 150/150 (100 %) |
| guidance coverage, all frames | 190/481 (39.5 %) | 190/481 (39.5 %) | 178/481 (37.0 %) |
| guidance coverage, non-danger frames | 184/186 (98.9 %) | 184/186 (98.9 %) | 177/186 (95.2 %) |
| UNKNOWN share | 0/481 | 0/481 | 1/481 (0.2 %) |
| OBSERVE share | 0/481 | 26/481 (5.4 %) | 46/481 (9.6 %) |
| BLOCKED share | 291/481 (60.5 %) | 265/481 (55.1 %) | 256/481 (53.2 %) |
| decision flips | 113/478 (23.6 %) | 124/478 (25.9 %) | 133/478 (27.8 %) |
| flips per minute | 42.6 | 46.7 | 50.1 |
| hazard onsets / warned within 10 frames | 47 / 47 | 47 / 47 | 46 / 47 |
| latency p50 / p95 (frames) | 0 / 0 | 0 / 0 | 0 / 0 |

GT denominators (annotated, all arms identical): road-in-path 15, curb-in-path 84,
ground-danger 103, occupancy-in-path 218, any-danger 295, confirmed-clear 150.

Reading: with the best inputs, the split restores the correct semantics (B moves 26 STOPs to
OBSERVE with identical coverage) and the spatial fusion **removes the remaining dangerous passes**
(occupancy miss 6→1, STRAIGHT-only 4→0) and no arm stops on a confirmed clear path. Coverage does not rise because the
151 confirmed-clear frames already get guidance (99.3 %) in every arm: the low all-frame coverage
is *correct blocking* of 218 GT occupancy frames (deep vegetation/trees/objects along a narrow
SANPO sidewalk), not over-conservatism. That is the operator's point quantified: the old "49.7 %
false stop" was mostly right behaviour.

### 4.2 `production` — v5 SegFormer-B0 + Depth-Anything-V2-Small + existing calibration

| metric | arm A (PR #17) | arm B (split) | arm C (split + spatial) |
|---|---:|---:|---:|
| road danger miss (conservative) | 1/15 (6.7 %) | 1/15 (6.7 %) | 1/15 (6.7 %) |
| curb danger miss (conservative) | 13/84 (15.5 %) | 13/84 (15.5 %) | **11/84 (13.1 %)** |
| curb miss, STRAIGHT-only | 5/84 (6.0 %) | 5/84 (6.0 %) | 4/84 (4.8 %) |
| ground danger miss | 15/103 (14.6 %) | 15/103 (14.6 %) | 13/103 (12.6 %) |
| **3D occupancy miss (conservative)** | 107/218 (49.1 %) | 108/218 (49.5 %) | **104/218 (47.7 %)** |
| 3D occupancy miss, STRAIGHT-only | 75/218 (34.4 %) | 75/218 (34.4 %) | 74/218 (33.9 %) |
| occupancy turn-guidance NOT_EVALUABLE | 32/218 | 33/218 | 30/218 |
| any-danger miss (secondary) | 126/295 (42.7 %) | 127/295 (43.1 %) | 120/295 (40.7 %) |
| **true false-stop (confirmed clear path)** | **0/150** | **0/150** | **0/150** |
| unnecessary OBSERVE (confirmed clear) | 0/150 | 0/150 | 0/150 |
| UNKNOWN on confirmed clear | 43/150 (28.7 %) | 43/150 (28.7 %) | 67/150 (44.7 %) |
| guidance on confirmed clear | 107/150 (71.3 %) | 107/150 (71.3 %) | 83/150 (55.3 %) |
| guidance coverage, all frames | 255/481 (53.0 %) | 256/481 (53.2 %) | 224/481 (46.6 %) |
| guidance coverage, non-danger frames | 129/186 (69.4 %) | 129/186 (69.4 %) | 104/186 (55.9 %) |
| UNKNOWN share | 124/481 (25.8 %) | 123/481 (25.6 %) | 200/481 (41.6 %) |
| OBSERVE share | 0/481 | 41/481 (8.5 %) | 45/481 (9.4 %) |
| BLOCKED share | 102/481 (21.2 %) | 61/481 (12.7 %) | 12/481 (2.5 %) |
| decision flips | 197/478 (41.2 %) | 210/478 (43.9 %) | 206/478 (43.1 %) |
| flips per minute | 74.2 | 79.1 | 77.6 |
| hazard onsets / warned within 10 frames | 47 / 35 (74.5 %) | 47 / 35 (74.5 %) | 47 / 36 (76.6 %) |
| latency p50 / p95 (frames) | 1 / 9 | 1 / 9 | 1 / 8.25 |

Reading: under the production input chain the split + spatial fusion still reduces dangerous passes
in the right direction (curb 13→11, occupancy 107→104, any-danger 126→120, warnings within
horizon 35→36/47) but the change is small: **the remaining dangerous passes are not decision-layer
misses — they are input-chain misses.** §5 attributes each missed GT pixel to the layer.

Arm A production numbers differ slightly from the committed PR #17 markdown table (any-danger miss
126/295 here vs 130/295 there): arm A is the same policy code and reproduces PR #17's committed
per-frame rows exactly on the annotated condition (0/481 mismatches), but production uses
non-bit-deterministic MPS model inference across runs. All A/B/C comparisons above are within one
run on identical inputs, so the ablation is valid.

### 4.3 Per-session grouping (headline metrics)

`annotated`:

| session | arm | occupancy miss | true false-stop | guidance coverage | UNKNOWN share | flips |
|---|---|---:|---:|---:|---:|---:|
| `-PqSDmiE…` (121) | A / B / C | 0/24 / 0/24 / 0/24 | 0/61 / 0/61 / 0/61 | 78/121 / 78/121 / 78/121 | 0 / 0 / 1 | 24/120 / 25/120 / 26/120 |
| `M5UIabs…` (184) | A / B / C | 6/91 / 6/91 / **1/91** | 0/76 each | 98/184 / 98/184 / 87/184 | 0 / 0 / 0 | 74/183 / 74/183 / 70/183 |
| `UGNwBEZ…` (176) | A / B / C | 0/103 / 0/103 / 0/103 | 0/13 / 0/13 / 0/13 | 14/176 / 14/176 / 13/176 | 0 / 0 / 0 | 15/175 / 25/175 / 37/175 |

`production` (same columns; `true false-stop` denominator is that session's confirmed-clear
frames; a hyphen = NOT_EVALUABLE in that session):

| session | arm | occupancy miss | curb miss | true false-stop | guidance coverage | UNKNOWN share | warned within horizon |
|---|---|---:|---:|---:|---:|---:|---:|
| `-PqSDmiE…` (121) | A | 7/24 | 4/6 | 0/61 | 51/121 | 52/121 | 8/8 |
| `-PqSDmiE…` | B | 7/24 | 4/6 | 0/61 | 51/121 | 52/121 | 8/8 |
| `-PqSDmiE…` | C | **5/24** | **2/6** | 0/61 | 31/121 | 74/121 | 8/8 |
| `M5UIabs…` (184) | A / B / C | 82/91 each | – | 0/76 each | 161 / 161 / 152 (of 184) | 23 / 23 / 32 (of 184) | 20/31 / 20/31 / 21/31 |
| `UGNwBEZ…` (176) | A / B / C | 18/103 / 19/103 / **17/103** | 9/78 each | 0/13 each | 43 / 44 / 41 (of 176) | 49 / 48 / 94 (of 176) | 7/8 each |

Two distinct failure pictures: session `M5UIabs…` is a dense-vegetation sidewalk where the v5
mask loses 82/91 GT occupancy frames in **all** arms (input chain, §5); session `-PqSDmiE…` is
where the spatial fusion actually helps (occupancy 7→5, curb 4→2) but at an UNKNOWN/coverage
cost. `UGNwBEZ…` shows the same trade at small scale.

Exact per-session numerator/denominator rows for every metric and arm are in
`metrics.csv` (scope = session) and `summary.md`.

## 5. Failure attribution — which layer owns the remaining dangerous passes

`pc/tools/responsibility_split_attribution.py` re-runs the production input chain on exactly the
frames where arm C emitted forward guidance over GT road/curb/3D-occupancy danger, and classifies
every missed GT pixel:

* **segmentation** — the v5 5-class mask calls the GT danger pixel SAFE;
* **geometry** — v5 calls it non-walkable but the metric depth / ground calibration does not place
  it at the expected body-swept position (distance / height band);
* **decision** — v5 non-walkable **and** a body-swept obstacle-height point exists at the GT pixel,
  so the decision layer had the evidence and still passed. This is the only layer this PR owns.

Result (production inputs, arm C, 110 frames that emitted forward guidance over GT danger;
108 attributed, 2 skipped because the fresh production ground calibration failed):

| danger kind | GT pixels | segmentation | geometry | decision |
|---|---:|---:|---:|---:|
| 3D occupancy | 14 909 | 5.8 % | **86.1 %** | 8.1 % |
| curb | 893 | **44.0 %** | 55.7 % | 0.3 % |
| road | 5 | 100 % | 0 % | 0 % |

Class distribution at those missed GT pixels: occupancy `non` 14 042 / `safe` 867 (so the v5 mask
*does* call 94.2 % of the missed occupancy pixels non-walkable); curb `non` 500 / `safe` 393.

Interpretation: after the structural change, the residual dangerous passes are **not** decision
layer losses. 3D-occupancy misses are 86.1 % a **geometry / spatial-perception** loss: the class
mask marks the pixel non-walkable, but Depth-Anything-V2 + the production ground calibration (with
SANPO-Real's documented 12–14° pose-derived gravity error) does not place that point inside the
1.5–6.0 m × ±0.4 m × 0.15–2.0 m body volume. Curb misses split between segmentation
(44.0 % predicted SAFE = curb-as-walkable) and the same geometry loss (55.7 %). Road misses in this
sample are tiny (5 GT pixels in 1 frame) and are segmentation (Road-as-Sidewalk). The decision
layer owns 8.1 % of occupancy pixels / 0.3 % of curb pixels, i.e. **the next change belongs in
segmentation and metric geometry, not in another decision-state iteration.** This matches the
operator's instruction: if dangerous passes do not fall, attribute them to segmentation / spatial
perception rather than tuning the state machine again.

## 6. Regression (explicit, not one-sided)

* **Arm C loses guidance coverage vs A under production**: 255→224 frames (53.0 %→46.6 %), and
  UNKNOWN share rises 124→200 (25.8 %→41.6 %). This is the spatial depth-quality gate refusing to
  pass on reused/degraded calibration and on first-frame (not yet continued) detections — it is
  "insufficient evidence => UNKNOWN", not a debounce trick. On confirmed-clear frames C still withholds
  guidance on 67/150 (A: 43/150); that cost is real and is not hidden.
* **Decision flips rise** (annotated 113→133, production 197→206): more states (OBSERVE) and
  first-frame OBSERVE→BLOCKED transitions. Flips per minute 42.6→50.1 (annotated) and 74.2→77.6
  (production).
* **Turn-guidance cases remain NOT_EVALUABLE**: 30/218 occupancy events in production end with
  LEFT/RIGHT guidance over a GT obstacle in the current sweep; single-frame data cannot decide
  whether that turn is safe, so it is neither counted as a miss nor as correct.
* **Curb/road miss barely improves** under production (13→11/84): the residual is dominated by the
  segmentation/perception layer (§5), not the decision policy.
* **Latency does not improve materially** (p50 1 frame, p95 9→8.25 frames); the data has 0.33 s
  frame spacing, not phone latency.
* Arm B is a semantics-only change (STOP→OBSERVE split) with identical coverage and identical
  blocking decisions — it does not restore guidance by itself. That is the honest split-only result.

## 7. Independent re-test on the local annotated phone clips

`pc/tools/phone_clip_retest.py`, 35 clips in `~/Desktop/盲行导航/视频标注`, none used for design.
Labels are video-level (`safe`/`near_miss`/`conflict`, target category, approximate onset time,
free text). **Label capability is stated before any number:**

| metric | status | why |
|---|---|---|
| curb/road danger miss | **NOT_EVALUABLE** | no per-frame curb/road segmentation/geometry GT |
| 3D occupancy danger miss | **NOT_EVALUABLE** | no per-frame 3D occupancy / object boxes |
| true false-stop (confirmed clear path) | **NOT_EVALUABLE** | no frame-level clear-path GT |
| latency to first hazard warning | **NOT_EVALUABLE** | only one coarse video-level onset for 4 clips |
| effective guidance coverage | **PROXY** | GT-free output share under assumed straight corridor (no IMU/pose/VIO) |
| UNKNOWN share | **PROXY** | same assumption |
| decision flips | **PROXY** | same assumption |

Result (610 sampled frames at 2 fps; 31 safe, 3 near-miss, 1 conflict clips), **PROXY only**:

| arm | guidance coverage | warning share | UNKNOWN share | OBSERVE share | BLOCKED share | flips |
|---|---:|---:|---:|---:|---:|---:|
| A (PR #17) | 130/610 (21.3 %) | 480/610 (78.7 %) | 65/610 (10.7 %) | 0 | 415/610 (68.0 %) | 92/575 |
| B (split) | 130/610 (21.3 %) | 480/610 (78.7 %) | 65/610 (10.7 %) | 223/610 (36.6 %) | 192/610 (31.5 %) | 128/575 |
| C (spatial) | 129/610 (21.1 %) | 481/610 (78.9 %) | 90/610 (14.8 %) | 244/610 (40.0 %) | 147/610 (24.1 %) | 145/575 |

On the human-labelled **safe** clips the proxy warning share is 79.6 % (A/B) and 79.8 % (C); the
four near-miss/conflict clips warn at or before the coarse note time (descriptive only).

Honest reading: the independent re-test **did not restore usable guidance** and cannot validate
safety. The fixed-pitch/height ground calibration itself *succeeded on 610/610* frames, so the low
coverage is not a calibration failure: the decision layers block on evidence inside the
assumed body swept band (parked bikes/curbs/road edges that these walkers safely passed to the
side), and C's spatial gating mostly converts BLOCKED to OBSERVE (blocked share 68.0 %→24.1 %,
OBSERVE 0→40.0 %) **without restoring forward guidance** (21.3 %→21.1 %). That is precisely the
shift Issue #18 warned not to mistake for a fix. With no IMU/pose/VIO the corridor is a fixed
straight-line assumption (heading/walker drift unverified), so the proxy cannot separate "policy
too conservative" from "missing pose makes the body sweep point the wrong way"; it is
NOT_EVALUABLE as a usability or safety result. The required next step is a device capture with
IMU/pose + per-frame labels (operator action), which this desktop ticket cannot substitute.

No device or blind-user claim is made from these clips.

## 8. NOT_EVALUABLE / not connected

* Device behaviour, phone IMU, real blind-user usefulness or acceptance: **NOT_EVALUABLE**.
* The obstacle node in the Android app (`app/`): **NOT_CONNECTED** — this PR changes PC-side
  research code only.
* Production curb recall gate, segmentation retraining, DAV2 replacement: not attempted, not
  evaluated.
* A truly fresh real SANPO-Real hold-out session: no fourth session exists locally; the per-session
  grouping is the validation view. The local phone clips are independent but GT-free for safety.

## 9. Reproducibility

```bash
cd pc
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 .venv/bin/python \
  tools/responsibility_split_eval.py \
  --v5-checkpoint /Users/mima0000/blindnav-real-eval/models/segformer_sanpo_b0_v5.pt \
  --out out/responsibility_split --save-failures 10
# independent clips (no pose/IMU; assumed straight corridor, fixed 15 deg pitch, 1.30 m height)
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 .venv/bin/python tools/phone_clip_retest.py \
  --v5-checkpoint /Users/mima0000/blindnav-real-eval/models/segformer_sanpo_b0_v5.pt \
  --out out/phone_retest
# layer attribution for arm C dangerous passes under production inputs
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 .venv/bin/python \
  tools/responsibility_split_attribution.py \
  --per-frame out/responsibility_split/per_frame.csv \
  --v5-checkpoint /Users/mima0000/blindnav-real-eval/models/segformer_sanpo_b0_v5.pt \
  --out out/responsibility_split/attribution
```

Regression: `cd pc && .venv/bin/python -m pytest -q` — see PR body for the count.

## 10. Remaining risks

* Perception is still the bottleneck for real deployment: the v5 5-class mask + DAV2 + ground
  calibration cannot see a large share of GT 3D occupancy in the body swept path (§5), so no
  decision-layer change can recover those dangerous passes.
* The spatial obstacle constants (physical extent ≥0.08 m / ≥0.15 m, 2-of-3 continuity, 1.5–6.0 m,
  0.15–2.0 m height) are a priori body/sensor-scale constants, not tuned on this eval set; a
  different phone mounting height/pitch would shift distances and needs the existing IMU/height
  calibration path, not new thresholds.
* The depth-quality gate raises UNKNOWN under degraded calibration; that is safe but reduces
  usable guidance, and a real user experience study is needed to judge the trade-off.
* Arm C's 3D occupancy is point-cloud/component based; crowded scenes and thin poles within the
  component-size floor remain at risk.
* Nothing here is device-verified; the cane remains the near-field safeguard.
