# TUM-VI / Basalt source audit (P0.3, 2026-11-07)

## TUM-VI source

The official TUM page exposes the 512x512 Euroc/DSO export directory:

`https://vision.in.tum.de/tumvi/exported/euroc/512_16/`

The same page documents that processed sequences have consistent camera/IMU
timestamps, proper IMU scaling and axis alignment, and ground-truth poses in
the IMU frame. It also gives the `room1` sequence family and says the data is
CC BY 4.0. The page provides `*.md5` sidecars and the verification command
`md5sum -c *.md5`.

The CDN directory and individual file were not downloaded in this phase. A
file-specific MD5 therefore remains **PENDING**, which is the correct state for
the P1 download gate. No large data was fetched.

## Basalt configuration

The upstream Basalt `doc/VioMapping.md` provides the EuRoC invocation shape:

```text
basalt_vio --dataset-path <sequence>/ \
  --cam-calib <basalt-euroc-calibration.json> \
  --dataset-type euroc \
  --config-path <basalt-euroc-config.json> \
  --marg-data <output-dir> \
  --show-gui 1
```

The document states that the EuRoC format contains time-aligned ground truth
in `state_groundtruth_estimate0`, and that Basalt supports the `euroc` dataset
type. This is sufficient to define the P1 command contract, but it is not a
run result.

## Local build dependency check

Read-only checks in the current macOS checkout found:

| dependency | result |
|---|---|
| `clang++` | present |
| `g++` | present |
| `cmake` | present (`4.4.3`) |
| `ninja` | absent |
| `pkg-config` | present |
| Eigen3 via pkg-config | `5.0.1` |
| independent Linux/WSL environment | absent |

No Basalt build was attempted. The project plan explicitly requires the
independent Linux/WSL environment before P1, and this phase did not create one
or modify the BlindNav worktree with a third-party build.

## Gate decision

P0.3 is **PARTIAL**: official source, sequence family, license, MD5 mechanism,
and Basalt command contract are recorded; exact room1 file MD5 and an
independent Linux/WSL build environment are still missing. P1 remains HOLD.

Sources:

- <https://cvg.cit.tum.de/data/datasets/visual-inertial-dataset>
- <https://github.com/VladyslavUsenko/basalt/blob/master/doc/VioMapping.md>
