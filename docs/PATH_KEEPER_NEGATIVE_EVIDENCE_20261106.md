# P1 Path Keeper — negative-evidence fusion in the corridor decision layer (A/B/C on SANPO-Real)

Ticket: Issue #16, "P1 Path Keeper: implement negative-evidence fusion in the corridor decision
layer". Date: 2026-11-06. Branch: `agent/negative-evidence-fusion-9bf4ca1477ca`, stacked on PR #15
head and opened against `feature/shadowwalk-protocol` (open for operator adoption; never merged by
the agent). Evidence level: **DESKTOP_REAL_DATA** — offline PC run on SANPO-Real frames, CPU/MPS.
Not device-verified, not user-tested. BlindNav is not a certified mobility aid; nothing here is a
safety claim.

## 1. Problem

PR #15 showed that the corridor decision layer consumes only walkable points: a correctly excluded
road or curb contributes nothing to the decision, so it cannot raise UNKNOWN/STOP. With annotated
segmentation and reference geometry the corridor still emitted walkable guidance while GT road or
curb lay inside the swept-body corridor in **23/481 frames** (21 curb + 2 road). That is a
representation defect in the decision layer, not a segmentation/threshold problem: with perfect
inputs the truth was already lost by the time the decision was made. This ticket implements the
change PR #15 recommended and measures it before/after on the same frames.

## 2. Evidence before (PR #15, unchanged, all existing repository reports)

* Same 481 SANPO-Real frames (three sessions: `-PqSD…` 121, `UGNw…` 176, `M5UI…` 184), chest
  camera, every 5th frame.
* Production geometry (DAV2 + pose-derived gravity + ground calibration): arm A annotated
  segmentation emitted walkable guidance while GT road/curb was inside the swept path in
  `road-in-path 2 + curb-in-path 24` frames; UNKNOWN `88/481 (18.3 %)`; corridor coverage
  `394/481 (81.9 %)`.
* Oracle geometry (GT depth + GT plane, diagnostic only): annotated segmentation failed on 25
  frames — **21 curb-in-path + 2 road-in-path + 2 rotation**; the band construction (PR #15 arms
  C/D) did not remove that class (24–29 curb-in-path frames) and cost 303/481 band-driven
  corrections and command agreement down to 68.2 %.
* v5 segmentation labelled 51.6 % of near-field GT road ground points walkable (Road-as-Sidewalk),
  which is a segmentation-layer defect this ticket does not retrain; it bounds what the production
  arm can achieve.

Falsifier stated by PR #15: if annotated segmentation + oracle geometry produced zero road/curb-in-
swept-path frames, the representation would not be losing truth. It produced 23, so the change was
justified.

## 3. Change (what was built)

Two files change the corridor decision layer; two new tools measure it; nothing else in the
production pipeline changes.

**`pc/blindnav_pc/corridor_evidence.py` (new).** An explicit four-channel evidence structure for
the corridor map frame (x right, z forward, metres):

| channel | definition |
|---|---|
| `walkable` | ground points (|height| <= 0.15 m) whose mask says walkable |
| `hazard` | curb/boundary-class ground points + blocking obstacle points (0.15 m < height <= 2.0 m) that the mask does not call walkable |
| `non_walkable` | excluded ground points (road, terrain, grass/other excluded ground classes) |
| `unknown` | unlabelled ground + geometric conflicts (mask says walkable but the point is not ground) |

