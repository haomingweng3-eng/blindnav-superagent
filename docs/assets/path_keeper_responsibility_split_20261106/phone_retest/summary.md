# Independent re-test on local annotated phone clips

Clips: 35 (610 sampled frames at 2.0 fps; 31 safe, 3 near_miss, 1 conflict).

**Label capability is stated before any number.** The annotation table is video-level; it has no per-frame segmentation, depth, pose or 3D occupancy GT. Metrics that need frame-level GT are NOT_EVALUABLE here and no substitute was improvised.

| metric | status | why |
|---|---|---|
| curb/road danger miss | **NOT_EVALUABLE** | video-level labels only; no per-frame curb/road segmentation or geometry GT |
| 3D occupancy danger miss | **NOT_EVALUABLE** | video-level labels only; no per-frame 3D occupancy / object boxes GT |
| true false-stop (confirmed clear path) | **NOT_EVALUABLE** | no per-frame clear-path GT; a safe video label does not make each frame clear |
| latency to first hazard warning | **NOT_EVALUABLE** | only a coarse video-level onset time for 4 clips; no frame-level hazard onset |
| effective guidance coverage | **PROXY** | GT-free system output share, but corridor geometry is assumed (no IMU/pose/VIO) |
| UNKNOWN share | **PROXY** | GT-free system output share under the same assumed corridor geometry |
| decision flips | **PROXY** | GT-free system output statistic under the same assumed corridor geometry |

Evidence level: **PROTOTYPE** for the proxy statistics (desktop run on phone clips with an assumed straight corridor, fixed 15 deg pitch and assumed camera height; no IMU, no pose, no VIO). Not DEVICE_VERIFIED, not USER_TESTED; no safety claim.

## GT-free output statistics (PROXY)

| arm | frames | guidance coverage | warning share | UNKNOWN share | OBSERVE share | BLOCKED share | flips |
|---|---:|---:|---:|---:|---:|---:|---:|
| A | 610 | 21.3 % | 78.7 % | 10.7 % | 0.0 % | 68.0 % | 92/575 |
| B | 610 | 21.3 % | 78.7 % | 10.7 % | 36.6 % | 31.5 % | 128/575 |
| C | 610 | 21.1 % | 78.9 % | 14.8 % | 40.0 % | 24.1 % | 145/575 |

### Per status group (`safe` / `near_miss` / `conflict`)

| arm | status | frames | guidance coverage | warning share |
|---|---|---:|---:|---:|
| A | safe | 565 | 20.4 % | 79.6 % |
| A | near_miss | 32 | 31.2 % | 68.8 % |
| A | conflict | 13 | 38.5 % | 61.5 % |
| B | safe | 565 | 20.4 % | 79.6 % |
| B | near_miss | 32 | 31.2 % | 68.8 % |
| B | conflict | 13 | 38.5 % | 61.5 % |
| C | safe | 565 | 20.2 % | 79.8 % |
| C | near_miss | 32 | 31.2 % | 68.8 % |
| C | conflict | 13 | 38.5 % | 61.5 % |

## Descriptive onset proxy (NOT a latency metric)

The annotation gives only one coarse onset time for the four near-miss / conflict clips. The table below is descriptive; the formal latency metric remains NOT_EVALUABLE.

| clip | status | note time (s) | arm | first warning (s) | warning<=note+0.5s |
|---|---|---:|---|---:|---|
| VID_20260922_170345.mp4 | near_miss | 2.5 | A | 1.5 | True |
| VID_20260922_170345.mp4 | near_miss | 2.5 | B | 1.5 | True |
| VID_20260922_170345.mp4 | near_miss | 2.5 | C | 1.5 | True |
| VID_20260922_170405.mp4 | near_miss | 2.5 | A | 3.0 | True |
| VID_20260922_170405.mp4 | near_miss | 2.5 | B | 3.0 | True |
| VID_20260922_170405.mp4 | near_miss | 2.5 | C | 3.0 | True |
| VID_20260922_170500.mp4 | near_miss | 4.0 | A | 0.0 | True |
| VID_20260922_170500.mp4 | near_miss | 4.0 | B | 0.0 | True |
| VID_20260922_170500.mp4 | near_miss | 4.0 | C | 0.0 | True |
| VID_20260922_170525.mp4 | conflict | 3.0 | A | 0.5 | True |
| VID_20260922_170525.mp4 | conflict | 3.0 | B | 0.5 | True |
| VID_20260922_170525.mp4 | conflict | 3.0 | C | 0.5 | True |

Remaining limitations: no IMU/pose/VIO in the clips, so the corridor is a straight-line assumption and absolute LEFT/RIGHT guidance is not validated; camera height / pitch are assumed, so metric distances can be biased; 2 fps sampling can miss short events; none of these clips was used for design, so this is an independent but weak (GT-free) re-test.
