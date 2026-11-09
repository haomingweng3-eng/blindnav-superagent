# Mobilio E2 occupancy-hint 独立复核（刷新 ZIP，2026-11-07）

## 结论

**PASS（随包逐行重算与不授权约束一致）；INCONCLUSIVE（未在独立开发环境执行完整项目测试/实验）。**

刷新 ZIP SHA-256：`f7cb05ed9b0436299889d2972319955bfa28341db5541ed6ea081a18f0ce3088`。

## 逐行重算

从 `production_per_frame.csv` 筛选 `condition=production`、`arm=mobilio`，得到 481 行；直接加载随包 `mobilio_occupancy_hint_eval.py` 的 `evaluate_rows` 重算结果与 `e2_occupancy_hint.json` 完全相等：

- hints：37；其中 danger=19、confirmed_clear=12；
- authorized_motion：49，`authorized_motion_unchanged=true`；
- danger forward-guidance miss：17/295；
- confirmed-clear false stop：0/150；
- hint reason：37 行均为 `occupancy:no_fully_observed_clearance`。

## 不授权约束

脚本的 `_hint` 仅在 `occupancy_state=UNKNOWN`、正 clearance distance、`occupancy_blocking_cells=0` 时返回诊断候选；`evaluate_rows` 不修改任何行的 `safety_state`、`effective_command` 或授权字段，只统计既有授权。脚本和 JSON 的 `hint_contract` 明确写出 UNKNOWN 保持 UNKNOWN，hint 不产生 CLEAR、STOP、authorized_motion 或方向 TTS。随包 unittest 也验证 partial clearance 只增加 hint、授权仍为 0，以及 blocked/clear rows 不产生 hint。

## 主动反证尝试

1. **重算反证：** 用同一脚本和冻结 CSV 逐行重算，结果与 JSON 完全相等，未推翻目标数字。
2. **授权泄漏反证：** 检查 `_hint` 与 `evaluate_rows` 源码；hint 只构造列表并计数，授权列表独立读取既有 `effective_command`/`authorized_motion`，未发现将 hint 写回 CLEAR 或运动命令的路径。
3. **分母反证：** 直接核对 481 production 行、295 danger 行、150 confirmed-clear 行；JSON 分母与 CSV 布尔字段计数一致，未发现把 annotated 半部混入的 962→481 错误。
4. **测试覆盖反证：** unittest 只覆盖合成行的 hint/blocked 行语义，不覆盖完整 481 行或 TTS 集成；因此不能把单元测试通过升级为完整系统实验通过，这保留 INCONCLUSIVE 限定。

## 门禁建议

E2 诊断产物可判 **PASS**：目标计数、同输入重算和“不授权”契约一致。E2 尚未证明导航质量提升；不得把 37 hints 当作新增授权 guidance。继续保留 UNKNOWN 与安全层，并在独立环境运行完整测试后再关闭实验门禁。

```sh
shasum -a 256 outputs/blindnav-superagent-20261107.zip
unzip -p outputs/blindnav-superagent-20261107.zip docs/assets/review_20261107/mobilio_feedback_20261107/production_per_frame.csv > /tmp/production_per_frame.csv
# 使用随包脚本的 evaluate_rows 对 condition=production、arm=mobilio 逐行重算
```
