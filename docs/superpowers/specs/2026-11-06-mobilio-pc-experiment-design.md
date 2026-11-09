# Mobilio-inspired PC Surface and Occupancy Experiment

**Date:** 2026-11-06  
**Status:** Approved for implementation in the isolated experiment worktree  
**Scope:** PC evaluation code only; Android and the Last10m P0 path remain frozen

## 1. Purpose

This experiment tests whether the useful part of Harvard-Slade-Lab/Mobilio's
architecture transfers to BlindNav's existing PC pipeline:

* keep a persistent two-dimensional surface map for path keeping;
* keep a separate three-dimensional occupancy map for objects in the body path;
* make the two responsibilities explicit at the decision boundary.

The implementation is an offline, reproducible experiment over the SANPO-Real
frames already available on this machine. It is not a claim that the current
phone app has ARKit/ARCore VIO, LiDAR, or a production-ready depth sensor.

The relevant Mobilio sources are:

* [Nature Biomedical Engineering paper](https://www.nature.com/articles/s41551-026-01772-x)
* [Mobilio repository](https://github.com/Harvard-Slade-Lab/Mobilio)
* [`Vision.cs`](https://raw.githubusercontent.com/Harvard-Slade-Lab/Mobilio/main/scripts/Vision.cs)
* [`DepthImage.cs`](https://raw.githubusercontent.com/Harvard-Slade-Lab/Mobilio/main/scripts/DepthImage.cs)

The source code establishes the architectural split, but its Unity/AR
Foundation implementation is not copied into BlindNav.

## 2. Frozen baseline and invariants

The experiment worktree starts at `c000ad2`, the existing responsibility-split
experiment. Arm A is the current production condition from that commit:

* v5 SegFormer output;
* the existing DAV2 and ground calibration path;
* spatial Obstacle Guardian;
* existing Path Keeper and Safety Coordinator behavior.

The following are out of scope for this experiment and must not be modified:

* the user's current branch or any existing PR branch;
* Android sources, MotionAuthority, Safety Policy, ArrivalAuthority, and
  Last10m;
* the frozen PR #17 reference evaluator and its per-frame reference result;
* SANPO data, model checkpoints, or other large binary assets.

Each experiment run resets map state at the start of every SANPO session. A/B
arms consume exactly the same frame indices, v5 outputs, DAV2 outputs, camera
intrinsics, and recorded poses.

The SANPO chest-camera replay has a measured near-field ground blind zone of
about 2.2 m (the first reliable ground cells start just beyond that distance).
The candidate exposes `blind_zone_m=2.2` as a named geometry configuration:
unknown cells inside that near-field zone are not counted as an internal
surface/occupancy gap, while the report still records the proxy. This keeps the
1.5 m immediate obstacle horizon separate from the camera's ground coverage
limitation.

## 3. Data and coordinate contract

The evaluator reads the existing `SanpoSession` layout using `--data-root`.
For SANPO-Real it uses `fixed_camera_poses.csv`, which is a recorded visual-
inertial pose track. The pose is an **offline VIO proxy**: SANPO does not expose
the raw IMU stream used to reproduce a phone implementation.

The common map convention is:

* horizontal map coordinates `(x, z)` in metres;
* `x` positive to the user's right and `z` positive in the map's forward plane;
* `yaw_rad` is the current body heading;
* world up and camera axes are the conventions already validated by
  `SanpoSession.gravity_check`;
* depth is metric camera z-depth after the existing DAV2 calibration adapter.

The new modules must accept a small pose protocol (`x_m`, `z_m`, `yaw_rad`,
and the vertical transform needed by the point adapter) rather than importing
SANPO. This keeps the geometry code testable and makes the proxy explicit at
the evaluator boundary.

## 4. System shape

The new PC-only path has four layers:

```text
RGB -> v5 roles -----> SurfaceGrid2D -----> surface route
depth + pose --------> OccupancyGrid3D --> obstacle route/risk
                                      \--> MobilioFusion decision
```

The baseline arm continues to use the existing evaluator and classes. The new
arm does not replace or mutate `PathKeeper`, `ObstacleGuardian`, or
`SafetyCoordinator`; it has an adapter-level fusion object so the experiment
can be removed or compared without changing the old contract.

### 4.1 Strict role adapter

`SurfaceGrid2D` receives four explicit roles: `WALKABLE`, `HAZARD`,
`NON_WALKABLE`, and `UNKNOWN`. The adapter owns model/data label mapping and
does not live in the grid.

* Road is not silently treated as sidewalk. Any v5/GT class selected as road
  is `NON_WALKABLE` for the strict sidewalk route.
* Curb and ground boundary evidence is `HAZARD`.
* Low-confidence, sky, missing depth, and unprojectable pixels are `UNKNOWN`.
* Unknown is never converted to clear evidence. A ray may be reported as
  diagnostically traversable through a bounded gap, but the decision carries
  an unknown flag and cannot claim a fully observed clear corridor.

## 5. SurfaceGrid2D

### 5.1 State

`SurfaceGrid2D` uses a configurable default cell size of **0.20 m** and a
15 m square map window, matching the scale used by Mobilio's `Vision.cs`.
Each cell stores:

* strict role evidence and confidence;
* last-observed frame/time;
* observed/unknown flags for diagnostics;
* a deterministic resolved role using the precedence
  `HAZARD > NON_WALKABLE > WALKABLE > UNKNOWN` for equal-time conflicts.

The grid is in the session map frame, so cells persist while the pose changes.
Cells outside the configured window are discarded. Unobserved cells remain
`UNKNOWN`; stale observed cells are decayed and eventually removed rather than
being declared walkable.

### 5.2 Frame update

`update_from_frame(roles, metric_depth, intrinsics, pose, ...)`:

1. Subsample valid pixels using a deterministic stride.
2. Back-project the pixel with the existing camera/depth convention.
3. Intersect or classify the point against the local ground band.
4. Transform the ground point to map `(x, z)` using the recorded pose.
5. Accumulate the role into its 0.20 m cell.

This is the PC substitute for Mobilio's camera-to-ground projection. It does
not require AR Foundation or LiDAR. The depth adapter may be fed DAV2 in the
production condition or SANPO depth in the annotated geometry condition.

### 5.3 Ray search

`raycast_route(target_angle_rad, ...)` reproduces the useful behavior of
Mobilio's `RaycastGrid` without copying Unity code:

* scan `target_angle +/- 90 degrees` at a 1 degree step;
* step through grid cells with a deterministic DDA/Bresenham traversal;
* stop on a resolved hazard or non-walkable cell;
* ignore the explicitly configured 2.2 m near-field blind zone, then allow at
  most two consecutive unknown/missing cells as a **gap diagnostic**;
* retain candidate rays longer than 5 m;
* prefer a fully observed candidate over a candidate containing an internal
  unknown gap; within that class prefer the greatest observed walkable
  distance, then the smallest angular distance to the requested route;
* report candidate branch distances for large turns.

The result includes the selected angle, observed distance, unknown-gap count,
termination role, candidate count, and a `fully_observed` flag. No unknown ray
is passed to the final fusion as an unqualified `CLEAR` result.

## 6. OccupancyGrid3D

### 6.1 State

`OccupancyGrid3D` uses **0.10 m** horizontal cells and height bands over the
ground-to-1.7 m human envelope, matching the body-scale intent of
`DepthImage.cs`. A cell stores a floating evidence score, last observation,
and the source frame count. The key is `(ix, iz, ih)` in the session map frame.

### 6.2 Frame update and decay

`update_from_depth(points, pose, visible_free_cells, ...)`:

1. Convert calibrated DAV2/annotated depth points to camera 3D points.
2. Transform them to map coordinates with the recorded pose.
3. Keep points within the configured 4 m search radius and human-height band.
4. Add occupancy evidence to the corresponding 0.10 m cells.
5. For cells proven visible and empty by the same camera rays, multiply
   evidence by the fixed 0.9 decay factor.
6. Remove evidence below the configured floor; never decay unobserved cells
   into free space.

The occupancy input excludes the ground band (`height <= 0.15 m`) and excludes
points whose strict semantic role is `WALKABLE`; those points are surface
evidence or visible free ground, not 3-D objects. The visibility list is
generated by the depth adapter, so an invalid DAV2 pixel contributes unknown
rather than free space. The map is session-local and there is no cross-session
memory.

### 6.3 Obstacle and route search

The immediate collision test uses the fixed a-priori body envelope:

* forward obstacle horizon: 1.5 m;
* padded planning horizon: 2.0 m;
* route search horizon: 4.0 m;
* person radius: 0.20 m;
* height band: ground through 1.7 m;
* a blocking cell requires evidence above the temporal threshold.

`raycast_clearance(target_angle_rad, ...)` scans the same +/-90 degree range,
inflates the body footprint, and returns the first blocking cell or the maximum
clear distance. It reports selected angle, clear distance, blocking cells,
unknown cells, and whether temporal evidence confirmed the mass. The geometry
is separate from semantic surface evidence.

## 7. MobilioFusion decision contract

`MobilioFusion` combines one `SurfaceRoute` and one `OccupancyRisk` with a
monotone safety rule:

1. confirmed occupancy in the swept body path -> `BLOCKED`;
2. insufficient/unknown geometry needed for a safe decision -> `UNKNOWN` or
   `OBSERVE`, with the reason preserved;
3. otherwise use the surface-selected route and its observed/unknown status.

The result is a serializable record containing both sub-results, selected
angle, state, reason, and counters. The fusion object does not emit Android
STOP commands and is only an experimental PC decision record.

## 8. A/B evaluator

Add `pc/tools/mobilio_sanpo_ab.py` with these properties:

* `--data-root`, `--sessions`, `--camera`, `--v5-checkpoint`, and the existing
  offline model flags;
* `--conditions production annotated` (production is the decision result;
  annotated is geometry/semantic attribution only);
* `--baseline split_c` and `--candidate mobilio` defaults;
* deterministic `--max-width`, `--limit-per-session`, `--pixel-stride`, and
  output directory options;
* per-session map reset and identical frame ordering for both arms;
* no SANPO GT passed to the production arm.

The evaluator reuses the existing metric definitions and reports at least:

* road, curb, ground, occupancy, and any-miss rates with denominators;
* false STOP / true-false-stop counts;
* guidance coverage, unknown share, state flips;
* warning-within-horizon and warning latency;
* surface ray coverage, occupancy confirmation, and unknown-gap rates;
* frame/session counts, model/checkpoint identifiers, pose mode, and all
  configuration values.

Outputs are written under an ignored `pc/out/mobilio_sanpo_ab_20261106/`
directory. A small summary, metrics CSV, and contact sheet may be copied to a
new `docs/assets/mobilio_sanpo_ab_20261106/` directory after verification;
raw images, SANPO data, and checkpoints are never committed.

## 9. Visual verification

For deterministic selected frames and saved failures, produce a contact sheet
with four panels:

1. input RGB with v5 role overlay;
2. current surface grid, route rays, and selected angle;
3. occupancy top-down map with body footprint and blocking cells;
4. A/B decision text and key per-frame metrics.

The visualization is diagnostic only. It must label the pose as `SANPO
recorded pose (offline VIO proxy)` and distinguish DAV2 production depth from
annotated/GT geometry.

## 10. Tests and acceptance criteria

Tests are added before implementation for:

* SurfaceGrid2D cell quantization, role precedence, unknown gaps, DDA stopping,
  angular tie-breaking, and map reset;
* OccupancyGrid3D camera-to-map transform, height filtering, evidence decay,
  visibility/unknown handling, footprint intersection, and temporal confirmation;
* MobilioFusion monotonic safety states;
* A/B evaluator frame alignment, session reset, metric denominators, and
  production-vs-annotated GT isolation.

The experiment is considered reproducible when:

1. the full existing suite still passes;
2. the new evaluator processes all 481 available SANPO frames in production
   and annotated modes with the same frame keys in both arms;
3. the frozen responsibility-split reference check remains unchanged;
4. the output contains machine-readable per-frame/summary records and the
   contact sheet;
5. the report states what is measured, what is a proxy, and whether the
   candidate improves or regresses each safety metric. No Mobilio paper number
   is attributed to the RGB+IMU-only BlindNav setup.

No metric threshold is tuned on the SANPO evaluation frames. If calibration or
threshold selection is needed, it must be recorded as an a-priori existing
configuration or moved to a separately named development split.

## 11. Risks and controlled follow-ups

* **Pose quality:** SANPO's fixed pose is useful for replay but hides phone VIO
  drift. The report must separate map-geometry performance from deployability.
* **DAV2 scale:** the production depth path depends on existing calibration;
  no new scale fit may use evaluation GT.
* **Domain shift:** the SANPO sessions are a small real-data sample. Results
  are an A/B measurement for this dataset, not a generalization claim.
* **Unknown treatment:** treating unknown as free can make the candidate look
  better while weakening safety. The candidate's unknown and fully-observed
  counters are therefore first-class outputs.
* **Runtime:** the first implementation is an offline reference. A later
  device benchmark is a separate task and must not be inferred from this run.
