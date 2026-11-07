# Mobilio E2 occupancy-hint 独立核查（2026-11-07）

## 结论

**INCONCLUSIVE（当前可审计工作区/外部 ZIP 未包含 cbb8811 的 E2 实验产物）。**

目标文件 `pc/blindnav_pc/mobilio_occupancy_hint_eval.py`、其 unittest、`docs/phase_reports/mobilio_feedback_20261107/e2_occupancy_hint.json` 以及对应 README/计划更新均无法在当前工作区或 `outputs/blindnav-superagent-20261107.zip` 中找到。现有 ZIP 仍只含冻结 `production_per_frame.csv` 与早期反馈报告，因此不能独立确认 37 hints、19 danger、12 confirmed_clear、授权 49、any_miss 17/295、false_stop 0/150 的 E2 重算结果，也不能确认 hint 规则不会产生 CLEAR/authorized_motion/TTS。

## 反证尝试

1. `find`/`rg --files` 搜索 `mobilio_occupancy_hint_eval.py` 与 `e2_occupancy_hint.json`，无结果。
2. `unzip -l outputs/blindnav-superagent-20261107.zip | rg 'occupancy_hint|e2_occupancy'`，无结果；ZIP 中 production CSV 的时间戳仍为此前 12:54 版本。
3. 仅用冻结 CSV 无法证明新脚本的规则：CSV 没有 E2 生成字段/变体 provenance；任何声称 37 hints 或“不授权”都可能来自未提供的代码路径。

## 复核所需命令

```sh
rg --files | rg '(mobilio_occupancy_hint_eval\.py|e2_occupancy_hint\.json|production_per_frame\.csv)$'
python -m unittest discover -s pc/blindnav_pc -p '*occupancy*hint*test*.py'
python pc/blindnav_pc/mobilio_occupancy_hint_eval.py \
  --input docs/assets/review_20261107/mobilio_feedback_20261107/production_per_frame.csv \
  --output docs/phase_reports/mobilio_feedback_20261107/e2_occupancy_hint.json
```

在提供 cbb8811 完整 checkout 或刷新 ZIP 后，再按 `arm=mobilio`/production 481 行逐项核对目标计数，并检查所有 hint 行的 `safety_state`、`authorized_motion`、`tts`/guidance 字段。
