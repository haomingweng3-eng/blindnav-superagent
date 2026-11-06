# Mobilio-inspired PC Surface and Occupancy Experiment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an ARCore/LiDAR-independent, reproducible SANPO A/B experiment that compares the frozen spatial responsibility-split baseline with persistent Mobilio-inspired 2D surface and 3D occupancy maps, including visual evidence.

**Architecture:** Keep all existing Path Keeper, Obstacle Guardian, Safety Coordinator, Android, and Last10m code unchanged. Add small geometry modules for a persistent map-frame surface grid, a height-layered occupancy grid, and a monotone fusion record; add one evaluator that shares model/depth/pose inputs between the baseline and candidate and reuses the existing GT metric definitions.

**Tech Stack:** Python 3, NumPy, OpenCV, existing PyTorch/Transformers v5 and DAV2 adapters, pytest, existing CSV/Markdown metric writers, and the repository's current visualization dependencies. No new runtime dependency and no committed data/checkpoints.

**Spec:** `docs/superpowers/specs/2026-11-06-mobilio-pc-experiment-design.md`

## Global Constraints

* Work only in `/Users/mima0000/blindnav-app-mobilio-pc-experiment` on `codex/mobilio-pc-experiment`, based on `c000ad2`.
* Keep the user's current branch, all existing PR branches, Android sources, and Last10m unchanged.
* Use a 0.20 m surface grid, 15 m surface extent, 0.10 m occupancy cells, 1.5 m immediate horizon, 2.0 m padded horizon, 4.0 m route horizon, 0.20 m person radius, and 1.7 m person height unless a test explicitly overrides a value.
* Use SANPO `fixed_camera_poses.csv` as an explicitly named offline VIO proxy; never imply that raw phone IMU/VIO has been reproduced.
* Production uses v5 + existing DAV2/ground calibration only; SANPO GT is scoring input and may be used by the separately named annotated geometry condition only.
* Unknown remains distinct from non-walkable and cannot be silently reported as a fully observed clear corridor.
* Run tests before each implementation commit and keep the frozen responsibility-split reference check unchanged.

## File Map

**Create:**

* `pc/blindnav_pc/mobilio_surface.py` — role constants, map-frame 2D grid, frame update, deterministic DDA ray search, serializable route result.
* `pc/blindnav_pc/mobilio_occupancy.py` — height-layered map-frame occupancy state, body-point transform, evidence/visibility decay, temporal confirmation, clearance ray search.
* `pc/blindnav_pc/mobilio_fusion.py` — candidate surface/occupancy result types and monotone experimental fusion.
* `pc/tests/test_mobilio_surface.py` — surface grid and DDA tests.
* `pc/tests/test_mobilio_occupancy.py` — occupancy transform, decay, visibility, and temporal tests.
* `pc/tests/test_mobilio_fusion.py` — fusion ordering and unknown-state tests.
* `pc/tests/test_mobilio_sanpo_ab.py` — evaluator alignment and GT-isolation tests using tiny in-memory fixtures.
* `pc/tests/test_mobilio_viz.py` — deterministic contact-sheet layout test.
* `pc/tools/mobilio_sanpo_ab.py` — same-input SANPO production/annotated A/B evaluator, CSV/JSON/Markdown output, failure selection, and CLI.
* `pc/tools/mobilio_viz.py` — deterministic four-panel surface/occupancy contact-sheet renderer.

**Modify:**

* `pc/blindnav_pc/__init__.py` only if the package currently exports public symbols and tests require the new types; otherwise leave it unchanged.
* No existing evaluator, Android source, or safety module is modified except for imports that are proven necessary and remain behaviorally identical.

**Outputs (ignored):**

* `pc/out/mobilio_sanpo_ab_20261106/` — full per-frame artifacts, failures, and contact sheets.
* Optional reviewed summaries under `docs/assets/mobilio_sanpo_ab_20261106/`; never copy raw SANPO frames or checkpoints.

---

### Task 1: Surface grid state and frame update

**Files:**
- Create: `pc/blindnav_pc/mobilio_surface.py`
- Create: `pc/tests/test_mobilio_surface.py`

**Interfaces:**
- Consumes: map-frame points `(x_m, z_m)`, strict role integers, and the existing `depth_to_level_points` geometry helper.
- Produces: `SurfaceGridConfig`, `SurfacePose`, `SurfaceRoute`, `SurfaceUpdate`, and `SurfaceGrid2D.update_points(...)` / `update_from_frame(...)` for the DDA task.

