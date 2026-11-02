# BlindNav core_v1 frozen cases

These cases are the frozen synthetic set used for the 2026-11-03 desktop policy
evaluation. They exercise the production policy classes without Android camera
or model inference.

- Obstacle cases: O1 chair in corridor, O2 wall close, O3 bicycle outside,
  O4 person crossing, O5 approaching obstacle, O6 static desk, O7 door/wall
  close, O8 unknown object in corridor, plus single-frame noise.
- Walking cases: straight, true left drift, true right drift, phone rotation
  only, oscillation, recovery, corridor unavailable.
- Last10m and Scene/Text cases reuse existing `app/src/test/resources` and
  `app/src/test/java/.../scene` suites; they are not duplicated here.

Status: DESKTOP_SIMULATION. Not device-verified.
