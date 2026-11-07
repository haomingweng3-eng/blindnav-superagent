# 实际回放命令记录

以下是实际命令的等价形式：个人主目录、数据目录及 Python 路径使用占位符脱敏；`CODE_SHA` 已展开。工作目录为代码快照 `6813160c555e297b03a599eb689b8083fe4ce36a`。日志退出码为 0。

```sh
HF_HUB_OFFLINE=1 PYTHONNOUSERSITE=1 PYTHONPATH=pc:pc/tools "$PYTHON" pc/tools/mobilio_sanpo_ab.py --data-root "$SANPO_REAL" --v5-checkpoint "$V5_CHECKPOINT" --out pc/out/e3_surface_6813160_off --conditions production --max-width 640 --seed 0 --device mps --save-failures 0 --e3-frozen --trace-run-id e3_6813160_off --trace-code-commit 6813160c555e297b03a599eb689b8083fe4ce36a --trace-input-manifest docs/phase_reports/e3_input_audit_20261107/input_inventory.json
HF_HUB_OFFLINE=1 PYTHONNOUSERSITE=1 PYTHONPATH=pc:pc/tools "$PYTHON" pc/tools/mobilio_sanpo_ab.py --data-root "$SANPO_REAL" --v5-checkpoint "$V5_CHECKPOINT" --out pc/out/e3_surface_6813160_on --conditions production --max-width 640 --seed 0 --device mps --save-failures 0 --e3-frozen --trace-run-id e3_6813160_on --trace-code-commit 6813160c555e297b03a599eb689b8083fe4ce36a --trace-input-manifest docs/phase_reports/e3_input_audit_20261107/input_inventory.json --ray-trace-out pc/out/e3_surface_6813160_on/rays.jsonl
```

新回放必须使用不存在的输出目录，并保证被冻结的输入清单、模型缓存与数据文件的哈希一致。`$PYTHON` 为已有 Python 3.12 虚拟环境；依赖版本见 environment_and_run.json。

旧版候选回放在 `git archive 2ee1f58791ea19d0660daf2dfb2ff03ee009ab40` 解出的隔离目录中执行，等价命令如下（路径脱敏）；该旧版没有当前的模型快照锁定预检：

```sh
HF_HUB_OFFLINE=1 PYTHONNOUSERSITE=1 PYTHONPATH="$OLD_TREE/pc:$OLD_TREE/pc/tools" "$PYTHON" "$OLD_TREE/pc/tools/mobilio_sanpo_ab.py" --data-root "$SANPO_REAL" --v5-checkpoint "$V5_CHECKPOINT" --out "$OLD_OUTPUT" --conditions production --max-width 640 --seed 0 --device mps --save-failures 0
```