- [ ] **Step 1: Write the failing state/update tests**

```python
import numpy as np

from blindnav_pc.mobilio_surface import (
    ROLE_HAZARD, ROLE_NON_WALKABLE, ROLE_UNKNOWN, ROLE_WALKABLE,
    SurfaceGrid2D, SurfaceGridConfig, SurfacePose,
)


def test_map_points_quantize_and_preserve_strict_role_precedence():
    grid = SurfaceGrid2D(SurfaceGridConfig(cell_m=0.2))
    points = np.array([[0.04, 1.01], [0.05, 1.02], [0.06, 1.03]], dtype=float)
    roles = np.array([ROLE_WALKABLE, ROLE_NON_WALKABLE, ROLE_HAZARD], dtype=np.uint8)
    update = grid.update_points(points, roles, frame_id=7)
    assert update.n_points == 3
    assert grid.cell_role(*grid.world_to_cell(0.05, 1.02)) == ROLE_HAZARD


def test_unknown_is_not_created_as_walkable():
    grid = SurfaceGrid2D()
    grid.update_points(np.array([[0.0, 1.0]]), np.array([ROLE_UNKNOWN], dtype=np.uint8), 1)
    assert grid.cell_role(*grid.world_to_cell(0.0, 1.0)) == ROLE_UNKNOWN


def test_reset_removes_session_map():
    grid = SurfaceGrid2D()
    grid.update_points(np.array([[0.0, 1.0]]), np.array([ROLE_WALKABLE], dtype=np.uint8), 1)
    grid.reset()
    assert grid.cell_role(*grid.world_to_cell(0.0, 1.0)) == ROLE_UNKNOWN
```

- [ ] **Step 2: Run the focused tests to verify they fail**

Run: `../blindnav-app/pc/.venv/bin/pytest -q pc/tests/test_mobilio_surface.py`

Expected: collection failure because `blindnav_pc.mobilio_surface` does not yet exist.

- [ ] **Step 3: Implement the minimal grid/update contract**

Add these concrete definitions before adding ray search:

```python
ROLE_UNKNOWN, ROLE_WALKABLE, ROLE_HAZARD, ROLE_NON_WALKABLE = 0, 1, 2, 3

@dataclass(frozen=True)
class SurfaceGridConfig:
    cell_m: float = 0.20
    half_extent_m: float = 15.0
    ground_band_m: float = 0.15
    max_range_m: float = 15.0
    evidence_decay: float = 0.90
    stale_after_frames: int = 15

@dataclass(frozen=True)
class SurfacePose:
    x_m: float
    z_m: float
    yaw_rad: float

@dataclass(frozen=True)
class SurfaceUpdate:
    n_points: int
    n_cells: int
    n_unknown: int

class SurfaceGrid2D:
    def __init__(self, cfg: SurfaceGridConfig | None = None): ...
    def reset(self) -> None: ...
    def world_to_cell(self, x_m: float, z_m: float) -> tuple[int, int]: ...
    def cell_role(self, ix: int, iz: int) -> int: ...
    def update_points(self, points_map: np.ndarray, roles: np.ndarray,
                      frame_id: int, confidence: np.ndarray | None = None) -> SurfaceUpdate: ...
    def update_from_frame(self, roles: np.ndarray, depth_m: np.ndarray, K: np.ndarray,
                          up: np.ndarray, camera_height_m: float, pose: SurfacePose,
                          frame_id: int, pixel_stride: int = 4) -> SurfaceUpdate: ...
```

Use a dictionary keyed by `(ix, iz)` so empty/missing cells remain unknown. In `update_points`, reject non-finite/out-of-window points, accumulate role scores, and resolve equal-time conflicts with `HAZARD > NON_WALKABLE > WALKABLE > UNKNOWN`. In `update_from_frame`, call `depth_to_level_points`, keep `abs(height) <= ground_band_m`, transform `(lat, forward)` using `pose.yaw_rad`, and pass the resulting map points to `update_points`.

- [ ] **Step 4: Run the focused tests to verify they pass**

Run: `../blindnav-app/pc/.venv/bin/pytest -q pc/tests/test_mobilio_surface.py`

Expected: 3 passed.

- [ ] **Step 5: Commit the state/update unit**

