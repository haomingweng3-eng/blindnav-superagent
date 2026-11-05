# P1 Path Keeper — negative-evidence fusion: A/B/C report tables

Generated: 2026-11-06 01:45:36

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
| B | 481 | 6/103 (5.8%) | 21/295 (7.1%) | 177/378 (46.8%) | 0/186 (0.0%) | 3/114 (2.6%) | 97/103 (94.2%) (STOP 97, UNK 0) | 274/295 (92.9%) (STOP 274, UNK 0) | 3/20/0 of 23 |
| C | 481 | 6/103 (5.8%) | 21/295 (7.1%) | 118/378 (31.2%) | 0/186 (0.0%) | 3/114 (2.6%) | 97/103 (94.2%) (STOP 81, UNK 16) | 274/295 (92.9%) (STOP 199, UNK 75) | 3/16/4 of 23 |

### 1b. Usability / coverage / regression events

| arm | frames | UNKNOWN n/den (%) | mean coverage | coverage>=min n/den (%) | guidance coverage n/den (%) | action flips vs A n/den (%) | road-in-path ignored n/den (%) | curb-in-path ignored n/den (%) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| A | 481 | 11/481 (2.3%) | 0.96 | 470/481 (97.7%) | 470/481 (97.7%) | 0/481 (0.0%) | 2/5 (40.0%) | 21/109 (19.3%) |
| B | 481 | 0/481 (0.0%) | 0.96 | 470/481 (97.7%) | 207/481 (43.0%) | 274/481 (57.0%) | 0/5 (0.0%) | 3/109 (2.8%) |
| C | 481 | 75/481 (15.6%) | 0.96 | 470/481 (97.7%) | 207/481 (43.0%) | 273/481 (56.8%) | 0/5 (0.0%) | 3/109 (2.8%) |

## 1. Condition `production`

### 1a. Safety / negative-evidence

