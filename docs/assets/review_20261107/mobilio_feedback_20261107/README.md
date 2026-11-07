# Mobilio feedback audit source bundle (2026-11-07)

These CSVs are the exact read-only inputs used to produce `docs/phase_reports/mobilio_feedback_20261107/layer_matrix.json`. They contain frozen replay rows only; no raw images or model weights are included.
| file | size | SHA256 |
|---|---:|---|

| `four_way_per_frame.csv` | 2405902 bytes | `0d452e45debb6c0645af6a961e5d9d346063d72806382e14d639266ffbc8095b` |
| `harvard_fixed_per_frame.csv` | 1167206 bytes | `cf82889df37422e84a55c8f1114a8f229d200891116f7ce28dc21746d193e7a7` |
| `production_per_frame.csv` | 1143996 bytes | `60b4b0c6a2e6f843e14f5650a12dd83ea1d5fcaf58d0ce84584ba91f414a069f` |

The first independent audit was INCONCLUSIVE because this bundle was absent from the external ZIP. Re-run it after refreshing the ZIP; the audit must recompute the matrix from these files.