```bash
git add pc/blindnav_pc/mobilio_surface.py pc/tests/test_mobilio_surface.py
git commit -m "feat: add Mobilio surface grid state"
```

### Task 2: Surface DDA route search

**Files:**
- Modify: `pc/blindnav_pc/mobilio_surface.py`
- Modify: `pc/tests/test_mobilio_surface.py`

**Interfaces:**
- Consumes: `SurfaceGrid2D`, `SurfacePose`, and roles from Task 1.
- Produces: `SurfaceRoute` and `SurfaceGrid2D.raycast_route(pose, target_angle_rad=0.0)`.

- [ ] **Step 1: Add failing DDA and tie-break tests**

```python
from blindnav_pc.mobilio_surface import (
    ROLE_NON_WALKABLE, ROLE_UNKNOWN, ROLE_WALKABLE,
    SurfaceGrid2D, SurfacePose,
)


def _paint(grid, xs, zs, role):
    grid.update_points(np.array(list(zip(xs, zs)), dtype=float),
                       np.full(len(xs), role, dtype=np.uint8), frame_id=1)


def test_raycast_stops_at_non_walkable_and_reports_observed_distance():
    grid = SurfaceGrid2D()
    _paint(grid, [0.1, 0.1, 0.1], [0.3, 0.5, 0.7], ROLE_WALKABLE)
    _paint(grid, [0.1], [0.9], ROLE_NON_WALKABLE)
    route = grid.raycast_route(SurfacePose(0.1, 0.1, 0.0), target_angle_rad=0.0)
    assert route.observed_distance_m < 1.2
    assert route.terminated_role == ROLE_NON_WALKABLE


def test_unknown_gap_is_flagged_and_never_fully_observed():
    grid = SurfaceGrid2D()
    _paint(grid, [0.1, 0.1, 0.1, 0.1], [0.3, 0.5, 0.9, 1.1], ROLE_WALKABLE)
    route = grid.raycast_route(SurfacePose(0.1, 0.1, 0.0), target_angle_rad=0.0)
    assert route.unknown_cells > 0
    assert not route.fully_observed


def test_equal_length_candidates_prefer_target_angle():
    grid = SurfaceGrid2D()
    # symmetric walkable fan; a target angle of +10 degrees must win a tie.
    for angle in (-10.0, 10.0):
        theta = np.deg2rad(angle)
        pts = np.array([[np.sin(theta) * r, np.cos(theta) * r]
                        for r in np.arange(0.3, 5.6, 0.2)])
        _paint(grid, pts[:, 0], pts[:, 1], ROLE_WALKABLE)
    route = grid.raycast_route(SurfacePose(0.0, 0.0, 0.0), np.deg2rad(10.0))
    assert abs(np.rad2deg(route.angle_rad) - 10.0) <= 1.1
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `../blindnav-app/pc/.venv/bin/pytest -q pc/tests/test_mobilio_surface.py -k 'raycast or gap or length'`

Expected: failure because `SurfaceRoute` and `raycast_route` are not defined.

- [ ] **Step 3: Implement deterministic angle scan and DDA**

Implement:

```python
@dataclass(frozen=True)
class SurfaceRoute:
    state: str
    angle_rad: float | None
    observed_distance_m: float
    unknown_cells: int
    terminated_role: int
    candidate_count: int
    fully_observed: bool
    reason: str

def raycast_route(self, pose: SurfacePose, target_angle_rad: float = 0.0) -> SurfaceRoute: ...
```

Generate angles from `target - pi/2` through `target + pi/2` in exactly one-degree increments. Traverse cell centers from the pose with a DDA step no larger than half a cell. A walkable cell extends the observed distance; unknown/missing cells increment a gap counter and terminate after `max_gap_cells`; hazard/non-walkable cells terminate immediately. Keep candidates with observed distance at least `min_candidate_m`, sort by `(-distance, abs(wrapped_angle-target), angle)`, and return the first. Set `state` to `CLEAR` only when the selected candidate is fully observed, `OBSERVE` when a candidate exists with bounded unknown gaps, and `UNKNOWN` when no candidate exists. Return `terminated_role` and `reason` for every branch.

- [ ] **Step 4: Run all surface tests and verify they pass**

Run: `../blindnav-app/pc/.venv/bin/pytest -q pc/tests/test_mobilio_surface.py`

Expected: all surface tests pass.

- [ ] **Step 5: Commit the surface route unit**

```bash
git add pc/blindnav_pc/mobilio_surface.py pc/tests/test_mobilio_surface.py
git commit -m "feat: add surface grid ray search"
```

### Task 3: Occupancy state, transform, and temporal evidence

**Files:**
- Create: `pc/blindnav_pc/mobilio_occupancy.py`
- Create: `pc/tests/test_mobilio_occupancy.py`

**Interfaces:**
- Consumes: body-frame points `(lateral_m, forward_m, height_m)`, `SurfacePose`, frame IDs, and optional visible-free map cell keys.
- Produces: `OccupancyGridConfig`, `OccupancyCellKey`, `OccupancyUpdate`, `OccupancyGrid3D.update_body_points(...)`, and `OccupancyGrid3D.reset()`.

- [ ] **Step 1: Write failing transform/filter/decay tests**

```python
import numpy as np

