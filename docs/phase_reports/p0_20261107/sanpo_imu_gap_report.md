# SANPO IMU gap report (P0, 2026-11-07)

## Finding

The frozen audit covers three SANPO-Real sessions and 481 replay frames. All
three sessions expose corrected `fixed_camera_poses.csv` files, but the local
file scan recorded zero IMU observation files for each session. The declared
sensor metadata contains accelerometer/gyroscope capability descriptions; that
metadata is not an IMU time series and cannot be used to run VIO.

| session | replay frames | corrected pose | IMU observation files | status |
|---|---:|---|---:|---|
| `-PqSDmiEe2pXjmYHgxh4YEBsj0T5LU10` | 121 | yes | 0 | NOT_EVALUABLE for RGB+IMU VIO |
| `M5UIabsltWCYg1D9LCXLoNPO3bMUo_5-` | 184 | yes | 0 | NOT_EVALUABLE for RGB+IMU VIO |
| `UGNwBEZMxhzlzXVyl8XUfflcvjGwdpA5` | 176 | yes | 0 | NOT_EVALUABLE for RGB+IMU VIO |

The existing SANPO audit JSON is
`pc/out/sanpo_imu_audit_20261106.json`. Its local root is intentionally not
copied into this report because private machine paths are not reviewable.

## Official-data discrepancy

The official SANPO repository documents that SANPO-Real sessions contain IMU
data and that corrected camera poses are in `fixed_camera_poses.csv`. This
checkout has not resolved whether the missing files are a selective-download
issue, an object-path issue, or a difference between the downloaded session
subset and the full release. A live GCS inventory could not be performed in
this phase because `gcloud` is not installed.

## Decision

Do not run SANPO RGB+IMU VIO and do not label recorded pose as VIO. P1 may use
TUM-VI/EuRoC only after their source, license, checksum and one-sequence
download are recorded. SANPO VIO remains `NOT_EVALUABLE` until an actual IMU
CSV/JSON stream is found and timestamp/calibration checks pass.
