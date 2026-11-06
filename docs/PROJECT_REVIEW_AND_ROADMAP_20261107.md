# BlindNav project review and roadmap

## Repository and review state

Repository: <https://github.com/haomingweng3-eng/blindnav-superagent>

Current review PR: [PR #20](https://github.com/haomingweng3-eng/blindnav-superagent/pull/20)

Branch: `agent/corridor-obstacle-split-ad0888814215`

The branch contains the PC Mobilio experiments, Harvard reference ports,
SANPO reports, Last10m Android sources, real Last10m replay assets, and the
evaluation protocols. PR #20 is open and has not been merged.

Local `.gradle/`, `build/`, and `local.properties` files are environment-only
and are not part of the reviewable repository changes.

## Current technical decision

### Last10m — active product priority

Keep the existing deterministic chain:

`OCR / replay vision -> target lock -> entrance candidates -> target-door relation -> ground evidence -> arrival authority -> speech policy`

The restored replay contains three real LSVT storefront scenarios and seven
frames covering multiple doors, a neighboring storefront, target-to-door
relations, target absence, and an obstructed entrance.

The next acceptance gate is a real or replayable Android run that reports:

* target-lock accuracy;
* correct entrance relation accuracy;
* wrong-entrance rate;
* target-absent refusal rate;
* views needed before confirmation;
* ground-obstruction evidence;
* motion authorization only after entrance and ground evidence.

### Path Keeper / Obstacle Guardian — research hold

Harvard Mobilio remains a reference implementation and research artifact. The
same SANPO-Real 481-frame set was used for A/B and four-way input ablations.
The corrected FillGrid replay reduced D-condition danger misses to 9/295 but
left authorized direction at 13/481 and UNKNOWN at 405/481. This is a safety
improvement with a usability failure, so the route is `BLOCKED_BY_EVIDENCE`.

Reopen conditions:

* independent pedestrian-view segmentation data with a measured gain;
* real synchronized RGB+IMU/depth input;
* independent continuous safe-corridor and detour ground truth.

## Technical selection pain points

### 1. Perception and planning were coupled too early

The Mobilio experiment showed that a better search routine cannot recover a
missing or broken ground map. Surface evidence, occupancy evidence, risk state,
and motion authorization must remain separate.

### 2. AR inputs were mistaken for replaceable plumbing

AR supplied world pose, metric depth, camera projection, and temporal map
alignment. DAV2 plus a recorded SANPO pose is useful for desktop attribution,
but it is not phone VIO validation.

### 3. UNKNOWN has two costs

UNKNOWN can reduce dangerous forward passes while making continuous navigation
unusable. Reports must always show both danger miss and confirmed-clear guidance;
UNKNOWN is never a successful hazard recognition.

### 4. Door detection is not entrance identification

A door box does not prove it belongs to the requested store. The Last10m
evaluation therefore requires explicit `SAME_TARGET` / `NEIGHBOR` relation
labels and separates entrance confirmation from motion authorization.

### 5. Evaluation data is the main product risk

The Mobilio set has a frozen 481-frame replay but no real RGB+IMU observation
files in the local sessions. The Last10m demo has real images and structured
replay answers, but Android execution is still blocked by missing local
`sherpa-onnx.aar` and insufficient disk space.

## Review gates

1. **Last10m asset gate:** scenarios parse, images load, relation labels are
   present, and replay provenance is visible.
2. **Android gate:** Gradle unit tests and the DemoActivity run with the actual
   local AAR dependencies.
3. **Entrance gate:** report correct relation, wrong relation, target absence,
   and view count; do not substitute door-box recall.
4. **Motion gate:** no motion authorization before verified entrance and ground
   evidence.
5. **Mobilio reopen gate:** only new sensor/data evidence can reopen Path
   Keeper work.

## Canonical reports

* [Last10m evaluation protocol](LAST10M_EVALUATION_PROTOCOL_20261107.md)
* [Last10m demo report](LAST10M_DEMO_REPORT_20261107.md)
* [Mobilio reference difference report](MOBILIO_REFERENCE_DIFF_20261106.md)
* [FillGrid replay report](MOBILIO_FILLGRID_REPLAY_20261107.md)
* [Harvard corrected replay](assets/mobilio_harvard_reference_fixed_20261107/summary.md)
* [Four-way input ablation](assets/mobilio_four_way_20261106/summary.md)