from blindnav_pc.mobilio_occupancy import (
    OccupancyGrid3D, OccupancyGridConfig, OccupancyCellKey,
)
from blindnav_pc.mobilio_surface import SurfacePose


def test_body_point_transform_respects_pose_yaw():
    grid = OccupancyGrid3D(OccupancyGridConfig(cell_m=0.1, height_cell_m=0.1))
    pose = SurfacePose(2.0, 3.0, np.pi / 2.0)
    grid.update_body_points(np.array([[0.0, 1.0, 0.8]]), pose, frame_id=1)
    key = grid.keys()[0]
    assert key.ix == 30  # body-forward becomes +map-x at yaw +90 degrees
    assert key.iz == 20
    assert key.ih == 8


def test_height_and_range_filter_rejects_ground_below_and_far_points():
    grid = OccupancyGrid3D()
    pose = SurfacePose(0.0, 0.0, 0.0)
    update = grid.update_body_points(
        np.array([[0.0, 1.0, -0.1], [0.0, 5.0, 0.8], [0.0, 1.0, 0.8]]),
        pose, frame_id=1)
    assert update.n_accepted == 1


def test_visible_free_cell_decays_but_unobserved_cell_does_not_disappear():
    grid = OccupancyGrid3D(OccupancyGridConfig(decay=0.9, remove_below=0.1))
    pose = SurfacePose(0.0, 0.0, 0.0)
    grid.update_body_points(np.array([[0.0, 1.0, 0.8]]), pose, frame_id=1)
    key = grid.keys()[0]
    before = grid.evidence(key)
    grid.update_body_points(np.zeros((0, 3)), pose, frame_id=2,
                            visible_free_cells=[key])
    assert grid.evidence(key) == before * 0.9
    grid.update_body_points(np.zeros((0, 3)), pose, frame_id=3)
    assert grid.evidence(key) > 0.0
```

- [ ] **Step 2: Run the focused occupancy tests to verify they fail**

Run: `../blindnav-app/pc/.venv/bin/pytest -q pc/tests/test_mobilio_occupancy.py`

Expected: collection failure because `blindnav_pc.mobilio_occupancy` does not yet exist.

- [ ] **Step 3: Implement the map state and evidence update**

Add these exact public types and defaults:

```python
@dataclass(frozen=True)
class OccupancyGridConfig:
    cell_m: float = 0.10
    height_cell_m: float = 0.10
    max_range_m: float = 4.0
    immediate_range_m: float = 1.5
    padded_range_m: float = 2.0
    person_radius_m: float = 0.20
    person_height_m: float = 1.70
    evidence_hit: float = 1.0
    confirm_score: float = 2.0
    confirm_frames: int = 2
    window_frames: int = 3
    decay: float = 0.90
    remove_below: float = 0.10

@dataclass(frozen=True)
class OccupancyCellKey:
    ix: int
    iz: int
    ih: int

@dataclass(frozen=True)
class OccupancyUpdate:
    n_input: int
    n_accepted: int
    n_visible_free: int

class OccupancyGrid3D:
    def __init__(self, cfg: OccupancyGridConfig | None = None): ...
    def reset(self) -> None: ...
    def update_body_points(self, points_body: np.ndarray, pose: SurfacePose,
                           frame_id: int, visible_free_cells=()) -> OccupancyUpdate: ...
    def keys(self) -> list[OccupancyCellKey]: ...
    def evidence(self, key: OccupancyCellKey) -> float: ...
