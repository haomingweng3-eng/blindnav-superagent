# P0 license register (2026-11-07)

This is a planning register, not a legal opinion. “Verified” means the
repository or official project page was checked during this phase. “Pending”
means the source URL is recorded but the data was not downloaded or its exact
terms were not independently checked in this phase.

| resource | source | license/status | use decision |
|---|---|---|---|
| SANPO dataset | <https://github.com/google-research-datasets/sanpo_dataset> | CC BY 4.0; verified from the official repository | allowed for attributed research and replay; no raw data committed |
| Harvard Mobilio source | <https://github.com/Harvard-Slade-Lab/Mobilio> | MIT; recorded in `docs/MOBILIO_MIT_NOTICE.md` | reference port kept isolated from production safety logic |
| Basalt code | <https://github.com/VladyslavUsenko/basalt> | BSD 3-Clause; verified from upstream README; third-party dependencies still require review | candidate P1 desktop VIO tool |
| ADVIO dataset | <https://github.com/AaltoVision/ADVIO> | CC BY-NC 4.0; verified from upstream README | research-only fallback; no commercial redistribution |
| TUM-VI dataset | <https://www.tum-vi.com/> | Pending exact dataset terms before download | do not download yet |
| EuRoC MAV dataset | <https://projects.asl.ethz.ch/datasets/euroc-mav/> | Pending exact dataset terms before download | do not download yet |
| Metric/depth checkpoints | project-specific URLs | Pending per-weight terms and hashes | no new weights downloaded in P0 |

Only sources needed for the current audit were checked. No raw dataset, model
checkpoint, token, or local absolute path is committed.
