# Responsibility split A/B/C — rebuilt measurement

Frames: 481 across sessions -PqSDmiEe2pXjmYHgxh4YEBsj0T5LU10 (121), M5UIabsltWCYg1D9LCXLoNPO3bMUo_5- (184), UGNwBEZMxhzlzXVyl8XUfflcvjGwdpA5 (176).
Arms: A = PR #17 as-is: four-channel negative-evidence fusion, no temporal confirmation; B = responsibility split: Path Keeper + Obstacle Guardian (coarse) + Safety Coordinator; C = B + spatial risk fusion (3D occupancy, swept intersection, clearance, continuity).

Evidence level: **DESKTOP_REAL_DATA** (offline SANPO-Real analysis). Not device-verified, not user-tested, no safety claim.

`NOT_EVALUABLE` = the GT data cannot decide that metric for this arm/scope. `low n` = denominator below the small-sample floor. A LEFT/RIGHT guidance while a GT danger lies in the current body sweep is reported as *not-evaluable* rather than counted as a miss: a single frame cannot establish whether the guided turn avoids it (the conservative any-guidance view is kept next to it).

## Condition `annotated`

| metric | arm A | arm B | arm C |
|---|---:|---:|---:|
| curb/road danger miss: `road_miss` | 0/15 (0.0 %) | 0/15 (0.0 %) | 0/15 (0.0 %) |
| curb/road danger miss: `road_miss_straight` | 0/15 (0.0 %) | 0/15 (0.0 %) | 0/15 (0.0 %) |
| curb/road danger miss: `road_turn_guidance_not_evaluable` | 0/15 (0.0 %) | 0/15 (0.0 %) | 0/15 (0.0 %) |
| curb/road danger miss: `curb_miss` | 0/84 (0.0 %) | 0/84 (0.0 %) | 0/84 (0.0 %) |
| curb/road danger miss: `curb_miss_straight` | 0/84 (0.0 %) | 0/84 (0.0 %) | 0/84 (0.0 %) |
| curb/road danger miss: `curb_turn_guidance_not_evaluable` | 0/84 (0.0 %) | 0/84 (0.0 %) | 0/84 (0.0 %) |
| curb/road danger miss: `ground_miss` | 0/103 (0.0 %) | 0/103 (0.0 %) | 0/103 (0.0 %) |
| curb/road danger miss: `ground_miss_straight` | 0/103 (0.0 %) | 0/103 (0.0 %) | 0/103 (0.0 %) |
| 3D occupancy danger miss: `occupancy_miss` | 6/218 (2.8 %) | 6/218 (2.8 %) | 1/218 (0.5 %) |
| 3D occupancy danger miss: `occupancy_miss_straight` | 4/218 (1.8 %) | 4/218 (1.8 %) | 0/218 (0.0 %) |
| 3D occupancy danger miss: `occupancy_turn_guidance_not_evaluable` | 2/218 (0.9 %) | 2/218 (0.9 %) | 1/218 (0.5 %) |
| 3D occupancy danger miss: `any_miss` | 6/295 (2.0 %) | 6/295 (2.0 %) | 1/295 (0.3 %) |
| true false-stop (confirmed clear path): `true_false_stop` | 0/150 (0.0 %) | 0/150 (0.0 %) | 0/150 (0.0 %) |
| true false-stop (confirmed clear path): `unnecessary_observe` | 0/150 (0.0 %) | 0/150 (0.0 %) | 0/150 (0.0 %) |
| true false-stop (confirmed clear path): `unknown_on_confirmed_clear` | 0/150 (0.0 %) | 0/150 (0.0 %) | 0/150 (0.0 %) |
| true false-stop (confirmed clear path): `guidance_on_confirmed_clear` | 150/150 (100.0 %) | 150/150 (100.0 %) | 150/150 (100.0 %) |
| true false-stop (confirmed clear path): `false_stop_on_weak_clear` | 1/174 (0.6 %) | 1/174 (0.6 %) | 0/174 (0.0 %) |
| guidance coverage: `guidance_coverage_all` | 190/481 (39.5 %) | 190/481 (39.5 %) | 178/481 (37.0 %) |
| guidance coverage: `guidance_coverage_non_danger` | 184/186 (98.9 %) | 184/186 (98.9 %) | 177/186 (95.2 %) |
| UNKNOWN / state shares: `unknown_share` | 0/481 (0.0 %) | 0/481 (0.0 %) | 1/481 (0.2 %) |
| UNKNOWN / state shares: `observe_share` | 0/481 (0.0 %) | 26/481 (5.4 %) | 46/481 (9.6 %) |
| UNKNOWN / state shares: `blocked_share` | 291/481 (60.5 %) | 265/481 (55.1 %) | 256/481 (53.2 %) |
| UNKNOWN / state shares: `clear_share` | 190/481 (39.5 %) | 190/481 (39.5 %) | 178/481 (37.0 %) |
| UNKNOWN / state shares: `stop_share` | 291/481 (60.5 %) | 265/481 (55.1 %) | 256/481 (53.2 %) |
| decision flips: `flips` | 113/478 (23.6 %) | 124/478 (25.9 %) | 133/478 (27.8 %) |
| decision flips: `flips_per_minute` | 42.552301255230084 flips/min | 46.694560669456024 flips/min | 50.08368200836815 flips/min |
| latency to first hazard warning: `hazard_onset_events` | 47 events | 47 events | 47 events |
| latency to first hazard warning: `warning_within_horizon` | 47/47 (100.0 %) | 47/47 (100.0 %) | 46/47 (97.9 %) |
| latency to first hazard warning: `warning_at_onset` | 45/47 (95.7 %) | 45/47 (95.7 %) | 46/47 (97.9 %) |
| latency to first hazard warning: `latency_p50_frames` | 0.0 frames | 0.0 frames | 0.0 frames |
| latency to first hazard warning: `latency_p95_frames` | 0.0 frames | 0.0 frames | 0.0 frames |
| latency to first hazard warning: `latency_p50_ms` | 0.0 ms | 0.0 ms | 0.0 ms |
| latency to first hazard warning: `latency_p95_ms` | 0.0 ms | 0.0 ms | 0.0 ms |

