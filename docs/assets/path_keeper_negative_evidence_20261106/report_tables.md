# P1 Path Keeper — negative-evidence fusion: A/B/C report tables

Generated: 2026-11-06 01:56:28

- Eval output dir: `/Users/mima0000/.dsh/state/superagent/work/negative-evidence-fusion-9bf4ca1477ca/pc/out/corridor_evidence_full`
- Frames: 481 across sessions: `-PqSDmiE`=121, `M5UIabsl`=184, `UGNwBEZM`=176
- Conditions: annotated, production; arms: A, B, C
- Evidence level: **DESKTOP_REAL_DATA** (offline desktop analysis on SANPO-Real; not device-verified, not user-tested, no safety claim)
- PR #15 fixed 23-frame set recovered: 23 frames (reporting label only, never fed to the algorithm)

## Definitions (frozen; explicit numerator/denominator, no tuned entries)

- **Two danger denominators (amendment v1.1, both from oracle geometry + raw SANPO ids in the decision swept body, 1.5-6 m, |lateral| <= 0.4 m):**
  - **GT excluded-ground danger (PR #15 class), primary** — GT non-walkable GROUND points with raw ids {1 road, 2 curb, 16 water, 18 inaccessible, 19 railway, 30 terrain}, >=5 points in the swept body (`danger_ground`).
  - **GT any-negative danger (incl. object/vegetation occupancy), secondary** — `danger_ground` OR GT object occupancy (raw ids {4,7,8,9,10,11,12,13,14,15,20,21,22,23,24,25,26,28} at 0.15 < height <= 2.0 m) OR GT non-walkable-class ground points, >=5 points in the swept body (`danger_any`).
- **false-free (primary)** = `danger_ground` and hint in (STRAIGHT, LEFT, RIGHT) / `danger_ground` frames; **false-free (any)** = `danger_any` and walkable guidance / `danger_any` frames. Primary flags stay on the GT-ground denominator; the PR #15 gate variant restricts the primary numerator to PR #15 road/curb-in-path event frames and uses those frames as denominator.
- **false-stop (primary)** = hint STOP, no `danger_ground` and >=5 GT walkable swept points / frames with no `danger_ground` and >=5 GT walkable swept points; **false-stop (any)** uses `danger_any` for both the flag and the denominator.
- **UNKNOWN**: hint UNKNOWN / all frames.
- **coverage>=min**: tracker coverage >= `min_coverage` 0.5 / all frames; mean coverage is over all frames with a recorded coverage.
- **guidance coverage**: hint in (STRAIGHT, LEFT, RIGHT) / all frames.
- **action flips vs A**: hint differs from arm A on the same (condition, session, frame) / frames joined with arm A; `A->X` pairs are in report_tables.csv.
- **danger blocking recall**: (STOP + UNKNOWN) on GT danger frames / GT danger frames, reported for BOTH denominators with the STOP/UNKNOWN split in the cell.
- **23-frame remain table**: fixed PR #15 set (annotated/A rows with pr15_event and walkable guidance), denominator **23**, applied unchanged to every condition/arm.
- **road/curb-in-path ignored**: PR #15 definitions (>=5 GT road points 1.5-6 m or curb points 2.5-6 m inside the map-frame swept body |lateral| <= 0.6 m while the output is STRAIGHT) / frames where that PR #15 event is present.

## 1. Condition `annotated`

### 1a. Safety / negative-evidence

Both danger denominators are shown side by side (primary = GT excluded-ground danger / PR #15 class; secondary = GT any-negative danger incl. object/vegetation occupancy).

| arm | frames | false-free ground n/den (%) | false-free any n/den (%) | false-stop ground n/den (%) | false-stop any n/den (%) | false-free PR15-gate n/den (%) | danger recall ground n/den (STOP/UNK) | danger recall any n/den (STOP/UNK) | 23-frame walkable/STOP/UNKNOWN (of 23) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| A | 481 | 92/103 (89.3%) | 284/295 (96.3%) | 0/378 (0.0%) | 0/186 (0.0%) | 78/114 (68.4%) | 11/103 (10.7%) (STOP 0, UNK 11) | 11/295 (3.7%) (STOP 0, UNK 11) | 23/0/0 of 23 |
| B | 481 | 0/103 (0.0%) | 6/295 (2.0%) | 188/378 (49.7%) | 2/186 (1.1%) | 0/114 (0.0%) | 103/103 (100.0%) (STOP 103, UNK 0) | 289/295 (98.0%) (STOP 289, UNK 0) | 2/21/0 of 23 |
| C | 481 | 0/103 (0.0%) | 6/295 (2.0%) | 128/378 (33.9%) | 0/186 (0.0%) | 0/114 (0.0%) | 103/103 (100.0%) (STOP 86, UNK 17) | 289/295 (98.0%) (STOP 214, UNK 75) | 2/19/2 of 23 |

### 1b. Usability / coverage / regression events

| arm | frames | UNKNOWN n/den (%) | mean coverage | coverage>=min n/den (%) | guidance coverage n/den (%) | action flips vs A n/den (%) | road-in-path ignored n/den (%) | curb-in-path ignored n/den (%) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| A | 481 | 11/481 (2.3%) | 0.96 | 470/481 (97.7%) | 470/481 (97.7%) | 0/481 (0.0%) | 2/5 (40.0%) | 21/109 (19.3%) |
| B | 481 | 0/481 (0.0%) | 0.96 | 470/481 (97.7%) | 190/481 (39.5%) | 291/481 (60.5%) | 0/5 (0.0%) | 2/109 (1.8%) |
| C | 481 | 77/481 (16.0%) | 0.96 | 470/481 (97.7%) | 190/481 (39.5%) | 290/481 (60.3%) | 0/5 (0.0%) | 2/109 (1.8%) |

## 1. Condition `production`

### 1a. Safety / negative-evidence

Both danger denominators are shown side by side (primary = GT excluded-ground danger / PR #15 class; secondary = GT any-negative danger incl. object/vegetation occupancy).

| arm | frames | false-free ground n/den (%) | false-free any n/den (%) | false-stop ground n/den (%) | false-stop any n/den (%) | false-free PR15-gate n/den (%) | danger recall ground n/den (STOP/UNK) | danger recall any n/den (STOP/UNK) | 23-frame walkable/STOP/UNKNOWN (of 23) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| A | 481 | 47/103 (45.6%) | 208/295 (70.5%) | 0/378 (0.0%) | 0/186 (0.0%) | 37/114 (32.5%) | 56/103 (54.4%) (STOP 0, UNK 56) | 87/295 (29.5%) (STOP 0, UNK 87) | 20/0/3 of 23 |
| B | 481 | 19/103 (18.4%) | 130/295 (44.1%) | 51/378 (13.5%) | 15/186 (8.1%) | 13/114 (11.4%) | 84/103 (81.6%) (STOP 40, UNK 44) | 165/295 (55.9%) (STOP 76, UNK 89) | 8/6/9 of 23 |
| C | 481 | 19/103 (18.4%) | 130/295 (44.1%) | 28/378 (7.4%) | 7/186 (3.8%) | 13/114 (11.4%) | 84/103 (81.6%) (STOP 25, UNK 59) | 165/295 (55.9%) (STOP 46, UNK 119) | 8/3/12 of 23 |

### 1b. Usability / coverage / regression events

| arm | frames | UNKNOWN n/den (%) | mean coverage | coverage>=min n/den (%) | guidance coverage n/den (%) | action flips vs A n/den (%) | road-in-path ignored n/den (%) | curb-in-path ignored n/den (%) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| A | 481 | 107/481 (22.2%) | 0.57 | 375/481 (78.0%) | 374/481 (77.8%) | 0/481 (0.0%) | 2/5 (40.0%) | 15/109 (13.8%) |
| B | 481 | 139/481 (28.9%) | 0.57 | 375/481 (78.0%) | 251/481 (52.2%) | 157/481 (32.6%) | 2/5 (40.0%) | 9/109 (8.3%) |
| C | 481 | 177/481 (36.8%) | 0.57 | 375/481 (78.0%) | 251/481 (52.2%) | 144/481 (29.9%) | 2/5 (40.0%) | 9/109 (8.3%) |

## 2. Per-session breakdown

### Condition `annotated`

| session | arm | frames | false-free ground | false-free any | false-stop ground | false-stop any | UNKNOWN | coverage>=min | guidance coverage | danger recall ground (STOP/UNK) | danger recall any (STOP/UNK) | 23-frame W/S/U (of 23) |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `-PqSDmiE` | A | 121 | 10/21 (47.6%) | 31/42 (73.8%) | 0/100 (0.0%) | 0/79 (0.0%) | 11/121 (9.1%) | 110/121 (90.9%) | 110/121 (90.9%) | 11/21 (52.4%) (STOP 0, UNK 11) | 11/42 (26.2%) (STOP 0, UNK 11) | 3/0/0 of 23 |
| `-PqSDmiE` | B | 121 | 0/21 (0.0%) | 0/42 (0.0%) | 22/100 (22.0%) | 1/79 (1.3%) | 0/121 (0.0%) | 110/121 (90.9%) | 78/121 (64.5%) | 21/21 (100.0%) (STOP 21, UNK 0) | 42/42 (100.0%) (STOP 42, UNK 0) | 1/2/0 of 23 |
| `-PqSDmiE` | C | 121 | 0/21 (0.0%) | 0/42 (0.0%) | 16/100 (16.0%) | 0/79 (0.0%) | 14/121 (11.6%) | 110/121 (90.9%) | 78/121 (64.5%) | 21/21 (100.0%) (STOP 13, UNK 8) | 42/42 (100.0%) (STOP 29, UNK 13) | 1/1/1 of 23 |
| `M5UIabsl` | A | 184 | 0/0 (n/a) | 91/91 (100.0%) | 0/184 (0.0%) | 0/93 (0.0%) | 0/184 (0.0%) | 184/184 (100.0%) | 184/184 (100.0%) | 0/0 (n/a) (STOP 0, UNK 0) | 0/91 (0.0%) (STOP 0, UNK 0) | 0/0/0 of 23 |
| `M5UIabsl` | B | 184 | 0/0 (n/a) | 6/91 (6.6%) | 86/184 (46.7%) | 1/93 (1.1%) | 0/184 (0.0%) | 184/184 (100.0%) | 98/184 (53.3%) | 0/0 (n/a) (STOP 0, UNK 0) | 85/91 (93.4%) (STOP 85, UNK 0) | 0/0/0 of 23 |
| `M5UIabsl` | C | 184 | 0/0 (n/a) | 6/91 (6.6%) | 35/184 (19.0%) | 0/93 (0.0%) | 51/184 (27.7%) | 184/184 (100.0%) | 98/184 (53.3%) | 0/0 (n/a) (STOP 0, UNK 0) | 85/91 (93.4%) (STOP 35, UNK 50) | 0/0/0 of 23 |
| `UGNwBEZM` | A | 176 | 82/82 (100.0%) | 162/162 (100.0%) | 0/94 (0.0%) | 0/14 (0.0%) | 0/176 (0.0%) | 176/176 (100.0%) | 176/176 (100.0%) | 0/82 (0.0%) (STOP 0, UNK 0) | 0/162 (0.0%) (STOP 0, UNK 0) | 20/0/0 of 23 |
| `UGNwBEZM` | B | 176 | 0/82 (0.0%) | 0/162 (0.0%) | 80/94 (85.1%) | 0/14 (0.0%) | 0/176 (0.0%) | 176/176 (100.0%) | 14/176 (8.0%) | 82/82 (100.0%) (STOP 82, UNK 0) | 162/162 (100.0%) (STOP 162, UNK 0) | 1/19/0 of 23 |
| `UGNwBEZM` | C | 176 | 0/82 (0.0%) | 0/162 (0.0%) | 77/94 (81.9%) | 0/14 (0.0%) | 12/176 (6.8%) | 176/176 (100.0%) | 14/176 (8.0%) | 82/82 (100.0%) (STOP 73, UNK 9) | 162/162 (100.0%) (STOP 150, UNK 12) | 1/18/1 of 23 |

### Condition `production`

| session | arm | frames | false-free ground | false-free any | false-stop ground | false-stop any | UNKNOWN | coverage>=min | guidance coverage | danger recall ground (STOP/UNK) | danger recall any (STOP/UNK) | 23-frame W/S/U (of 23) |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `-PqSDmiE` | A | 121 | 10/21 (47.6%) | 23/42 (54.8%) | 0/100 (0.0%) | 0/79 (0.0%) | 33/121 (27.3%) | 89/121 (73.6%) | 88/121 (72.7%) | 11/21 (52.4%) (STOP 0, UNK 11) | 19/42 (45.2%) (STOP 0, UNK 19) | 2/0/1 of 23 |
| `-PqSDmiE` | B | 121 | 7/21 (33.3%) | 13/42 (31.0%) | 11/100 (11.0%) | 9/79 (11.4%) | 60/121 (49.6%) | 89/121 (73.6%) | 49/121 (40.5%) | 14/21 (66.7%) (STOP 1, UNK 13) | 29/42 (69.0%) (STOP 3, UNK 26) | 2/0/1 of 23 |
| `-PqSDmiE` | C | 121 | 7/21 (33.3%) | 13/42 (31.0%) | 4/100 (4.0%) | 4/79 (5.1%) | 68/121 (56.2%) | 89/121 (73.6%) | 49/121 (40.5%) | 14/21 (66.7%) (STOP 0, UNK 14) | 29/42 (69.0%) (STOP 0, UNK 29) | 2/0/1 of 23 |
| `M5UIabsl` | A | 184 | 0/0 (n/a) | 90/91 (98.9%) | 0/184 (0.0%) | 0/93 (0.0%) | 5/184 (2.7%) | 179/184 (97.3%) | 179/184 (97.3%) | 0/0 (n/a) (STOP 0, UNK 0) | 1/91 (1.1%) (STOP 0, UNK 1) | 0/0/0 of 23 |
| `M5UIabsl` | B | 184 | 0/0 (n/a) | 80/91 (87.9%) | 8/184 (4.3%) | 6/93 (6.5%) | 21/184 (11.4%) | 179/184 (97.3%) | 155/184 (84.2%) | 0/0 (n/a) (STOP 0, UNK 0) | 11/91 (12.1%) (STOP 2, UNK 9) | 0/0/0 of 23 |
| `M5UIabsl` | C | 184 | 0/0 (n/a) | 80/91 (87.9%) | 5/184 (2.7%) | 3/93 (3.2%) | 24/184 (13.0%) | 179/184 (97.3%) | 155/184 (84.2%) | 0/0 (n/a) (STOP 0, UNK 0) | 11/91 (12.1%) (STOP 2, UNK 9) | 0/0/0 of 23 |
| `UGNwBEZM` | A | 176 | 37/82 (45.1%) | 95/162 (58.6%) | 0/94 (0.0%) | 0/14 (0.0%) | 69/176 (39.2%) | 107/176 (60.8%) | 107/176 (60.8%) | 45/82 (54.9%) (STOP 0, UNK 45) | 67/162 (41.4%) (STOP 0, UNK 67) | 18/0/2 of 23 |
| `UGNwBEZM` | B | 176 | 12/82 (14.6%) | 37/162 (22.8%) | 32/94 (34.0%) | 0/14 (0.0%) | 58/176 (33.0%) | 107/176 (60.8%) | 47/176 (26.7%) | 70/82 (85.4%) (STOP 39, UNK 31) | 125/162 (77.2%) (STOP 71, UNK 54) | 6/6/8 of 23 |
| `UGNwBEZM` | C | 176 | 12/82 (14.6%) | 37/162 (22.8%) | 19/94 (20.2%) | 0/14 (0.0%) | 85/176 (48.3%) | 107/176 (60.8%) | 47/176 (26.7%) | 70/82 (85.4%) (STOP 25, UNK 45) | 125/162 (77.2%) (STOP 44, UNK 81) | 6/3/11 of 23 |

## 3. B vs A / C vs B / C vs A (generated from the numbers above)

**annotated**

- **B vs A**: false-free [ground danger] 92/103 (89.3%) -> 0/103 (0.0%) (n -92, -89.3 pp); false-free [any-negative danger] 284/295 (96.3%) -> 6/295 (2.0%) (n -278, -94.2 pp); false-stop [ground danger] 0/378 (0.0%) -> 188/378 (49.7%) (n +188, +49.7 pp); false-stop [any-negative danger] 0/186 (0.0%) -> 2/186 (1.1%) (n +2, +1.1 pp); UNKNOWN 11/481 (2.3%) -> 0/481 (0.0%) (n -11, -2.3 pp); coverage>=min 470/481 (97.7%) -> 470/481 (97.7%) (mean 0.96 -> 0.96); guidance coverage 470/481 (97.7%) -> 190/481 (39.5%) (n -280, -58.2 pp); danger blocking recall [ground danger] 11/103 (10.7%) -> 103/103 (100.0%) (n +92, +89.3 pp; STOP 0 -> 103, UNKNOWN 11 -> 0); danger blocking recall [any-negative danger] 11/295 (3.7%) -> 289/295 (98.0%) (n +278, +94.2 pp; STOP 0 -> 289, UNKNOWN 11 -> 0); 23-frame walkable guidance 2/23 (of 23; STOP 21, UNKNOWN 0); action flips vs A 291/481 (60.5%).
- **C vs B**: false-free [ground danger] 0/103 (0.0%) -> 0/103 (0.0%) (n +0, +0.0 pp); false-free [any-negative danger] 6/295 (2.0%) -> 6/295 (2.0%) (n +0, +0.0 pp); false-stop [ground danger] 188/378 (49.7%) -> 128/378 (33.9%) (n -60, -15.9 pp); false-stop [any-negative danger] 2/186 (1.1%) -> 0/186 (0.0%) (n -2, -1.1 pp); UNKNOWN 0/481 (0.0%) -> 77/481 (16.0%) (n +77, +16.0 pp); coverage>=min 470/481 (97.7%) -> 470/481 (97.7%) (mean 0.96 -> 0.96); guidance coverage 190/481 (39.5%) -> 190/481 (39.5%) (n +0, +0.0 pp); danger blocking recall [ground danger] 103/103 (100.0%) -> 103/103 (100.0%) (n +0, +0.0 pp; STOP 103 -> 86, UNKNOWN 0 -> 17); danger blocking recall [any-negative danger] 289/295 (98.0%) -> 289/295 (98.0%) (n +0, +0.0 pp; STOP 289 -> 214, UNKNOWN 0 -> 75); 23-frame walkable guidance 2/23 (of 23; STOP 19, UNKNOWN 2); action flips vs A 290/481 (60.3%).
- **C vs A**: false-free [ground danger] 92/103 (89.3%) -> 0/103 (0.0%) (n -92, -89.3 pp); false-free [any-negative danger] 284/295 (96.3%) -> 6/295 (2.0%) (n -278, -94.2 pp); false-stop [ground danger] 0/378 (0.0%) -> 128/378 (33.9%) (n +128, +33.9 pp); false-stop [any-negative danger] 0/186 (0.0%) -> 0/186 (0.0%) (n +0, +0.0 pp); UNKNOWN 11/481 (2.3%) -> 77/481 (16.0%) (n +66, +13.7 pp); coverage>=min 470/481 (97.7%) -> 470/481 (97.7%) (mean 0.96 -> 0.96); guidance coverage 470/481 (97.7%) -> 190/481 (39.5%) (n -280, -58.2 pp); danger blocking recall [ground danger] 11/103 (10.7%) -> 103/103 (100.0%) (n +92, +89.3 pp; STOP 0 -> 86, UNKNOWN 11 -> 17); danger blocking recall [any-negative danger] 11/295 (3.7%) -> 289/295 (98.0%) (n +278, +94.2 pp; STOP 0 -> 214, UNKNOWN 11 -> 75); 23-frame walkable guidance 2/23 (of 23; STOP 19, UNKNOWN 2); action flips vs A 290/481 (60.3%).

**production**

- **B vs A**: false-free [ground danger] 47/103 (45.6%) -> 19/103 (18.4%) (n -28, -27.2 pp); false-free [any-negative danger] 208/295 (70.5%) -> 130/295 (44.1%) (n -78, -26.4 pp); false-stop [ground danger] 0/378 (0.0%) -> 51/378 (13.5%) (n +51, +13.5 pp); false-stop [any-negative danger] 0/186 (0.0%) -> 15/186 (8.1%) (n +15, +8.1 pp); UNKNOWN 107/481 (22.2%) -> 139/481 (28.9%) (n +32, +6.7 pp); coverage>=min 375/481 (78.0%) -> 375/481 (78.0%) (mean 0.57 -> 0.57); guidance coverage 374/481 (77.8%) -> 251/481 (52.2%) (n -123, -25.6 pp); danger blocking recall [ground danger] 56/103 (54.4%) -> 84/103 (81.6%) (n +28, +27.2 pp; STOP 0 -> 40, UNKNOWN 56 -> 44); danger blocking recall [any-negative danger] 87/295 (29.5%) -> 165/295 (55.9%) (n +78, +26.4 pp; STOP 0 -> 76, UNKNOWN 87 -> 89); 23-frame walkable guidance 8/23 (of 23; STOP 6, UNKNOWN 9); action flips vs A 157/481 (32.6%).
- **C vs B**: false-free [ground danger] 19/103 (18.4%) -> 19/103 (18.4%) (n +0, +0.0 pp); false-free [any-negative danger] 130/295 (44.1%) -> 130/295 (44.1%) (n +0, +0.0 pp); false-stop [ground danger] 51/378 (13.5%) -> 28/378 (7.4%) (n -23, -6.1 pp); false-stop [any-negative danger] 15/186 (8.1%) -> 7/186 (3.8%) (n -8, -4.3 pp); UNKNOWN 139/481 (28.9%) -> 177/481 (36.8%) (n +38, +7.9 pp); coverage>=min 375/481 (78.0%) -> 375/481 (78.0%) (mean 0.57 -> 0.57); guidance coverage 251/481 (52.2%) -> 251/481 (52.2%) (n +0, +0.0 pp); danger blocking recall [ground danger] 84/103 (81.6%) -> 84/103 (81.6%) (n +0, +0.0 pp; STOP 40 -> 25, UNKNOWN 44 -> 59); danger blocking recall [any-negative danger] 165/295 (55.9%) -> 165/295 (55.9%) (n +0, +0.0 pp; STOP 76 -> 46, UNKNOWN 89 -> 119); 23-frame walkable guidance 8/23 (of 23; STOP 3, UNKNOWN 12); action flips vs A 144/481 (29.9%).
- **C vs A**: false-free [ground danger] 47/103 (45.6%) -> 19/103 (18.4%) (n -28, -27.2 pp); false-free [any-negative danger] 208/295 (70.5%) -> 130/295 (44.1%) (n -78, -26.4 pp); false-stop [ground danger] 0/378 (0.0%) -> 28/378 (7.4%) (n +28, +7.4 pp); false-stop [any-negative danger] 0/186 (0.0%) -> 7/186 (3.8%) (n +7, +3.8 pp); UNKNOWN 107/481 (22.2%) -> 177/481 (36.8%) (n +70, +14.6 pp); coverage>=min 375/481 (78.0%) -> 375/481 (78.0%) (mean 0.57 -> 0.57); guidance coverage 374/481 (77.8%) -> 251/481 (52.2%) (n -123, -25.6 pp); danger blocking recall [ground danger] 56/103 (54.4%) -> 84/103 (81.6%) (n +28, +27.2 pp; STOP 0 -> 25, UNKNOWN 56 -> 59); danger blocking recall [any-negative danger] 87/295 (29.5%) -> 165/295 (55.9%) (n +78, +26.4 pp; STOP 0 -> 46, UNKNOWN 87 -> 119); 23-frame walkable guidance 8/23 (of 23; STOP 3, UNKNOWN 12); action flips vs A 144/481 (29.9%).

These sentences are formatted mechanically from the tables above; the interpretation (which change is acceptable) is a human decision and is left as `TODO(Lead)`.
