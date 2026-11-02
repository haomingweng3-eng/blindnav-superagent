# Device Test — 2026-11-03

APK: `app/build/outputs/apk/debug/app-debug.apk`
SHA-256: `2d19236e352b1729ba650e5f161349431068962a4af187b47ac9e66a76a48e21`
Size: 586,069,616 bytes

Run these 8 actions only. Keep logcat and one screenshot per action.

1. **Desk**: stand still facing a laptop/desk for 10 s. Expected: no repeated
   “前面很近，先停下”. If it repeats, record time and log tag `BlindNavFocus`.
2. **Straight corridor**: walk a clear corridor for 30 s. Expected: no more than
   3 LEFT/RIGHT flips; most of the time KEEP.
3. **Phone yaw**: stand still, rotate the phone ±30° left/right. Expected: no
   false LEFT/RIGHT command.
4. **True drift**: walk with a real 0.5–1 m lateral offset. Expected: one clear
   LEFT/RIGHT correction within ~1 s; return to KEEP after recentering.
5. **Chair blocking**: put a chair in the corridor and approach. Expected: STOP
   within 1 s; remove chair, release within 2 s.
6. **Wall/door close**: stand close to a side wall or door. Expected: no STOP.
7. **Target search**: mount Last10m with KFC or 瑞幸; point at the sign. Expected:
   target found; no arrival from the sign alone.
8. **Neighbor door**: with target locked, turn to a neighboring door. Expected:
   `UNKNOWN` / `REOBSERVE`, never ARRIVAL.

Capture `adb logcat -d` after each action. Do not walk blind in traffic; use a
safe indoor corridor or a companion.