GT counts (`annotated`): road_in_path_frames=15, curb_in_path_frames=84, ground_danger_frames=103, occupancy_in_path_frames=218, any_danger_frames=295, confirmed_clear_frames=150, clear_weak_frames=174

### Per-session grouping (`annotated`)

| session | arm | `road_miss` | `curb_miss` | `occupancy_miss` | `true_false_stop` | `guidance_coverage_all` | `unknown_share` | `flips` | `warning_within_horizon` |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| -PqSDmiEe2pXjmYHgxh4YEBsj0T5LU10 | A | 0/15 (0.0 %) | 0/6 (0.0 %) (low n) | 0/24 (0.0 %) | 0/61 (0.0 %) | 78/121 (64.5 %) | 0/121 (0.0 %) | 24/120 (20.0 %) | 8/8 (100.0 %) (low n) |
| -PqSDmiEe2pXjmYHgxh4YEBsj0T5LU10 | B | 0/15 (0.0 %) | 0/6 (0.0 %) (low n) | 0/24 (0.0 %) | 0/61 (0.0 %) | 78/121 (64.5 %) | 0/121 (0.0 %) | 25/120 (20.8 %) | 8/8 (100.0 %) (low n) |
| -PqSDmiEe2pXjmYHgxh4YEBsj0T5LU10 | C | 0/15 (0.0 %) | 0/6 (0.0 %) (low n) | 0/24 (0.0 %) | 0/61 (0.0 %) | 78/121 (64.5 %) | 1/121 (0.8 %) | 26/120 (21.7 %) | 8/8 (100.0 %) (low n) |
| M5UIabsltWCYg1D9LCXLoNPO3bMUo_5- | A | **NOT_EVALUABLE** | **NOT_EVALUABLE** | 6/91 (6.6 %) | 0/76 (0.0 %) | 98/184 (53.3 %) | 0/184 (0.0 %) | 74/183 (40.4 %) | 31/31 (100.0 %) |
| M5UIabsltWCYg1D9LCXLoNPO3bMUo_5- | B | **NOT_EVALUABLE** | **NOT_EVALUABLE** | 6/91 (6.6 %) | 0/76 (0.0 %) | 98/184 (53.3 %) | 0/184 (0.0 %) | 74/183 (40.4 %) | 31/31 (100.0 %) |
| M5UIabsltWCYg1D9LCXLoNPO3bMUo_5- | C | **NOT_EVALUABLE** | **NOT_EVALUABLE** | 1/91 (1.1 %) | 0/76 (0.0 %) | 87/184 (47.3 %) | 0/184 (0.0 %) | 70/183 (38.3 %) | 30/31 (96.8 %) |
| UGNwBEZMxhzlzXVyl8XUfflcvjGwdpA5 | A | **NOT_EVALUABLE** | 0/78 (0.0 %) | 0/103 (0.0 %) | 0/13 (0.0 %) | 14/176 (8.0 %) | 0/176 (0.0 %) | 15/175 (8.6 %) | 8/8 (100.0 %) (low n) |
| UGNwBEZMxhzlzXVyl8XUfflcvjGwdpA5 | B | **NOT_EVALUABLE** | 0/78 (0.0 %) | 0/103 (0.0 %) | 0/13 (0.0 %) | 14/176 (8.0 %) | 0/176 (0.0 %) | 25/175 (14.3 %) | 8/8 (100.0 %) (low n) |
| UGNwBEZMxhzlzXVyl8XUfflcvjGwdpA5 | C | **NOT_EVALUABLE** | 0/78 (0.0 %) | 0/103 (0.0 %) | 0/13 (0.0 %) | 13/176 (7.4 %) | 0/176 (0.0 %) | 37/175 (21.1 %) | 8/8 (100.0 %) (low n) |

