# Mobilio feedback 独立复核（刷新 ZIP，2026-11-07）

## 结论

**PASS（指定数字与矩阵可由随包逐帧数据重算）；PASS（E1/E2/E3 语义表述）；INCONCLUSIVE（外部执行可重复性仍需独立重跑）。**

刷新 ZIP SHA-256：`b3fc2510927cbfdc1eb3d53ebd1fa2d768d03cea96a2a104346dd3550f9e9b77`。

## 逐项重算

- `layer_matrix.json` 的 `production:production`：`frames=481`、`fused_authorized=49`、`unknown=275`、`any_danger=295`；`reasons` 中 `surface:no_candidate_ray=125`、`occupancy:no_observed_clearance=97`、`occupancy:no_fully_observed_clearance=44`。逐帧 `four_way_per_frame.csv` 的 D 条件（962 行为 annotated+production 两半）对应 production 半部 481 行，原因计数同为 125/97/44。
- `harvard_fixed:harvard_A`：`frames=481`、`surface_clear=23`、`fused_authorized=8`、`unknown=168`；`harvard_fixed:harvard_D`：`surface_clear=80`、`fused_authorized=71`、`unknown=347`。因此 23/80 确为 surface-layer CLEAR 候选，8/71 确为最终授权 guidance，未混作同一指标。
- 四组矩阵键存在且各为 481 帧：`four_way:A_gt_gt` 授权 29、`B_v5_gt` 21、`C_gt_dav2` 25、`D_v5_dav2` 269（CSV 每个条件各 962 行，含两半；矩阵按 481 帧半部报告）。这些数字与矩阵的 `fused_authorized` 字段一致；README 对生产臂的 49/481、275/481、17/295 也由 `production:production` 与其 danger-miss 字段支持。

## E1/E2/E3 语义

README 明确要求 UNKNOWN 永不重编码为 CLEAR 或 hazard recognition；E2/E3 只输出 `observation_hint`，保留 STOP/confirmed-occupancy 规则，不得转为 `authorized_motion`、方向 TTS 或 CLEAR；只有 CLEAR certificate 才能授权运动。该约束与矩阵字段分离（`observation_hint`、`authorized_motion`、`safety_state`）一致。

## 可能推翻报告的证据（已主动检查）

1. **分母/重复行反证：** CSV 每条件为 962 行，而矩阵按 481；若把两半误合计会得到 2× 数字。README 已明确 frozen 481-frame 半部，审查按 condition+半部核对，未发现该算术错误。
2. **Harvard 指标混淆反证：** 逐帧 CSV 同时含 `surface_state` 与最终 `authorized_motion`；直接计数得到 A/D 的 surface CLEAR=23/80、最终授权=8/71，证明若只看 surface 列会误报最终 guidance。README 已显式拆分，故该反证未推翻报告。
3. **D 原因互斥性反证：** 125/97/44 是 reason 字段计数，矩阵另列 `matrix_counts`；这些原因可能与其它层状态重叠，不能相加当作 266 的互斥分母。README 明确称其“not mutually exclusive”，因此未构成矛盾。
4. **安全语义反证：** 检查矩阵字段与 README 文字，没有规则把 UNKNOWN、`observation_hint` 或 surface candidate 直接映射为 authorized motion；因此未发现 E1/E2/E3 授权泄漏。

## 门禁

证据包内数字和语义 **PASS**。仍建议在独立环境重跑同一 CSV 聚合脚本，以确认生成链未被旧缓存或条件筛选影响；在此之前，结论是随包证据一致性 PASS，而非重新执行实验的 PASS。

```sh
shasum -a 256 outputs/blindnav-superagent-20261107.zip
unzip -l outputs/blindnav-superagent-20261107.zip | rg 'mobilio_feedback_20261107/(README|layer_matrix)|per_frame.csv'
```