Both danger denominators are shown side by side (primary = GT excluded-ground danger / PR #15 class; secondary = GT any-negative danger incl. object/vegetation occupancy).

| arm | frames | false-free ground n/den (%) | false-free any n/den (%) | false-stop ground n/den (%) | false-stop any n/den (%) | false-free PR15-gate n/den (%) | danger recall ground n/den (STOP/UNK) | danger recall any n/den (STOP/UNK) | 23-frame walkable/STOP/UNKNOWN (of 23) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| A | 481 | 47/103 (45.6%) | 208/295 (70.5%) | 0/378 (0.0%) | 0/186 (0.0%) | 37/114 (32.5%) | 56/103 (54.4%) (STOP 0, UNK 56) | 87/295 (29.5%) (STOP 0, UNK 87) | 20/0/3 of 23 |
| B | 481 | 21/103 (20.4%) | 134/295 (45.4%) | 47/378 (12.4%) | 14/186 (7.5%) | 14/114 (12.3%) | 82/103 (79.6%) (STOP 39, UNK 43) | 161/295 (54.6%) (STOP 72, UNK 89) | 8/6/9 of 23 |
| C | 481 | 21/103 (20.4%) | 134/295 (45.4%) | 25/378 (6.6%) | 4/186 (2.2%) | 14/114 (12.3%) | 82/103 (79.6%) (STOP 22, UNK 60) | 161/295 (54.6%) (STOP 43, UNK 118) | 8/3/12 of 23 |

### 1b. Usability / coverage / regression events

| arm | frames | UNKNOWN n/den (%) | mean coverage | coverage>=min n/den (%) | guidance coverage n/den (%) | action flips vs A n/den (%) | road-in-path ignored n/den (%) | curb-in-path ignored n/den (%) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| A | 481 | 107/481 (22.2%) | 0.57 | 375/481 (78.0%) | 374/481 (77.8%) | 0/481 (0.0%) | 2/5 (40.0%) | 15/109 (13.8%) |
| B | 481 | 138/481 (28.7%) | 0.57 | 375/481 (78.0%) | 257/481 (53.4%) | 151/481 (31.4%) | 2/5 (40.0%) | 10/109 (9.2%) |
| C | 481 | 177/481 (36.8%) | 0.57 | 375/481 (78.0%) | 257/481 (53.4%) | 136/481 (28.3%) | 2/5 (40.0%) | 10/109 (9.2%) |

## 2. Per-session breakdown

### Condition `annotated`

| session | arm | frames | false-free ground | false-free any | false-stop ground | false-stop any | UNKNOWN | coverage>=min | guidance coverage | danger recall ground (STOP/UNK) | danger recall any (STOP/UNK) | 23-frame W/S/U (of 23) |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `-PqSDmiE` | A | 121 | 10/21 (47.6%) | 31/42 (73.8%) | 0/100 (0.0%) | 0/79 (0.0%) | 11/121 (9.1%) | 110/121 (90.9%) | 110/121 (90.9%) | 11/21 (52.4%) (STOP 0, UNK 11) | 11/42 (26.2%) (STOP 0, UNK 11) | 3/0/0 of 23 |
| `-PqSDmiE` | B | 121 | 3/21 (14.3%) | 8/42 (19.0%) | 16/100 (16.0%) | 0/79 (0.0%) | 0/121 (0.0%) | 110/121 (90.9%) | 87/121 (71.9%) | 18/21 (85.7%) (STOP 18, UNK 0) | 34/42 (81.0%) (STOP 34, UNK 0) | 1/2/0 of 23 |
| `-PqSDmiE` | C | 121 | 3/21 (14.3%) | 8/42 (19.0%) | 9/100 (9.0%) | 0/79 (0.0%) | 14/121 (11.6%) | 110/121 (90.9%) | 87/121 (71.9%) | 18/21 (85.7%) (STOP 11, UNK 7) | 34/42 (81.0%) (STOP 20, UNK 14) | 1/1/1 of 23 |
| `M5UIabsl` | A | 184 | 0/0 (n/a) | 91/91 (100.0%) | 0/184 (0.0%) | 0/93 (0.0%) | 0/184 (0.0%) | 184/184 (100.0%) | 184/184 (100.0%) | 0/0 (n/a) (STOP 0, UNK 0) | 0/91 (0.0%) (STOP 0, UNK 0) | 0/0/0 of 23 |
| `M5UIabsl` | B | 184 | 0/0 (n/a) | 9/91 (9.9%) | 82/184 (44.6%) | 0/93 (0.0%) | 0/184 (0.0%) | 184/184 (100.0%) | 102/184 (55.4%) | 0/0 (n/a) (STOP 0, UNK 0) | 82/91 (90.1%) (STOP 82, UNK 0) | 0/0/0 of 23 |
| `M5UIabsl` | C | 184 | 0/0 (n/a) | 9/91 (9.9%) | 35/184 (19.0%) | 0/93 (0.0%) | 47/184 (25.5%) | 184/184 (100.0%) | 102/184 (55.4%) | 0/0 (n/a) (STOP 0, UNK 0) | 82/91 (90.1%) (STOP 35, UNK 47) | 0/0/0 of 23 |
| `UGNwBEZM` | A | 176 | 82/82 (100.0%) | 162/162 (100.0%) | 0/94 (0.0%) | 0/14 (0.0%) | 0/176 (0.0%) | 176/176 (100.0%) | 176/176 (100.0%) | 0/82 (0.0%) (STOP 0, UNK 0) | 0/162 (0.0%) (STOP 0, UNK 0) | 20/0/0 of 23 |
| `UGNwBEZM` | B | 176 | 3/82 (3.7%) | 4/162 (2.5%) | 79/94 (84.0%) | 0/14 (0.0%) | 0/176 (0.0%) | 176/176 (100.0%) | 18/176 (10.2%) | 79/82 (96.3%) (STOP 79, UNK 0) | 158/162 (97.5%) (STOP 158, UNK 0) | 2/18/0 of 23 |
| `UGNwBEZM` | C | 176 | 3/82 (3.7%) | 4/162 (2.5%) | 74/94 (78.7%) | 0/14 (0.0%) | 14/176 (8.0%) | 176/176 (100.0%) | 18/176 (10.2%) | 79/82 (96.3%) (STOP 70, UNK 9) | 158/162 (97.5%) (STOP 144, UNK 14) | 2/15/3 of 23 |

### Condition `production`

| session | arm | frames | false-free ground | false-free any | false-stop ground | false-stop any | UNKNOWN | coverage>=min | guidance coverage | danger recall ground (STOP/UNK) | danger recall any (STOP/UNK) | 23-frame W/S/U (of 23) |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `-PqSDmiE` | A | 121 | 10/21 (47.6%) | 23/42 (54.8%) | 0/100 (0.0%) | 0/79 (0.0%) | 33/121 (27.3%) | 89/121 (73.6%) | 88/121 (72.7%) | 11/21 (52.4%) (STOP 0, UNK 11) | 19/42 (45.2%) (STOP 0, UNK 19) | 2/0/1 of 23 |
| `-PqSDmiE` | B | 121 | 8/21 (38.1%) | 15/42 (35.7%) | 9/100 (9.0%) | 8/79 (10.1%) | 58/121 (47.9%) | 89/121 (73.6%) | 53/121 (43.8%) | 13/21 (61.9%) (STOP 1, UNK 12) | 27/42 (64.3%) (STOP 2, UNK 25) | 2/0/1 of 23 |
| `-PqSDmiE` | C | 121 | 8/21 (38.1%) | 15/42 (35.7%) | 1/100 (1.0%) | 1/79 (1.3%) | 67/121 (55.4%) | 89/121 (73.6%) | 53/121 (43.8%) | 13/21 (61.9%) (STOP 0, UNK 13) | 27/42 (64.3%) (STOP 0, UNK 27) | 2/0/1 of 23 |
| `M5UIabsl` | A | 184 | 0/0 (n/a) | 90/91 (98.9%) | 0/184 (0.0%) | 0/93 (0.0%) | 5/184 (2.7%) | 179/184 (97.3%) | 179/184 (97.3%) | 0/0 (n/a) (STOP 0, UNK 0) | 1/91 (1.1%) (STOP 0, UNK 1) | 0/0/0 of 23 |
| `M5UIabsl` | B | 184 | 0/0 (n/a) | 80/91 (87.9%) | 8/184 (4.3%) | 6/93 (6.5%) | 21/184 (11.4%) | 179/184 (97.3%) | 155/184 (84.2%) | 0/0 (n/a) (STOP 0, UNK 0) | 11/91 (12.1%) (STOP 2, UNK 9) | 0/0/0 of 23 |
| `M5UIabsl` | C | 184 | 0/0 (n/a) | 80/91 (87.9%) | 5/184 (2.7%) | 3/93 (3.2%) | 24/184 (13.0%) | 179/184 (97.3%) | 155/184 (84.2%) | 0/0 (n/a) (STOP 0, UNK 0) | 11/91 (12.1%) (STOP 2, UNK 9) | 0/0/0 of 23 |
| `UGNwBEZM` | A | 176 | 37/82 (45.1%) | 95/162 (58.6%) | 0/94 (0.0%) | 0/14 (0.0%) | 69/176 (39.2%) | 107/176 (60.8%) | 107/176 (60.8%) | 45/82 (54.9%) (STOP 0, UNK 45) | 67/162 (41.4%) (STOP 0, UNK 67) | 18/0/2 of 23 |
| `UGNwBEZM` | B | 176 | 13/82 (15.9%) | 39/162 (24.1%) | 30/94 (31.9%) | 0/14 (0.0%) | 59/176 (33.5%) | 107/176 (60.8%) | 49/176 (27.8%) | 69/82 (84.1%) (STOP 38, UNK 31) | 123/162 (75.9%) (STOP 68, UNK 55) | 6/6/8 of 23 |
| `UGNwBEZM` | C | 176 | 13/82 (15.9%) | 39/162 (24.1%) | 19/94 (20.2%) | 0/14 (0.0%) | 86/176 (48.9%) | 107/176 (60.8%) | 49/176 (27.8%) | 69/82 (84.1%) (STOP 22, UNK 47) | 123/162 (75.9%) (STOP 41, UNK 82) | 6/3/11 of 23 |

## 3. B vs A / C vs B / C vs A (generated from the numbers above)

**annotated**

- **B vs A**: false-free [ground danger] 92/103 (89.3%) -> 6/103 (5.8%) (n -86, -83.5 pp); false-free [any-negative danger] 284/295 (96.3%) -> 21/295 (7.1%) (n -263, -89.2 pp); false-stop [ground danger] 0/378 (0.0%) -> 177/378 (46.8%) (n +177, +46.8 pp); false-stop [any-negative danger] 0/186 (0.0%) -> 0/186 (0.0%) (n +0, +0.0 pp); UNKNOWN 11/481 (2.3%) -> 0/481 (0.0%) (n -11, -2.3 pp); coverage>=min 470/481 (97.7%) -> 470/481 (97.7%) (mean 0.96 -> 0.96); guidance coverage 470/481 (97.7%) -> 207/481 (43.0%) (n -263, -54.7 pp); danger blocking recall [ground danger] 11/103 (10.7%) -> 97/103 (94.2%) (n +86, +83.5 pp; STOP 0 -> 97, UNKNOWN 11 -> 0); danger blocking recall [any-negative danger] 11/295 (3.7%) -> 274/295 (92.9%) (n +263, +89.2 pp; STOP 0 -> 274, UNKNOWN 11 -> 0); 23-frame walkable guidance 3/23 (of 23; STOP 20, UNKNOWN 0); action flips vs A 274/481 (57.0%).
- **C vs B**: false-free [ground danger] 6/103 (5.8%) -> 6/103 (5.8%) (n +0, +0.0 pp); false-free [any-negative danger] 21/295 (7.1%) -> 21/295 (7.1%) (n +0, +0.0 pp); false-stop [ground danger] 177/378 (46.8%) -> 118/378 (31.2%) (n -59, -15.6 pp); false-stop [any-negative danger] 0/186 (0.0%) -> 0/186 (0.0%) (n +0, +0.0 pp); UNKNOWN 0/481 (0.0%) -> 75/481 (15.6%) (n +75, +15.6 pp); coverage>=min 470/481 (97.7%) -> 470/481 (97.7%) (mean 0.96 -> 0.96); guidance coverage 207/481 (43.0%) -> 207/481 (43.0%) (n +0, +0.0 pp); danger blocking recall [ground danger] 97/103 (94.2%) -> 97/103 (94.2%) (n +0, +0.0 pp; STOP 97 -> 81, UNKNOWN 0 -> 16); danger blocking recall [any-negative danger] 274/295 (92.9%) -> 274/295 (92.9%) (n +0, +0.0 pp; STOP 274 -> 199, UNKNOWN 0 -> 75); 23-frame walkable guidance 3/23 (of 23; STOP 16, UNKNOWN 4); action flips vs A 273/481 (56.8%).
- **C vs A**: false-free [ground danger] 92/103 (89.3%) -> 6/103 (5.8%) (n -86, -83.5 pp); false-free [any-negative danger] 284/295 (96.3%) -> 21/295 (7.1%) (n -263, -89.2 pp); false-stop [ground danger] 0/378 (0.0%) -> 118/378 (31.2%) (n +118, +31.2 pp); false-stop [any-negative danger] 0/186 (0.0%) -> 0/186 (0.0%) (n +0, +0.0 pp); UNKNOWN 11/481 (2.3%) -> 75/481 (15.6%) (n +64, +13.3 pp); coverage>=min 470/481 (97.7%) -> 470/481 (97.7%) (mean 0.96 -> 0.96); guidance coverage 470/481 (97.7%) -> 207/481 (43.0%) (n -263, -54.7 pp); danger blocking recall [ground danger] 11/103 (10.7%) -> 97/103 (94.2%) (n +86, +83.5 pp; STOP 0 -> 81, UNKNOWN 11 -> 16); danger blocking recall [any-negative danger] 11/295 (3.7%) -> 274/295 (92.9%) (n +263, +89.2 pp; STOP 0 -> 199, UNKNOWN 11 -> 75); 23-frame walkable guidance 3/23 (of 23; STOP 16, UNKNOWN 4); action flips vs A 273/481 (56.8%).

**production**

- **B vs A**: false-free [ground danger] 47/103 (45.6%) -> 21/103 (20.4%) (n -26, -25.2 pp); false-free [any-negative danger] 208/295 (70.5%) -> 134/295 (45.4%) (n -74, -25.1 pp); false-stop [ground danger] 0/378 (0.0%) -> 47/378 (12.4%) (n +47, +12.4 pp); false-stop [any-negative danger] 0/186 (0.0%) -> 14/186 (7.5%) (n +14, +7.5 pp); UNKNOWN 107/481 (22.2%) -> 138/481 (28.7%) (n +31, +6.4 pp); coverage>=min 375/481 (78.0%) -> 375/481 (78.0%) (mean 0.57 -> 0.57); guidance coverage 374/481 (77.8%) -> 257/481 (53.4%) (n -117, -24.3 pp); danger blocking recall [ground danger] 56/103 (54.4%) -> 82/103 (79.6%) (n +26, +25.2 pp; STOP 0 -> 39, UNKNOWN 56 -> 43); danger blocking recall [any-negative danger] 87/295 (29.5%) -> 161/295 (54.6%) (n +74, +25.1 pp; STOP 0 -> 72, UNKNOWN 87 -> 89); 23-frame walkable guidance 8/23 (of 23; STOP 6, UNKNOWN 9); action flips vs A 151/481 (31.4%).
- **C vs B**: false-free [ground danger] 21/103 (20.4%) -> 21/103 (20.4%) (n +0, +0.0 pp); false-free [any-negative danger] 134/295 (45.4%) -> 134/295 (45.4%) (n +0, +0.0 pp); false-stop [ground danger] 47/378 (12.4%) -> 25/378 (6.6%) (n -22, -5.8 pp); false-stop [any-negative danger] 14/186 (7.5%) -> 4/186 (2.2%) (n -10, -5.4 pp); UNKNOWN 138/481 (28.7%) -> 177/481 (36.8%) (n +39, +8.1 pp); coverage>=min 375/481 (78.0%) -> 375/481 (78.0%) (mean 0.57 -> 0.57); guidance coverage 257/481 (53.4%) -> 257/481 (53.4%) (n +0, +0.0 pp); danger blocking recall [ground danger] 82/103 (79.6%) -> 82/103 (79.6%) (n +0, +0.0 pp; STOP 39 -> 22, UNKNOWN 43 -> 60); danger blocking recall [any-negative danger] 161/295 (54.6%) -> 161/295 (54.6%) (n +0, +0.0 pp; STOP 72 -> 43, UNKNOWN 89 -> 118); 23-frame walkable guidance 8/23 (of 23; STOP 3, UNKNOWN 12); action flips vs A 136/481 (28.3%).
- **C vs A**: false-free [ground danger] 47/103 (45.6%) -> 21/103 (20.4%) (n -26, -25.2 pp); false-free [any-negative danger] 208/295 (70.5%) -> 134/295 (45.4%) (n -74, -25.1 pp); false-stop [ground danger] 0/378 (0.0%) -> 25/378 (6.6%) (n +25, +6.6 pp); false-stop [any-negative danger] 0/186 (0.0%) -> 4/186 (2.2%) (n +4, +2.2 pp); UNKNOWN 107/481 (22.2%) -> 177/481 (36.8%) (n +70, +14.6 pp); coverage>=min 375/481 (78.0%) -> 375/481 (78.0%) (mean 0.57 -> 0.57); guidance coverage 374/481 (77.8%) -> 257/481 (53.4%) (n -117, -24.3 pp); danger blocking recall [ground danger] 56/103 (54.4%) -> 82/103 (79.6%) (n +26, +25.2 pp; STOP 0 -> 22, UNKNOWN 56 -> 60); danger blocking recall [any-negative danger] 87/295 (29.5%) -> 161/295 (54.6%) (n +74, +25.1 pp; STOP 0 -> 43, UNKNOWN 87 -> 118); 23-frame walkable guidance 8/23 (of 23; STOP 3, UNKNOWN 12); action flips vs A 136/481 (28.3%).

These sentences are formatted mechanically from the tables above; the interpretation (which change is acceptable) is a human decision and is left as `TODO(Lead)`.