## Condition `production`

| metric | arm A | arm B | arm C |
|---|---:|---:|---:|
| curb/road danger miss: `road_miss` | 1/15 (6.7 %) | 1/15 (6.7 %) | 1/15 (6.7 %) |
| curb/road danger miss: `road_miss_straight` | 1/15 (6.7 %) | 1/15 (6.7 %) | 1/15 (6.7 %) |
| curb/road danger miss: `road_turn_guidance_not_evaluable` | 0/15 (0.0 %) | 0/15 (0.0 %) | 0/15 (0.0 %) |
| curb/road danger miss: `curb_miss` | 13/84 (15.5 %) | 13/84 (15.5 %) | 11/84 (13.1 %) |
| curb/road danger miss: `curb_miss_straight` | 5/84 (6.0 %) | 5/84 (6.0 %) | 4/84 (4.8 %) |
| curb/road danger miss: `curb_turn_guidance_not_evaluable` | 8/84 (9.5 %) | 8/84 (9.5 %) | 7/84 (8.3 %) |
| curb/road danger miss: `ground_miss` | 15/103 (14.6 %) | 15/103 (14.6 %) | 13/103 (12.6 %) |
| curb/road danger miss: `ground_miss_straight` | 7/103 (6.8 %) | 7/103 (6.8 %) | 6/103 (5.8 %) |
| 3D occupancy danger miss: `occupancy_miss` | 107/218 (49.1 %) | 108/218 (49.5 %) | 104/218 (47.7 %) |
| 3D occupancy danger miss: `occupancy_miss_straight` | 75/218 (34.4 %) | 76/218 (34.9 %) | 74/218 (33.9 %) |
| 3D occupancy danger miss: `occupancy_turn_guidance_not_evaluable` | 32/218 (14.7 %) | 32/218 (14.7 %) | 30/218 (13.8 %) |
| 3D occupancy danger miss: `any_miss` | 126/295 (42.7 %) | 127/295 (43.1 %) | 120/295 (40.7 %) |
| true false-stop (confirmed clear path): `true_false_stop` | 0/150 (0.0 %) | 0/150 (0.0 %) | 0/150 (0.0 %) |
| true false-stop (confirmed clear path): `unnecessary_observe` | 0/150 (0.0 %) | 0/150 (0.0 %) | 0/150 (0.0 %) |
| true false-stop (confirmed clear path): `unknown_on_confirmed_clear` | 43/150 (28.7 %) | 43/150 (28.7 %) | 67/150 (44.7 %) |
| true false-stop (confirmed clear path): `guidance_on_confirmed_clear` | 107/150 (71.3 %) | 107/150 (71.3 %) | 83/150 (55.3 %) |
| true false-stop (confirmed clear path): `false_stop_on_weak_clear` | 0/174 (0.0 %) | 0/174 (0.0 %) | 0/174 (0.0 %) |
| guidance coverage: `guidance_coverage_all` | 255/481 (53.0 %) | 256/481 (53.2 %) | 224/481 (46.6 %) |
| guidance coverage: `guidance_coverage_non_danger` | 129/186 (69.4 %) | 129/186 (69.4 %) | 104/186 (55.9 %) |
| UNKNOWN / state shares: `unknown_share` | 124/481 (25.8 %) | 123/481 (25.6 %) | 200/481 (41.6 %) |
| UNKNOWN / state shares: `observe_share` | 0/481 (0.0 %) | 41/481 (8.5 %) | 45/481 (9.4 %) |
| UNKNOWN / state shares: `blocked_share` | 102/481 (21.2 %) | 61/481 (12.7 %) | 12/481 (2.5 %) |
| UNKNOWN / state shares: `clear_share` | 255/481 (53.0 %) | 256/481 (53.2 %) | 224/481 (46.6 %) |
| UNKNOWN / state shares: `stop_share` | 102/481 (21.2 %) | 61/481 (12.7 %) | 12/481 (2.5 %) |
| decision flips: `flips` | 197/478 (41.2 %) | 210/478 (43.9 %) | 206/478 (43.1 %) |
| decision flips: `flips_per_minute` | 74.18410041840997 flips/min | 79.07949790794972 flips/min | 77.5732217573221 flips/min |
| latency to first hazard warning: `hazard_onset_events` | 47 events | 47 events | 47 events |
| latency to first hazard warning: `warning_within_horizon` | 35/47 (74.5 %) | 35/47 (74.5 %) | 36/47 (76.6 %) |
| latency to first hazard warning: `warning_at_onset` | 15/47 (31.9 %) | 15/47 (31.9 %) | 17/47 (36.2 %) |
| latency to first hazard warning: `latency_p50_frames` | 1.0 frames | 1.0 frames | 1.0 frames |
| latency to first hazard warning: `latency_p95_frames` | 9.0 frames | 9.0 frames | 8.25 frames |
| latency to first hazard warning: `latency_p50_ms` | 333.3333333333333 ms | 333.3333333333333 ms | 333.3333333333333 ms |
| latency to first hazard warning: `latency_p95_ms` | 3000.0 ms | 3000.0 ms | 2750.0 ms |