`build_corridor_evidence(role_map, depth, K, up, camera_height_m, pose, ...)` back-projects the
role map with the existing `depth_to_level_points` helper and returns the four map-frame point
sets. Its `walkable` channel is row-for-row identical to the existing
`mask_to_map_points` output, so arm B/C do not change the walkable input — they only add
counter-evidence. The existing class taxonomy is reused unchanged (v5 ids 0 ignore / 1 SAFE /
2 BOUNDARY / 3 CONDITIONAL / 4 NON; SANPO GT is remapped with the same `gt_remap` used by PR #15),
so `annotated` and `production` conditions share one evidence definition and are comparable.

**`pc/blindnav_pc/shadowwalk.py` (edit).** `ShadowWalkTracker` gains
`update_evidence(evidence, pose)`; `update(points, pose)` now delegates to
`update_evidence(walkable_only(points), pose)` and is regression-tested to be byte-identical to the
old behaviour. The new decision rule (a priori constants, not tuned on the eval set):

* **Swept-body gating.** The body rectangle is derived from the current heading (`Pose2D` yaw),
  human body width (`body_width_m = 0.5 m`, the existing `PlannerConfig.body_width_m`), a lateral
  safety margin (`body_margin_m = 0.15 m`, the existing `PlannerConfig.margin_m`), and the real
  forward range of the guidance (`near_m..far_m = 1.5–6.0 m`, existing). A negative point counts
  only if it is inside that rectangle; the same threshold as the existing "a bin counts as seen"
  support (`points_per_bin = 5`) decides whether a forward bin is credible. Road beside the
  pavement is therefore outside the body sweep and does not stop the walker.
* **Credible blocking evidence** (a swept-body bin with >= 5 hazard or non-walkable points) raises
  `STOP` with the new explainable reason codes `hazard_in_path` / `non_walkable_in_path` **before**
  the walkable-quantity branches, so enough walkable points cannot override it.
* **UNKNOWN on low confidence / conflict.** Credible unknown-channel support raises
  `unknown_evidence`; negative evidence that is present but not credible in a bin where walkable
  support is also below the support threshold raises `evidence_conflict`. Both were previously
  treated as Safe.
* **Temporal confirmation (arm C).** `temporal_confirm_frames = 3` (provenance:
  `LayerConfig.drop_confirm_frames = 3`; SANPO frames are 0.33 s apart, so ~1 s persistence):
  blocking requires 3 consecutive analysed frames with credible blocking evidence; earlier frames
  report UNKNOWN with `hazard_confirming` / `non_walkable_confirming`.
* All existing commands (LEFT/RIGHT/STRAIGHT/STOP/UNKNOWN), hint texts, reason codes, the
  `off_corridor`/`walkable_unseen`/`corridor_unseen`/`yaw_too_large`/`lateral`/`yaw`/`centred`
  branches and the STOP/MotionAuthority/ArrivalAuthority policies are unchanged.

**Measurement tools (new, no algorithm side effects).**

* `pc/tools/corridor_evidence_eval.py` runs A/B/C on the same 481 frames and the same
  corridor/pose setup as PR #15, under two input conditions:
  * `annotated`: GT SANPO segmentation (remapped exactly like the v5 training labels) + reference
    geometry (GT depth, GT ground-plane up/height). This proves the logic with the best inputs the
    segmenter can ever provide; reference geometry is a diagnostic, never a system input.
  * `production`: v5 SegFormer-B0 SANPO fine-tune + Depth-Anything-V2-Small + the existing
    `GroundCalibrator` with pose-derived gravity (the deployment-like chain). One calibrator per
    session, shared by all arms.
  GT scoring is computed once per frame with oracle geometry and raw SANPO ids, so every arm and
  condition has identical denominators. It reports primary GT **excluded-ground danger**
  (road/curb/water/inaccessible/railway/terrain >= 5 points in the decision swept body) and a
  secondary **any-negative danger** that also counts GT object/vegetation occupancy
  (0.15 m < height <= 2.0 m) in the same body; both are reported side by side so no safety number
  is one-sided.
* `pc/tools/corridor_evidence_report.py` turns the frozen eval output into per-condition A/B/C
  tables with numerator/denominator for every metric, the fixed PR #15 23-frame ledger
  (denominator 23; the set is a reporting label only and is never fed to the algorithm), per-session
  breakdowns, the B-vs-A / C-vs-B / C-vs-A deltas, and at least five remaining failure frames with
  raw RGB, annotated overlay and a mechanical failure-layer label.

## 4. Data and conditions

* `pc/data/sanpo_real/sanpo-real` — all three sessions, all downloaded frames, chest camera, every
  5th frame: **481 frames** total (`-PqSD…` 121, `UGNw…` 176, `M5UI…` 184). 2.0 GB local, already
  present; no synthetic data, no missing data, no new download.
* v5 checkpoint: local `blindnav-real-eval/models/segformer_sanpo_b0_v5.pt` (not committed; `*.pt`
  is gitignored).
* Fixed seeds, no sampling; the same frames, order, corridor config and inputs for all three arms
  and both conditions. Arm A under the annotated condition is byte-identical to PR #15's
  oracle/A rows on all 481 frames (hint, road-in-path and curb-in-path counts), which is the
  regression anchor that this is the same measurement, not an easier dataset.
* The decision rule and every constant were frozen in the branch before the official run; no
  parameter or threshold was tuned against measured results. The input-condition split and the
  per-session breakdown below are the validation slices; a truly independent real clip and any
  device/user truth are NOT_EVALUABLE here.

## 5. Results

All numbers below are from one deterministic run over the same 481 frames and identical
inputs/denominators per condition. Machine-readable: `summary.json`, `metrics.csv`,
`per_frame.csv` (2 886 rows = 481 frames x 2 conditions x 3 arms) and `report_tables.md/.csv`
in `docs/assets/path_keeper_negative_evidence_20261106/`. `pr15_23` was recovered as exactly 23
frames and verified on the full run; PR #15 in-path event frames = 114.

Two GT danger denominators are reported side by side because a single one would be one-sided:

* **primary / GT excluded-ground danger** — the PR #15 first-loss class: GT non-walkable ground
  points (road, curb, water, inaccessible, railway, terrain; raw SANPO ids) >= 5 in the decision
  swept body. 103/481 frames.
* **secondary / GT any-negative danger** — primary plus GT object/vegetation occupancy
  (0.15 m < height <= 2.0 m) and GT non-walkable-class ground >= 5 in the swept body. 295/481
  frames.

### 5.1 `annotated` condition (GT segmentation + reference geometry — proves the logic)

| arm | false-free ground | false-free any | false-stop ground | false-stop any | UNKNOWN | guidance coverage | 23-frame W/S/U |
|---|---:|---:|---:|---:|---:|---:|---:|
| A | 92/103 (89.3 %) | 284/295 (96.3 %) | 0/378 (0.0 %) | 0/186 (0.0 %) | 11/481 (2.3 %) | 470/481 (97.7 %) | 23/0/0 |
| B | **6/103 (5.8 %)** | **21/295 (7.1 %)** | 177/378 (46.8 %) | 0/186 (0.0 %) | 0/481 (0.0 %) | 207/481 (43.0 %) | **3/20/0** |
| C | **6/103 (5.8 %)** | 21/295 (7.1 %) | 118/378 (31.2 %) | 0/186 (0.0 %) | 75/481 (15.6 %) | 207/481 (43.0 %) | 3/16/4 |

Other measured cells: danger blocking recall on the ground-danger frames **A 11/103 (10.7 %)
-> B 97/103 (94.2 %) -> C 97/103 (94.2 %)** (C: 81 STOP + 16 UNKNOWN); PR #15-gate false-free
78/114 (68.4 %) -> 3/114 (2.6 %); mean walkable coverage unchanged at 0.96 and coverage>=min
unchanged 470/481; road-in-path ignored 2/5 -> 0/5 and curb-in-path ignored 21/109 -> 3/109
(PR #15 0.6 m definitions, STRAIGHT only); action flips vs A 274/481 (57.0 %) for B, 273/481
(56.8 %) for C.

### 5.2 `production` condition (v5 + DAV2 + existing ground calibration)

| arm | false-free ground | false-free any | false-stop ground | false-stop any | UNKNOWN | guidance coverage | 23-frame W/S/U |
|---|---:|---:|---:|---:|---:|---:|---:|
| A | 47/103 (45.6 %) | 208/295 (70.5 %) | 0/378 (0.0 %) | 0/186 (0.0 %) | 107/481 (22.2 %) | 374/481 (77.8 %) | 20/0/3 |
| B | **21/103 (20.4 %)** | **134/295 (45.4 %)** | 47/378 (12.4 %) | 14/186 (7.5 %) | 138/481 (28.7 %) | 257/481 (53.4 %) | **8/6/9** |
| C | **21/103 (20.4 %)** | 134/295 (45.4 %) | 25/378 (6.6 %) | 4/186 (2.2 %) | 177/481 (36.8 %) | 257/481 (53.4 %) | 8/3/12 |

Other measured cells: danger blocking recall on ground-danger frames **A 56/103 (54.4 %) ->B
82/103 (79.6 %) -> C 82/103 (79.6 %)** (C: 22 STOP + 60 UNKNOWN); PR #15-gate false-free
37/114 (32.5 %) -> 14/114 (12.3 %); mean coverage unchanged 0.57 and coverage>=min unchanged
375/481; road-in-path ignored 2/5 -> 2/5, curb-in-path ignored 15/109 -> 10/109; action flips vs A
151/481 (31.4 %) for B, 136/481 (28.3 %) for C.

### 5.3 PR #15 regression frames (fixed 23 frames, denominator 23, never fed to the algorithm)

| condition | arm | of 23: walkable guidance | STOP | UNKNOWN |
|---|---|---:|---:|---:|
| annotated | A | 23 | 0 | 0 |
| annotated | B | **3** | 20 | 0 |
| annotated | C | **3** | 16 | 4 |
| production | A | 20 | 0 | 3 |
| production | B | **8** | 6 | 9 |
| production | C | **8** | 3 | 12 |

With annotated inputs the representation defect is closed for 20/23 frames in B (3 remain
walkable because their only GT ground-danger points fall in the 5.5–6.0 m tail of the forward
range, which the inherited integer-bin counters do not bin — see Remaining risks). Under
production inputs 8/23 remain walkable, all with zero binned negative evidence in the v5 mask
(segmentation layer, v5 Road-as-Sidewalk) plus the same bin-tail case.

### 5.4 Temporal confirmation (C vs B)

Temporal confirmation does not change false-free at all (it cannot make B stricter): annotated
6/103 -> 6/103, production 21/103 -> 21/103. It reduces STOP and moves the difference into
UNKNOWN: annotated STOP 274 -> 199 with UNKNOWN 0 -> 75, false-stop ground 177/378 (46.8 %) ->
118/378 (31.2 %); production STOP 86 -> 47 with UNKNOWN 138 -> 177, false-stop ground 47/378
(12.4 %) -> 25/378 (6.6 %). Guidance coverage is unchanged in both conditions because blocked
frames become UNKNOWN instead of walkable.

### 5.5 Per-session validation slice (including the session with no GT road/curb danger)

The rule and every constant were frozen before this run; no threshold was changed in response to
these numbers. The per-session split is the available validation view (all cells in
`report_tables.md` §2):

* `-PqSD…` (121 frames, the only session with GT road points): annotated A false-free ground
  10/21 -> B 3/21, C 3/21; B STOP 34/121, C STOP 20 + 14 UNKNOWN.
* `UGNw…` (176 frames, curb-heavy): annotated A false-free ground 82/82 (100 %) -> B 3/82,
  C 3/82; but B STOP 158/176 and guidance coverage 18/176 — the over-conservative cost is
  concentrated here.
* `M5UI…` (184 frames, **zero** GT road/curb ground-danger frames — the PR #15 clean session):
  annotated A false-free 0/0, guidance 184/184; B still STOPs 82/184 (44.6 %) and loses guidance
  on those frames even though there is no ground danger at all; all 82 STOPs are carried by
  object/vegetation occupancy (danger_any) evidence. This is the clearest validation of the cost
  findings: the false-stop inflation is not a ground-truth road/curb artefact.
* A genuinely fresh fourth real clip was not available: the raw SANPO-Real download has more
  sessions but no preprocessed depth/segmentation/pose in this repository, and adding one was not
  possible inside this ticket. A truly independent real clip and device/user truth are
  **NOT_EVALUABLE**.

## 6. Remaining failing frames

Ten frames from both conditions and both dimensions are committed with raw RGB and annotated
overlay in `docs/assets/path_keeper_negative_evidence_20261106/failures/`
(`failures.json` has the machine-readable manifest, counts and layer label; the fixed 23-frame
set is never special-cased). Overlay legend: green = GT walkable, red = GT hazard/non-walkable,
yellow = decision swept body (1.5–6 m, ±0.4 m from the current heading).

| # | kind | condition / arm | session | frame | hint / reason | layer | what still fails |
|---|---|---|---|---:|---|---|---|
| f01 | false-free (ground) | annotated / B | `-PqSD…` | 155 | RIGHT / lateral | decision | 24 GT curb points in the swept body but they sit at 5.55–6.0 m; the inherited 4-bin counters (`round(4.5)=4` bins) do not bin that tail, so hazard support is 0 |
| f02 | false-free (ground) | annotated / B | `UGNw…` | 145 | RIGHT / lateral | decision | 6 GT curb points, same 5.55–6.0 m tail; PR #15 0.6 m event is also false (below its 2.5 m curb start), so this frame is not in the 23 |
| f03 | false-free (ground) | production / B | `-PqSD…` | 180 | STRAIGHT / centred | segmentation | v5 mask labels the GT curb/road ground walkable (Road-as-Sidewalk), so the evidence layer has nothing to block; v5/D AV2 input limitation |
| f04 | false-free (ground) | production / B | `UGNw…` | 150 | STRAIGHT / centred | segmentation | same: GT curb 5 points inside the swept body, zero binned negative evidence in the v5 mask |
| f05 | false-free (any) | annotated / B | `M5UI…` | 85 | STRAIGHT / centred | decision | 6 GT object-occupancy points at the body edge (3 hazard points, below the 5-point support), so the fused decision keeps walkable guidance — low-support case |
| f06 | false-free (any) | production / B | `-PqSD…` | 10 | RIGHT / lateral | segmentation | 539 GT object/vegetation points in the swept body but the v5 mask produces no binned negative evidence there |
| f07 | false-stop (ground) | annotated / B | `M5UI…` | 50 | STOP / hazard_in_path | decision | no ground danger; 39 object/vegetation points trip the hazard channel and stop the walker — the over-conservative occupancy case |
| f08 | false-stop (ground) | production / B | `UGNw…` | 15 | STOP / hazard_in_path | decision | no ground danger; 126 hazard + 389 unknown points (v5/D AV2 mask/geometry) occupy the swept body |
| f09 | UNKNOWN when GT walkable | annotated / C | `-PqSD…` | 195 | UNKNOWN / hazard_confirming | temporal | persistent curb evidence in the body; C converts the first frames of the block to UNKNOWN instead of STOP (temporal cost) |
| f10 | UNKNOWN when GT walkable | production / B | `M5UI…` | 65 | UNKNOWN / unknown_evidence | segmentation | 377 unknown-channel points (v5 ignore-class / geometric conflict) block confirmation of a GT-walkable surface |

![f07 overlay: accepted stop on object/vegetation occupancy with no GT ground danger](assets/path_keeper_negative_evidence_20261106/failures/f07_false_stop_M5UIabsl_000050_overlay.jpg)

![f01 overlay: GT curb inside the body but in the unbinned 5.5-6.0 m tail](assets/path_keeper_negative_evidence_20261106/failures/f01_false_free_-PqSDmiE_000155_overlay.jpg)

![f03 overlay: production v5 calls the GT curb/road walkable, so there is no counter-evidence](assets/path_keeper_negative_evidence_20261106/failures/f03_false_free_-PqSDmiE_000180_overlay.jpg)

## 7. What improved, what regressed, what remains NOT_EVALUABLE

**Improved (representation defect).** Under annotated segmentation + reference geometry — the best
inputs the segmenter can ever give — the corridor no longer emits walkable guidance while GT
excluded ground is inside the swept body: false-free ground danger 92/103 (89.3 %) -> 6/103
(5.8 %), blocking recall on those frames 10.7 % -> 94.2 %, and the PR #15 23-frame regression set
23 -> 3 remaining walkable (B). The unintended "walkable-only" representation (excluded ground
contributes nothing) is replaced by an explicit four-channel structure with swept-body gating,
UNKNOWN on low confidence/conflict, explainable reason codes and no change to the existing state
machine semantics. Under production inputs the same change still reduces false-free ground danger
45.6 % -> 20.4 % and the 23-frame remain count 20 -> 8; the residual is bounded by the unchanged
segmentation (v5 Road-as-Sidewalk) and by the inherited bin-tail representation.

**Regressed (cost — this is why the PR is not an adoption).** The rule "any credible negative
evidence inside the body sweep escalates" is far too conservative for real walks: guidance
coverage falls 97.7 % -> 43.0 % (annotated) and 77.8 % -> 53.4 % (production), with 274/481
(57.0 %) and 86/481 (17.9 %) STOPs. The dominant cause is not road/curb: it is object/vegetation
occupancy inside the 0.4 m body sweep. The dual denominators expose this exactly — annotated B
false-stop is 177/378 (46.8 %) against GT ground danger but 0/186 against GT any-negative danger,
and the held-out clean session `M5UI…` (zero GT ground danger) still stops 82/184 frames in the
annotated condition. Temporal confirmation (C) is a real but insufficient iteration: it halves the
ground false-stop rate (46.8 % -> 31.2 % annotated, 12.4 % -> 6.6 % production) by turning STOP
into UNKNOWN (UNKNOWN 0 -> 15.6 % annotated, 28.7 % -> 36.8 % production) without changing
false-free or guidance coverage.

**NOT_EVALUABLE.** Device behaviour, phone IMU/camera, real blind-user usefulness and acceptance of
STOP decisions (no device or user session; NOT_CONNECTED), obstacle-node behaviour (separate node;
this ticket does not modify it), an independent real clip (no preprocessed fourth session
available — see §5.5), production curb-recall gate, and Android integration (the change is in the
offline `pc/` corridor decision layer; the Android `app/` pipeline is NOT_CONNECTED to it). The
oracle-geometry condition is a diagnostic, never a system input.

**Interpretation for adoption (human decision).** The measurement supports keeping the evidence
structure and the swept-body/UNKNOWN rules, but not adopting "credible negative evidence in the
body sweep -> STOP" for all four channels. The next iteration should split severity: excluded
*ground* evidence (curb/drop/road) remains the corridor decision's escalation channel; object/
vegetation occupancy should be routed to the obstacle node (which owns approach, persistence and
context) or at most raise UNKNOWN, and the 0.4 m body sweep / 5.5–6.0 m bin tail should be
re-examined. That split was not implemented here because it would add a new public evidence channel
and a product decision on STOP semantics; the PR therefore stays open for operator adoption and is
not merged.

## 8. Regression

* Full `pc/` test suite: **336 passed** (includes the pre-existing ShadowWalk and PR #15 harness
  tests unchanged, plus 16 evidence-module tests, 28 new shadowwalk tests, 25 eval-tool tests and
  28 report-tool tests). Run: `cd pc && .venv/bin/python -m pytest tests/ -q`.
* Arm A under the annotated condition is the unchanged algorithm: its 481 rows match PR #15's
  committed `docs/assets/corridor_truth_20261106/per_frame.csv` oracle/A rows exactly on hint,
  road-in-path and curb-in-path counts (validated by the eval tool's GT-only check and by the
  observed hint counts STRAIGHT 263 / LEFT 125 / RIGHT 82 / UNKNOWN 11, identical to PR #15).
* No production code outside the corridor decision layer changed: `corridor_truth_eval.py`,
  segmentation, depth, ground calibration, the existing pipeline tools and the Android `app/` tree
  are untouched. New code paths are opt-in (`update_evidence`); `update()` legacy behaviour is
  covered by equivalence tests.
* No frozen set, branch or prior report was overwritten; the PR #15 assets remain byte-identical in
  this branch and are only read by the new tool.
* The eval is deterministic (fixed seed, no sampling); runtime ≈2 min for the 481-frame x 2-condition
  run on this machine, report/overlay generation ≈2 s.

## 9. Remaining risks

* **Over-conservative blocking is the headline risk**: 57 % STOP / 43 % guidance coverage
  (annotated) and 17.9 % STOP / 53.4 % guidance (production). Do not deploy; the severity split in
  §7 is the required next iteration.
* **Swept-body constants** are a priori choices reused from existing config (`PlannerConfig`
  body width/margin; `near/far`; `points_per_bin`); they were not swept or tuned. The 0.4 m half
  width is narrower than PR #15's 0.6 m event band, and the 5.5–6.0 m tail is not binned because the
  inherited `round((far-near)/bin)=4` bin count is kept unchanged to keep arm A comparable
  (2/6 annotated residual false-free frames are exactly this tail). Fixing it would change the
  baseline too and was left out of scope.
* **The hazard/occupancy channels include object and vegetation points** (raw GT ids and the v5
  NON class), which is what makes the corridor STOP on ordinary sidewalk encounters. The evidence
  builder intentionally overlaps ignore-class obstacle pixels in `hazard` and `unknown`
  (spec-literal); this can only make the conservative direction slightly more conservative in the
  production condition.
* **Production inputs are segmentation-bounded**: v5 Road-as-Sidewalk means the road channel often
  has no walkable counter-evidence to fuse; 20 of 21 production false-free ground frames have zero
  binned negative evidence. No segmentation retraining was done (hard constraint).
* **Production A calibration note**: for a fair A/B/C comparison all three production arms share one
  `GroundCalibrator` per session (deployment-like: calibrated on the v5 walkable mask), so
  production/A is not byte-identical to PR #15's per-arm-calibrated production/A. Annotated/A *is*
  byte-identical (this is the regression anchor).
* **Temporal confirmation across calibration gaps**: frames without metric geometry return
  `UNKNOWN no_metric_depth` without invoking the tracker, so a C block streak is not reset by a gap
  (documented implementation note; no effect on centreline poses here).
* **Bin-tail/rounding and integer bin boundaries** are inherited from the baseline; they are
  reported rather than silently changed.
* **True independent validation, device verification and blind-user testing are NOT_EVALUABLE**;
  all numbers are DESKTOP_REAL_DATA on SANPO-Real (chest-mounted ZED VIO pose used as an IMU proxy,
  recorded VIO route used as the corridor centreline). BlindNav is not a certified mobility aid and
  nothing here is a safety claim.

## 10. Reproduction

```bash
cd pc
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
.venv/bin/python tools/corridor_evidence_eval.py \
    --v5-checkpoint /path/to/segformer_sanpo_b0_v5.pt \
    --data-root data/sanpo_real/sanpo-real \
    --out out/corridor_evidence_full
.venv/bin/python tools/corridor_evidence_report.py \
    --out-dir out/corridor_evidence_full --out out/corridor_evidence_report \
    --save-failures --data-root data/sanpo_real/sanpo-real
.venv/bin/python -m pytest tests/ -q
```
