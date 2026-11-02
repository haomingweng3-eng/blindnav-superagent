# Baseline Before Real-Data Round — 2026-11-03

Frozen before the real-data optimization round. Code state: `main` at
`BASELINE_20261103` plus governance/Actions bridge.

- Tests: **173 tests, 0 failures, 0 errors**
- Build: `:app:assembleDebug` BUILD SUCCESSFUL
- APK: `app/build/outputs/apk/debug/app-debug.apk`
- SHA-256: `dba67e30dd42929da4e9cf3b618495cb2a7e7cd139fdefba85bfdbd32b7cd13e`
- Size: 586,330,484 bytes
- APK mtime: 2026-11-03 01:28:05
- Git: clean working tree before this round

## Capability baseline

| Capability | Evidence | Status |
|---|---|---|
| Walking | desktop synthetic + SANPO real frames | YELLOW/RED: tactile-only corridor 0/45 SANPO frames |
| Obstacle | synthetic + UrbanEgo/BLV real frames | YELLOW/RED: UrbanEgo recall 36.4%, BLV macro recall 41.8% |
| Last10m Search/Lock | JVM tests | DESKTOP_VERIFIED, device not re-verified |
| Last10m Entrance | code audit | RED: automatic proposal not connected |
| Scene/Text | code audit | RED: not connected |

Known REDs: general traversability, BLV object coverage, automatic door
proposal, scene/text production wiring.