GT counts (`production`): road_in_path_frames=15, curb_in_path_frames=84, ground_danger_frames=103, occupancy_in_path_frames=218, any_danger_frames=295, confirmed_clear_frames=150, clear_weak_frames=174

### Per-session grouping (`production`)

| session | arm | `road_miss` | `curb_miss` | `occupancy_miss` | `true_false_stop` | `guidance_coverage_all` | `unknown_share` | `flips` | `warning_within_horizon` |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| -PqSDmiEe2pXjmYHgxh4YEBsj0T5LU10 | A | 1/15 (6.7 %) | 4/6 (66.7 %) (low n) | 7/24 (29.2 %) | 0/61 (0.0 %) | 51/121 (42.1 %) | 52/121 (43.0 %) | 51/120 (42.5 %) | 8/8 (100.0 %) (low n) |
| -PqSDmiEe2pXjmYHgxh4YEBsj0T5LU10 | B | 1/15 (6.7 %) | 4/6 (66.7 %) (low n) | 7/24 (29.2 %) | 0/61 (0.0 %) | 51/121 (42.1 %) | 52/121 (43.0 %) | 51/120 (42.5 %) | 8/8 (100.0 %) (low n) |
| -PqSDmiEe2pXjmYHgxh4YEBsj0T5LU10 | C | 1/15 (6.7 %) | 2/6 (33.3 %) (low n) | 5/24 (20.8 %) | 0/61 (0.0 %) | 31/121 (25.6 %) | 74/121 (61.2 %) | 46/120 (38.3 %) | 8/8 (100.0 %) (low n) |
| M5UIabsltWCYg1D9LCXLoNPO3bMUo_5- | A | **NOT_EVALUABLE** | **NOT_EVALUABLE** | 82/91 (90.1 %) | 0/76 (0.0 %) | 161/184 (87.5 %) | 23/184 (12.5 %) | 84/183 (45.9 %) | 20/31 (64.5 %) |
| M5UIabsltWCYg1D9LCXLoNPO3bMUo_5- | B | **NOT_EVALUABLE** | **NOT_EVALUABLE** | 82/91 (90.1 %) | 0/76 (0.0 %) | 161/184 (87.5 %) | 23/184 (12.5 %) | 84/183 (45.9 %) | 20/31 (64.5 %) |
| M5UIabsltWCYg1D9LCXLoNPO3bMUo_5- | C | **NOT_EVALUABLE** | **NOT_EVALUABLE** | 82/91 (90.1 %) | 0/76 (0.0 %) | 152/184 (82.6 %) | 32/184 (17.4 %) | 78/183 (42.6 %) | 21/31 (67.7 %) |
| UGNwBEZMxhzlzXVyl8XUfflcvjGwdpA5 | A | **NOT_EVALUABLE** | 9/78 (11.5 %) | 18/103 (17.5 %) | 0/13 (0.0 %) | 43/176 (24.4 %) | 49/176 (27.8 %) | 62/175 (35.4 %) | 7/8 (87.5 %) (low n) |
| UGNwBEZMxhzlzXVyl8XUfflcvjGwdpA5 | B | **NOT_EVALUABLE** | 9/78 (11.5 %) | 19/103 (18.4 %) | 0/13 (0.0 %) | 44/176 (25.0 %) | 48/176 (27.3 %) | 75/175 (42.9 %) | 7/8 (87.5 %) (low n) |
| UGNwBEZMxhzlzXVyl8XUfflcvjGwdpA5 | C | **NOT_EVALUABLE** | 9/78 (11.5 %) | 17/103 (16.5 %) | 0/13 (0.0 %) | 41/176 (23.3 %) | 94/176 (53.4 %) | 82/175 (46.9 %) | 7/8 (87.5 %) (low n) |

