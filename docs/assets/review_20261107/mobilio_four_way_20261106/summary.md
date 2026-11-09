# Mobilio-inspired PC SANPO A/B replay

Frames: 481 across 3 sessions.

Evidence level: **DESKTOP_REAL_DATA** (offline SANPO-Real analysis). The pose is a SANPO recorded pose used as an offline VIO proxy; this is not a phone-device or LiDAR validation.

Baseline: `baseline_split_c` = frozen c000 spatial Path Keeper + Obstacle Guardian.
Candidate: `mobilio` = persistent SurfaceGrid2D + OccupancyGrid3D + monotone fusion.

## Condition `A_gt_gt`

| metric | baseline_split_c | mobilio |
|---|---:|---:|
| `road_miss` | 0/15 (0.0%) | 0/15 (0.0%) |
| `curb_miss` | 0/84 (0.0%) | 1/84 (1.2%) |
| `ground_miss` | 0/103 (0.0%) | 1/103 (1.0%) |
| `occupancy_miss` | 1/218 (0.5%) | 9/218 (4.1%) |
| `any_miss` | 1/295 (0.3%) | 10/295 (3.4%) |
| `true_false_stop` | 0/150 (0.0%) | 0/150 (0.0%) |
| `guidance_coverage_all` | 178/481 (37.0%) | 29/481 (6.0%) |
| `unknown_share` | 1/481 (0.2%) | 37/481 (7.7%) |
| `flips` | 133/478 (27.8%) | 65/478 (13.6%) |
| `warning_within_horizon` | 46/47 (97.9%) | 47/47 (100.0%) |

GT/frame counts: road_in_path_frames=15, curb_in_path_frames=84, ground_danger_frames=103, occupancy_in_path_frames=218, any_danger_frames=295, confirmed_clear_frames=150, clear_weak_frames=174

## Condition `B_v5_gt`

| metric | baseline_split_c | mobilio |
|---|---:|---:|
| `road_miss` | 0/15 (0.0%) | 0/15 (0.0%) |
| `curb_miss` | 0/84 (0.0%) | 0/84 (0.0%) |
| `ground_miss` | 0/103 (0.0%) | 0/103 (0.0%) |
| `occupancy_miss` | 1/218 (0.5%) | 8/218 (3.7%) |
| `any_miss` | 2/295 (0.7%) | 8/295 (2.7%) |
| `true_false_stop` | 0/150 (0.0%) | 0/150 (0.0%) |
| `guidance_coverage_all` | 147/481 (30.6%) | 15/481 (3.1%) |
| `unknown_share` | 13/481 (2.7%) | 61/481 (12.7%) |
| `flips` | 138/478 (28.9%) | 39/478 (8.2%) |
| `warning_within_horizon` | 46/47 (97.9%) | 47/47 (100.0%) |

GT/frame counts: road_in_path_frames=15, curb_in_path_frames=84, ground_danger_frames=103, occupancy_in_path_frames=218, any_danger_frames=295, confirmed_clear_frames=150, clear_weak_frames=174

## Condition `C_gt_dav2`

| metric | baseline_split_c | mobilio |
|---|---:|---:|
| `road_miss` | 1/15 (6.7%) | 0/15 (0.0%) |
| `curb_miss` | 11/84 (13.1%) | 3/84 (3.6%) |
| `ground_miss` | 13/103 (12.6%) | 3/103 (2.9%) |
| `occupancy_miss` | 104/218 (47.7%) | 15/218 (6.9%) |
| `any_miss` | 120/295 (40.7%) | 18/295 (6.1%) |
| `true_false_stop` | 0/150 (0.0%) | 0/150 (0.0%) |
| `guidance_coverage_all` | 224/481 (46.6%) | 57/481 (11.9%) |
| `unknown_share` | 200/481 (41.6%) | 276/481 (57.4%) |
| `flips` | 206/478 (43.1%) | 145/478 (30.3%) |
| `warning_within_horizon` | 36/47 (76.6%) | 47/47 (100.0%) |

GT/frame counts: road_in_path_frames=15, curb_in_path_frames=84, ground_danger_frames=103, occupancy_in_path_frames=218, any_danger_frames=295, confirmed_clear_frames=150, clear_weak_frames=174

## Condition `D_v5_dav2`

| metric | baseline_split_c | mobilio |
|---|---:|---:|
| `road_miss` | 3/15 (20.0%) | 0/15 (0.0%) |
| `curb_miss` | 13/84 (15.5%) | 4/84 (4.8%) |
| `ground_miss` | 17/103 (16.5%) | 4/103 (3.9%) |
| `occupancy_miss` | 105/218 (48.2%) | 14/218 (6.4%) |
| `any_miss` | 123/295 (41.7%) | 17/295 (5.8%) |
| `true_false_stop` | 0/150 (0.0%) | 0/150 (0.0%) |
| `guidance_coverage_all` | 220/481 (45.7%) | 49/481 (10.2%) |
| `unknown_share` | 214/481 (44.5%) | 275/481 (57.2%) |
| `flips` | 201/478 (42.1%) | 136/478 (28.5%) |
| `warning_within_horizon` | 36/47 (76.6%) | 47/47 (100.0%) |

GT/frame counts: road_in_path_frames=15, curb_in_path_frames=84, ground_danger_frames=103, occupancy_in_path_frames=218, any_danger_frames=295, confirmed_clear_frames=150, clear_weak_frames=174

## Interpretation rules

* Production decisions use v5 + calibrated DAV2 only; GT is used after the decision for scoring.
* Annotated is a geometry/semantic attribution condition, not a deployment result.
* `UNKNOWN` and missing surface/occupancy observations are not counted as a fully observed clear path.
* Any improvement or regression is specific to these SANPO sessions and does not reproduce Mobilio's paper numbers.
