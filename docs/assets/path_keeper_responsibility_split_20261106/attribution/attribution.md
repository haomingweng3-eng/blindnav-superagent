# Dangerous-pass layer attribution (production condition, arm C)

Frames where the arm emitted forward guidance while GT curb/road/3D occupancy lay in the body swept path. Each missed GT pixel is classified by the production input chain: `segmentation` = v5 mask says SAFE; `geometry` = v5 non-walkable but metric depth/ground calibration does not place it in the body swept band; `decision` = evidence exists and the decision layer still passed.

Frames requested: 110; attributed: 108; skipped: 2.

| danger kind | GT pixels | segmentation share | geometry share | decision share |
|---|---:|---:|---:|---:|
| occupancy | 14909 | 5.8 % | 86.1 % | 8.1 % |
| road | 5 | 100.0 % | 0.0 % | 0.0 % |
| curb | 893 | 44.0 % | 55.7 % | 0.3 % |

Skipped frames (calibration/plane unavailable): production_calibration_failed:low_confidence+stale, production_calibration_failed:low_confidence+stale

Class distribution at the missed GT pixels: `curb:non`=500, `curb:safe`=393, `occupancy:non`=14042, `occupancy:safe`=867, `road:safe`=5