```

Use `map_x = pose.x_m + forward*sin(yaw) + lateral*cos(yaw)` and
`map_z = pose.z_m + forward*cos(yaw) - lateral*sin(yaw)`. Filter finite points,
`0 <= height <= person_height_m`, and `0 < hypot(lateral, forward) <= max_range_m`. Store a bounded deque of frame IDs per key so confirmation requires hits in at least `confirm_frames` distinct frames within `window_frames`; apply decay only to explicitly visible-free keys.

- [ ] **Step 4: Run the occupancy state tests and verify they pass**

Run: `../blindnav-app/pc/.venv/bin/pytest -q pc/tests/test_mobilio_occupancy.py`

Expected: 3 passed.

- [ ] **Step 5: Commit the occupancy state unit**

```bash
git add pc/blindnav_pc/mobilio_occupancy.py pc/tests/test_mobilio_occupancy.py
git commit -m "feat: add persistent occupancy evidence grid"
```

### Task 4: Occupancy clearance search and candidate fusion

**Files:**
- Modify: `pc/blindnav_pc/mobilio_occupancy.py`
- Create: `pc/blindnav_pc/mobilio_fusion.py`
- Create: `pc/tests/test_mobilio_fusion.py`
- Modify: `pc/tests/test_mobilio_occupancy.py`

**Interfaces:**
- Consumes: Task 3 occupancy cells and Task 2 `SurfaceRoute`.
- Produces: `OccupancyRisk`, `OccupancyRoute`, `OccupancyGrid3D.raycast_clearance(...)`, `MobilioDecision`, and `fuse(surface, occupancy)`.

- [ ] **Step 1: Write failing obstacle/route/fusion tests**

```python
from blindnav_pc.mobilio_fusion import fuse
from blindnav_pc.mobilio_occupancy import OccupancyGrid3D, OccupancyRoute
from blindnav_pc.mobilio_surface import SurfacePose, SurfaceRoute


def test_two_frame_body_intersection_becomes_blocked():
    grid = OccupancyGrid3D()
    pose = SurfacePose(0.0, 0.0, 0.0)
    point = np.array([[0.0, 1.0, 0.8]])
    grid.update_body_points(point, pose, frame_id=1)
    first = grid.raycast_clearance(pose)
    grid.update_body_points(point, pose, frame_id=2)
    second = grid.raycast_clearance(pose)
    assert first.state == "OBSERVE"
    assert second.state == "BLOCKED"


def test_confirmed_obstacle_overrides_surface_route():
    surface = SurfaceRoute("CLEAR", 0.0, 6.0, 0, 0, 1, True, "walkable")
    occupancy = OccupancyRoute("BLOCKED", 0.0, 0.0, 1, 0, True, "confirmed")
    decision = fuse(surface, occupancy)
    assert decision.state == "BLOCKED"
    assert "occupancy" in decision.reason


def test_unknown_surface_does_not_become_clear_from_empty_occupancy():
    surface = SurfaceRoute("UNKNOWN", None, 0.0, 3, 0, 0, False, "no observed ray")
    occupancy = OccupancyRoute("UNKNOWN", 0.0, 0.0, 0, 3, False, "unobserved")
    assert fuse(surface, occupancy).state == "UNKNOWN"
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `../blindnav-app/pc/.venv/bin/pytest -q pc/tests/test_mobilio_occupancy.py pc/tests/test_mobilio_fusion.py`

Expected: failures because clearance and fusion types are absent.

- [ ] **Step 3: Implement occupancy ray search and monotone fusion**

Add these public results:

```python
@dataclass(frozen=True)
class OccupancyRoute:
    state: str
    angle_rad: float
    clear_distance_m: float
    blocking_cells: int
    unknown_cells: int
    confirmed: bool
    reason: str

@dataclass(frozen=True)
class MobilioDecision:
    state: str
    angle_rad: float | None
    guidance: str | None
    reason: str
    surface_state: str
    occupancy_state: str
```

`raycast_clearance` scans target +/-90 degrees at one degree, inflates each horizontal cell by `person_radius_m`, and uses `confirmed` occupancy cells within 1.5 m to produce `BLOCKED`; an unconfirmed intersection produces `OBSERVE`. A route with no observed-free evidence produces `UNKNOWN`, never `CLEAR`. `fuse` must order states `BLOCKED > UNKNOWN > OBSERVE > CLEAR`, retain the surface angle unless occupancy selects a safe alternative, and derive guidance as `LEFT`, `RIGHT`, or `STRAIGHT` from the selected relative angle.

