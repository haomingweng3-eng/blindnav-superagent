# Basalt dependency and environment audit (P0.2, 2026-11-07)

This is a read-only host audit. It does not clone, configure, compile, or
install Basalt in the BlindNav checkout.

## Upstream contract

Basalt's upstream documentation identifies visual-inertial odometry/mapping as
supported tools and gives a EuRoC command using `basalt_vio`, EuRoC calibration,
an EuRoC config, and a marginalization output directory. The same documentation
requires the EuRoC dataset's time-aligned ground truth for trajectory checks.

## Current host checks

| check | result | interpretation |
|---|---|---|
| OS / architecture | macOS ARM64 | not the independent Linux/WSL environment required by P0/P1 |
| C++ compiler | `/usr/bin/clang++`, `/usr/bin/g++` | local compiler commands exist |
| CMake | 4.4.3 | local configure tool exists |
| Ninja | absent | likely build-generator gap |
| pkg-config | present | package discovery command exists |
| Eigen3 via pkg-config | 5.0.1 | header dependency appears discoverable locally |
| Docker / Podman / Colima | absent | no local container fallback available |
| Java runtime | absent (macOS shim only) | Android build remains independently blocked |
| free disk | about 66 MB at audit time | unsafe for a VIO checkout or dataset download |

No Basalt binary is present in the checkout and no build was attempted. The
absence of an independent Linux/WSL environment, a build generator, and free
disk space is recorded as an environment gate, not as an algorithm failure.

## Decision

`P0.2 = HOLD`. The repository now has an explicit dependency checklist, but
P1 cannot honestly begin from this host. The next executable step is to provide
an independent Linux/WSL workspace with CMake, Ninja, a C++ toolchain, Eigen,
Basalt's third-party dependencies, and enough disk for one TUM-VI sequence.
