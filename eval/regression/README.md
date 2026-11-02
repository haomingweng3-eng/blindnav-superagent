# Permanent core regressions

Implemented in `app/src/test/java/com/blindnav/app/regression/CoreRegressionTest.kt`.

| ID | Failure | Test |
|---|---|---|
| REG-001 | Static desk produced repeated near-focus STOP | `reg001_staticDeskDoesNotKeepStopping` |
| REG-002 | KFC must lock within two distinct frames | `reg002_kfcLocksWithinTwoFrames` |
| REG-003 | KFC text must be silent when target is the library | `reg003_kfcIsSilentWhenTargetIsLibrary` |
| REG-004 | Close wall alone must not STOP | `reg004_wallCloseDoesNotStop` |
| REG-005 | Unknown object blocking corridor must STOP | `reg005_unknownObjectInCorridorStops` |
| REG-006 | Neighbor door must never produce ARRIVAL | `reg006_neighborDoorNeverArrives` |

Run:

```bash
JAVA_HOME=/opt/homebrew/opt/openjdk@17 ./gradlew :app:testDebugUnitTest --tests 'com.blindnav.app.regression.CoreRegressionTest'
```