- [ ] **Step 4: Run focused and full unit tests**

Run: `../blindnav-app/pc/.venv/bin/pytest -q pc/tests/test_mobilio_occupancy.py pc/tests/test_mobilio_fusion.py`

Expected: all new occupancy/fusion tests pass.

- [ ] **Step 5: Commit the occupancy route and fusion unit**

```bash
git add pc/blindnav_pc/mobilio_occupancy.py pc/blindnav_pc/mobilio_fusion.py \
  pc/tests/test_mobilio_occupancy.py pc/tests/test_mobilio_fusion.py
git commit -m "feat: add occupancy clearance and Mobilio fusion"
```

### Task 5: Same-input SANPO A/B evaluator

**Files:**
- Create: `pc/tools/mobilio_sanpo_ab.py`
- Create: `pc/tests/test_mobilio_sanpo_ab.py`

**Interfaces:**
- Consumes: `SanpoSession`, `load_v5`, `predict_v5`, existing DAV2/ground calibration, `responsibility_split_metrics`, Task 1–4 modules, and the recorded route frame helpers.
- Produces: `evaluate_session(...) -> list[dict]`, `write_per_frame_csv(...)`, `write_metrics_csv(...)`, `write_summary(...)`, and CLI `main(argv) -> int`.

- [ ] **Step 1: Add failing in-memory alignment and GT-isolation tests**

```python
from blindnav_pc.mobilio_sanpo_ab import (
    candidate_frame_keys, make_candidate_row, resolve_condition_depth,
)


def test_candidate_and_baseline_keys_are_identical():
    baseline = [{"session": "s", "frame": 1}, {"session": "s", "frame": 2}]
    candidate = [{"session": "s", "frame": 1}, {"session": "s", "frame": 2}]
    assert candidate_frame_keys(baseline) == candidate_frame_keys(candidate)


def test_production_depth_resolver_does_not_read_gt():
    fit = type("Fit", (), {"ok": True, "a": 1.0, "b": 0.0, "up": None, "confidence": 0.7})()
    resolved = resolve_condition_depth("production", q_dav2=np.ones((2, 2)), fit=fit,
                                      gt_depth=np.full((2, 2), 99.0))
    assert not np.all(resolved.depth_m == 99.0)
    assert resolved.source == "dav2_calibrated"


def test_candidate_row_contains_same_gt_denominators_as_baseline():
    gt_ref = {"plane_ok": True, "gt_road_in_path": True, "gt_curb_in_path": False,
              "gt_occupancy_in_path": False, "confirmed_clear": False}
    row = make_candidate_row("production", "mobilio", "s", 1, gt_ref=gt_ref,
                             decision=type("D", (), {"state": "BLOCKED", "guidance": None,
                             "reason": "test", "angle_rad": 0.0})())
    assert row["session"] == "s" and row["frame"] == 1
    assert row["gt_road_in_path"] is True
```

- [ ] **Step 2: Run the evaluator tests to verify they fail**

Run: `../blindnav-app/pc/.venv/bin/pytest -q pc/tests/test_mobilio_sanpo_ab.py`

Expected: collection failure because the new evaluator module does not exist.

- [ ] **Step 3: Implement shared per-frame loop and row contract**

The CLI must set `sys.path` exactly as the existing responsibility-split evaluator does, then import model/GT helpers without modifying them. Implement:

```python
BASELINE_ARM = "baseline_split_c"
CANDIDATE_ARM = "mobilio"
CONDITIONS = ("annotated", "production")

@dataclass(frozen=True)
class ResolvedDepth:
    depth_m: np.ndarray | None
    up: np.ndarray | None
    camera_height_m: float
    ok: bool
    source: str
    confidence: float | None

def resolve_condition_depth(condition: str, q_dav2, fit, gt_depth) -> ResolvedDepth: ...
def candidate_frame_keys(rows: Sequence[dict]) -> list[tuple[str, int]]: ...
def make_candidate_row(condition: str, arm: str, session: str, frame: int,
                       *, gt_ref: dict, decision, surface_route=None,
                       occupancy_route=None, calib_ok: bool = True,
                       depth_source: str = "unknown") -> dict: ...
def evaluate_session(session: SanpoSession, model, device: str, depth_estimator,
                     cfg: Config, conditions: Sequence[str],
                     limit_per_session: int = 0) -> list[dict]: ...
```

