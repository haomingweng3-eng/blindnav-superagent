# Mobilio-inspired PC SANPO A/B replay

Frames: 481 across 3 sessions.

Evidence level: **DESKTOP_REAL_DATA** (offline SANPO-Real analysis). The pose is a SANPO recorded pose used as an offline VIO proxy; this is not a phone-device or LiDAR validation.

Baseline: `baseline_split_c` = frozen c000 spatial Path Keeper + Obstacle Guardian.
Candidate: `mobilio` = persistent SurfaceGrid2D + OccupancyGrid3D + monotone fusion.

## Condition `harvard_A`

| metric | baseline_split_c | mobilio |
|---|---:|---:|
| `road_miss` | 0/15 (0.0%) | 6/15 (40.0%) |
| `curb_miss` | 0/84 (0.0%) | 0/84 (0.0%) |
| `ground_miss` | 0/103 (0.0%) | 6/103 (5.8%) |
| `occupancy_miss` | 1/218 (0.5%) | 0/218 (0.0%) |
| `any_miss` | 1/295 (0.3%) | 6/295 (2.0%) |
| `true_false_stop` | 0/150 (0.0%) | 0/150 (0.0%) |
| `guidance_coverage_all` | 178/481 (37.0%) | 23/481 (4.8%) |
| `unknown_share` | 1/481 (0.2%) | 458/481 (95.2%) |
| `flips` | 133/478 (27.8%) | 16/478 (3.3%) |
| `warning_within_horizon` | 46/47 (97.9%) | 47/47 (100.0%) |

GT/frame counts: road_in_path_frames=15, curb_in_path_frames=84, ground_danger_frames=103, occupancy_in_path_frames=218, any_danger_frames=295, confirmed_clear_frames=150, clear_weak_frames=174

## Condition `harvard_D`

| metric | baseline_split_c | mobilio |
|---|---:|---:|
| `road_miss` | 3/15 (20.0%) | 10/15 (66.7%) |
| `curb_miss` | 13/84 (15.5%) | 0/84 (0.0%) |
| `ground_miss` | 17/103 (16.5%) | 11/103 (10.7%) |
| `occupancy_miss` | 105/218 (48.2%) | 22/218 (10.1%) |
| `any_miss` | 123/295 (41.7%) | 40/295 (13.6%) |
| `true_false_stop` | 0/150 (0.0%) | 0/150 (0.0%) |
| `guidance_coverage_all` | 220/481 (45.7%) | 80/481 (16.6%) |
| `unknown_share` | 214/481 (44.5%) | 401/481 (83.4%) |
| `flips` | 201/478 (42.1%) | 41/478 (8.6%) |
| `warning_within_horizon` | 36/47 (76.6%) | 46/47 (97.9%) |

GT/frame counts: road_in_path_frames=15, curb_in_path_frames=84, ground_danger_frames=103, occupancy_in_path_frames=218, any_danger_frames=295, confirmed_clear_frames=150, clear_weak_frames=174

## Interpretation rules

* Production decisions use v5 + calibrated DAV2 only; GT is used after the decision for scoring.
* Annotated is a geometry/semantic attribution condition, not a deployment result.
* `UNKNOWN` and missing surface/occupancy observations are not counted as a fully observed clear path.
* Any improvement or regression is specific to these SANPO sessions and does not reproduce Mobilio's paper numbers.
