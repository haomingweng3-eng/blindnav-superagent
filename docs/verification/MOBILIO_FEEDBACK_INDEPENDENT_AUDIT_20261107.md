# Mobilio feedback 独立核查（2026-11-07）

## 结论

**INCONCLUSIVE；不能判定 README/layer_matrix 的数字与逻辑已通过。** 当前可审计工作区与可用归档中均未发现 `docs/phase_reports/mobilio_feedback_20261107/README.md`、`layer_matrix.json` 或其指定 CSV，因此无法逐行核对四组/Harvard 数字、23/80 与 8/71、D 条件 125/97/44，亦无法验证 E1/E2/E3 的授权语义。

## 逐项核查状态

1. **四组/Harvard 数字：INCONCLUSIVE。** 缺少目标 README、layer_matrix 与 CSV 原件，无法计算逐行汇总或核对分母。
2. **Harvard 23/80 与 8/71：INCONCLUSIVE。** 没有可读记录证明 23/80 的标签确为 `surface_clear`，或 8/71 的标签确为 `final guidance`。
3. **D 条件 125/97/44：INCONCLUSIVE。** 缺少 D 条件矩阵和原因列，无法验证三项统计是否互斥、覆盖完整或与矩阵一致。
4. **E1/E2/E3 安全语义：INCONCLUSIVE。** 缺少方案文本/代码，无法确认 UNKNOWN 不被当作 CLEAR，也无法确认 `observation_hint` 不会授权运动。

## 反证尝试

- 反证“目标文件已在当前 checkout”：`find`/`rg --files` 未找到 `mobilio_feedback_20261107` 或 `layer_matrix.json`。
- 反证“可用 ZIP 已包含证据”：对现有 `outputs/blindnav-superagent-20261107.zip` 执行 `unzip -l | rg 'mobilio_feedback_20261107|layer_matrix'` 无匹配。
- 反证“可由旧 Mobilio 产物替代”：工作区仅有旧版 `outputs/mobilio_*` 汇总，未见目标四组/Harvard CSV、D 条件矩阵或 E1/E2/E3 定义；旧产物不能证明新报告数字逐行一致。

## 门禁建议

在提供 commit `869ea96` 的完整 checkout 或包含目标目录的 ZIP 前，保持该审查为 INCONCLUSIVE；不要将四组数字、Harvard 计数、D 条件原因统计或 E1/E2/E3 安全语义标记为已验证。复核所需命令：

```sh
rg --files | rg 'docs/phase_reports/mobilio_feedback_20261107/(README\.md|layer_matrix\.json|.*\.csv)$'
rg -n 'surface_clear|final guidance|125|97|44|E1|E2|E3|UNKNOWN|observation_hint' docs/phase_reports/mobilio_feedback_20261107
python - <<'PY'
import csv, json
# load the report CSVs and layer_matrix.json, then recompute each reported count
PY
```