In `evaluate_session`, obtain `indices = session.available_frames()` once, call v5/DAV2 once per frame for production, compute the GT reference once for scoring, and feed identical `cls/depth/up/pose` products to both arms. Use `route_frame(session, indices)` for map positions/headings; create `SurfacePose(pos[k,0], pos[k,1], headings[k])`; set the target relative angle to the route tangent minus recorded heading. Reset one `SurfaceGrid2D`, one `OccupancyGrid3D`, one existing baseline PathKeeper/ObstacleGuardian/SafetyCoordinator set per condition at session start. Build baseline rows with the existing spatial Obstacle Guardian path, and candidate rows with the new surface/occupancy/fusion path.

The row starts from `responsibility_split_eval.PER_FRAME_FIELDS`, fills the same GT fields, calls `responsibility_split_metrics.add_row_flags`, then adds candidate diagnostics: `surface_state`, `surface_distance_m`, `surface_unknown_cells`, `surface_fully_observed`, `occupancy_state`, `occupancy_clear_distance_m`, `occupancy_blocking_cells`, `occupancy_unknown_cells`, `pose_mode`, and `depth_source`. Production must pass `gt_ref` only after the decision has been made.

- [ ] **Step 4: Implement output writers and CLI**

Add CLI arguments `--data-root`, `--sessions`, `--camera`, `--v5-checkpoint`, `--out`, `--conditions`, `--max-width`, `--limit-per-session`, `--save-failures`, `--device`, and `--seed`, with output default `pc/out/mobilio_sanpo_ab_20261106`. Use `responsibility_split_metrics.summarize`, `metrics_csv_rows`, and `render_summary_md` so the baseline/candidate metrics retain the old denominators. Write `per_frame.csv`, `metrics.csv`, `summary.json`, `summary.md`, and `run_metadata.json`. Add a hard check that candidate and baseline frame keys match per condition and that the full run has 481 unique frame keys when no limit is supplied.

- [ ] **Step 5: Run evaluator unit tests and commit**

Run: `../blindnav-app/pc/.venv/bin/pytest -q pc/tests/test_mobilio_sanpo_ab.py`

Expected: all alignment and GT-isolation tests pass.

```bash
git add pc/tools/mobilio_sanpo_ab.py pc/tests/test_mobilio_sanpo_ab.py
git commit -m "feat: add same-input Mobilio SANPO evaluator"
```

### Task 6: Four-panel visualization and failure evidence

**Files:**
- Create: `pc/tools/mobilio_viz.py`
- Modify: `pc/tools/mobilio_sanpo_ab.py`

**Interfaces:**
- Consumes: saved per-frame rows, RGB frames, v5 role maps, surface grid snapshot/rays, occupancy snapshot/rays, and the explicit condition/pose labels.
- Produces: `render_contact_sheet(cases, out_path, ...)` and optional per-case PNGs under the ignored output directory.

- [ ] **Step 1: Add a small deterministic renderer test**

```python
def test_contact_sheet_has_four_panels(tmp_path):
    out = tmp_path / "sheet.png"
    render_contact_sheet([minimal_case()], str(out))
    image = cv2.imread(str(out))
    assert image is not None
    assert image.shape[0] >= 2 * 120
    assert image.shape[1] >= 2 * 160
```

- [ ] **Step 2: Run the renderer test to verify it fails**

Run: `../blindnav-app/pc/.venv/bin/pytest -q pc/tests/test_mobilio_viz.py`

Expected: failure until the renderer exists.

- [ ] **Step 3: Implement the four panels**

Use OpenCV/Matplotlib already present in the PC environment. Render RGB with strict role colors, map-frame surface cells and selected rays, top-down occupancy cells/body footprint, and a text panel containing `condition`, `arm`, `SANPO recorded pose (offline VIO proxy)`, `depth_source`, state, reason, and the GT diagnostic counters. Use deterministic case ordering and filenames; never infer a safety claim from the image.

- [ ] **Step 4: Wire failure selection and run the renderer test**

Select at most the requested number of production failures first, then annotated cases, preserving `(condition, arm, session, frame)` in the manifest. Run the focused visualization tests and commit:

