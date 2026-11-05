# A/B/C corridor negative-evidence evaluation — machine summary

Frames: 481 across 3 SANPO-Real sessions (-PqSDmiEe2pXjmYHgxh4YEBsj0T5LU10=121, M5UIabsltWCYg1D9LCXLoNPO3bMUo_5-=184, UGNwBEZMxhzlzXVyl8XUfflcvjGwdpA5=176).

Conditions: **annotated** = GT SANPO segmentation remapped to the v5 5-class ids + oracle geometry (GT depth, GT ground-plane up/height); **production** = v5 SegFormer-B0 + Depth-Anything-V2-Small + the existing GroundCalibrator with pose-derived gravity.

Arms: A = current ShadowWalk baseline (walkable points only); B = A + negative-evidence channels (no temporal confirmation); C = B + temporal confirmation (temporal_confirm_frames) (temporal_confirm_frames=3).

Decision swept body for GT scoring: body heading, 1.5-6.0 m, |lateral| <= 0.4 m (body_width/2 + margin); GT danger >= 5 points.

**PRIMARY danger = GT non-walkable ground** (road/curb/water/inaccessible/rail/terrain) in the swept body - the PR #15 first-loss / false-safe class. **SECONDARY danger = any GT danger** (primary + object occupancy + non-walkable-class ground points). Both denominators are shown side by side: no one-sided safety number (BLINDNAV_EVAL_RULES).

PR #15 regression set: 23 frames (expected 23; verified=True, scope=full_run). PR #15 road/curb in-path event frames: 114.

Denominators: false-free = GT-danger frames of the same view (ground / any); false-free PR15 gate = PR15 in-path event frames; false-stop = frames with no danger of that view and >= 5 GT walkable points in the swept body; UNKNOWN and guidance coverage = all frames; action flips = all frames of the arm.

Evidence level: **DESKTOP_REAL_DATA** (offline PC, CPU/MPS). Not device-verified, not user-tested. BlindNav is not a certified mobility aid; no safety claim.

## Input condition: annotated

| arm | frames | false-free GROUND (primary) | false-free ANY (secondary) | false-free PR15 gate | false-stop GROUND | false-stop ANY | danger blocking recall GROUND | danger blocking recall ANY |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| A | 481 | 92/103 (89.3%) | 284/295 (96.3%) | 78/114 (68.4%) | 0/378 (0.0%) | 0/186 (0.0%) | 0+11/103 (10.7%) | 0+11/295 (3.7%) |
| B | 481 | 0/103 (0.0%) | 6/295 (2.0%) | 0/114 (0.0%) | 188/378 (49.7%) | 2/186 (1.1%) | 103+0/103 (100.0%) | 289+0/295 (98.0%) |
| C | 481 | 0/103 (0.0%) | 6/295 (2.0%) | 0/114 (0.0%) | 128/378 (33.9%) | 0/186 (0.0%) | 86+17/103 (100.0%) | 214+75/295 (98.0%) |

| arm | UNKNOWN | mean coverage | coverage>=min | guidance coverage | flips vs A |
|---|---:|---:|---:|---:|---:|
| A | 11/481 (2.3%) | 0.961 | 470/481 (97.7%) | 470/481 (97.7%) | 0/481 (0.0%) |
| B | 0/481 (0.0%) | 0.961 | 470/481 (97.7%) | 190/481 (39.5%) | 291/481 (60.5%) |
| C | 77/481 (16.0%) | 0.961 | 470/481 (97.7%) | 190/481 (39.5%) | 290/481 (60.3%) |

| arm | of 23 walkable guidance | of 23 STOP | of 23 UNKNOWN | road-in-path ignored (STRAIGHT) | curb-in-path ignored (STRAIGHT) | unknown when GT walkable | hazard / non-walkable / unknown points | blocking-evidence frames | danger_ground frames | danger_any frames |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| A | 23 | 0 | 0 | 2 | 21 | 0 | 0 / 0 / 0 | 0 | 103 | 295 |
| B | 2 | 21 | 0 | 0 | 2 | 0 | 186294 / 42230 / 1946 | 291 | 103 | 295 |
| C | 2 | 19 | 2 | 0 | 2 | 76 | 186294 / 42230 / 1946 | 291 | 103 | 295 |

## Input condition: production

| arm | frames | false-free GROUND (primary) | false-free ANY (secondary) | false-free PR15 gate | false-stop GROUND | false-stop ANY | danger blocking recall GROUND | danger blocking recall ANY |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| A | 481 | 47/103 (45.6%) | 208/295 (70.5%) | 37/114 (32.5%) | 0/378 (0.0%) | 0/186 (0.0%) | 0+56/103 (54.4%) | 0+87/295 (29.5%) |
| B | 481 | 19/103 (18.4%) | 130/295 (44.1%) | 13/114 (11.4%) | 51/378 (13.5%) | 15/186 (8.1%) | 40+44/103 (81.6%) | 76+89/295 (55.9%) |
| C | 481 | 19/103 (18.4%) | 130/295 (44.1%) | 13/114 (11.4%) | 28/378 (7.4%) | 7/186 (3.8%) | 25+59/103 (81.6%) | 46+119/295 (55.9%) |

| arm | UNKNOWN | mean coverage | coverage>=min | guidance coverage | flips vs A |
|---|---:|---:|---:|---:|---:|
| A | 107/481 (22.2%) | 0.572 | 375/481 (78.0%) | 374/481 (77.8%) | 0/481 (0.0%) |
| B | 139/481 (28.9%) | 0.572 | 375/481 (78.0%) | 251/481 (52.2%) | 157/481 (32.6%) |
| C | 177/481 (36.8%) | 0.572 | 375/481 (78.0%) | 251/481 (52.2%) | 144/481 (29.9%) |

| arm | of 23 walkable guidance | of 23 STOP | of 23 UNKNOWN | road-in-path ignored (STRAIGHT) | curb-in-path ignored (STRAIGHT) | unknown when GT walkable | hazard / non-walkable / unknown points | blocking-evidence frames | danger_ground frames | danger_any frames |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| A | 20 | 0 | 3 | 2 | 15 | 96 | 0 / 0 / 0 | 0 | 103 | 295 |
| B | 8 | 6 | 9 | 2 | 9 | 128 | 18125 / 7879 / 63822 | 91 | 103 | 295 |
| C | 8 | 3 | 12 | 2 | 9 | 166 | 18125 / 7879 / 63822 | 91 | 103 | 295 |