```bash
git add pc/tools/mobilio_viz.py pc/tools/mobilio_sanpo_ab.py pc/tests/test_mobilio_viz.py
git commit -m "feat: render Mobilio A/B evidence sheets"
```

### Task 7: Integration verification, SANPO run, and reviewed report

**Files:**
- Modify: `docs/assets/mobilio_sanpo_ab_20261106/summary.md` (after the run)
- Create: `docs/assets/mobilio_sanpo_ab_20261106/metrics.csv` (after the run)
- Create: `docs/assets/mobilio_sanpo_ab_20261106/summary.json` (after the run)
- Create: `docs/assets/mobilio_sanpo_ab_20261106/contact_sheet.png` (after the run)

**Interfaces:**
- Consumes: all prior modules/tests, `/Users/mima0000/blindnav-app/pc/data/sanpo_real/sanpo-real`, `/Users/mima0000/blindnav-real-eval/models/segformer_sanpo_b0_v5.pt`, and local DAV2/HF caches.
- Produces: verified evidence for the 481-frame production/annotated A/B comparison and a concise report that distinguishes measured results from proxies.

- [ ] **Step 1: Run the full unit suite before real data**

Run: `../blindnav-app/pc/.venv/bin/pytest -q`

Expected: the original 418-test baseline plus all new unit/integration tests pass; `reference_check` tests remain green.

- [ ] **Step 2: Run a bounded real-data smoke test**

```bash
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 \
../blindnav-app/pc/.venv/bin/python pc/tools/mobilio_sanpo_ab.py \
  --v5-checkpoint /Users/mima0000/blindnav-real-eval/models/segformer_sanpo_b0_v5.pt \
  --data-root /Users/mima0000/blindnav-app/pc/data/sanpo_real/sanpo-real \
  --conditions annotated production --limit-per-session 2 \
  --save-failures 2 --out pc/out/mobilio_sanpo_ab_smoke
```

Expected: both arms share the same six `(session, frame)` keys per condition, output files are readable, and no GT depth enters the production candidate decision.

- [ ] **Step 3: Run the full 481-frame production/annotated comparison**

```bash
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 \
../blindnav-app/pc/.venv/bin/python pc/tools/mobilio_sanpo_ab.py \
  --v5-checkpoint /Users/mima0000/blindnav-real-eval/models/segformer_sanpo_b0_v5.pt \
  --data-root /Users/mima0000/blindnav-app/pc/data/sanpo_real/sanpo-real \
  --conditions annotated production --save-failures 12 \
  --out pc/out/mobilio_sanpo_ab_20261106
```

Expected: 481 unique frame keys per condition, two arms per key, machine-readable metrics with denominators, and contact-sheet/failure manifests. A missing model/cache or data path is reported as `BLOCKED_MISSING_*` and is not converted into a result.

- [ ] **Step 4: Inspect and copy only reviewed small artifacts**

Verify with:

```bash
python - <<'PY'
import csv, json
from pathlib import Path
p = Path("pc/out/mobilio_sanpo_ab_20261106")
rows = list(csv.DictReader((p / "per_frame.csv").open()))
assert len({(r["condition"], r["session"], r["frame"]) for r in rows}) == 2 * 481
assert {r["arm"] for r in rows} == {"baseline_split_c", "mobilio"}
json.load((p / "summary.json").open())
print("verified", len(rows), "rows")
PY
```

Review the summary for candidate regressions, unknown share, false stops, and latency before copying `summary.md`, `metrics.csv`, `summary.json`, and `contact_sheet.png` into `docs/assets/mobilio_sanpo_ab_20261106/`. Do not copy raw failure images or data.

- [ ] **Step 5: Run the full suite after report generation and commit the evidence**

Run: `../blindnav-app/pc/.venv/bin/pytest -q`

Expected: all tests still pass and the report-generation step has not changed any frozen source or reference output.

```bash
git add docs/assets/mobilio_sanpo_ab_20261106
git commit -m "docs: record Mobilio SANPO A/B results"
```

### Review checkpoints

* After Task 2: review surface-cell semantics and unknown-gap handling before adding occupancy state.
* After Task 4: review that `BLOCKED` occupancy overrides surface guidance and that empty/unobserved space remains unknown.
* After Task 5: review frame-key equality and production GT isolation before the 2 GB replay.
* After Task 7: review the summary and visual contact sheet; only then report whether Mobilio-inspired mapping improved any metric.
