# 当前执行任务包：同一路径联合检查

本目录是聊天附件 `blindnav_next_execution.zip` 的完整展开版，按用户要求上传到现有开发分支，不改变既定任务。除本 README 外，原包的 9 个文件保持原始内容；`MANIFEST.json` 中的 SHA256 可用于校验。

**当前唯一主任务：实现同一路径的地面与障碍联合检查，然后完成一次真实旧新对照。** 详细步骤见 [EXECUTE_NEXT.md](EXECUTE_NEXT.md)。它细化已有 [PC_ACCEPTANCE_TODAY.md](../../phase_prompts/PC_ACCEPTANCE_TODAY.md)，不是恢复 PC 网页演示，也不是另开技术路线。

## 直接开始，不需要下载或拖拽附件

在原开发分支的仓库根目录、此前 E3 的 Python 环境中运行已有结果检查：

```bash
RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)"
python docs/task_packages/next_execution_20261108/audit_route_pairs.py \
  --csv docs/phase_reports/e3_surface_6813160/on_per_frame.csv.gz \
  --out "pc/out/route_pairs_$RUN_ID" \
  --expected-frames 481
```

`python` 使用此前 E3 的实际 Python 可执行文件。此路径直接运行包内脚本，无须按原附件说明再复制到 `pc/tools/`。如果已有同输入的有效检查结果，直接复用，不重复运行。

检查后继续执行 `EXECUTE_NEXT.md` 第 2、3 节：实现独立 PC 候选并交回真实 `before_after.csv`、路径检查证据、逐场景汇总及实际运行日志。**不能只交角度统计，也不能将角度不同当作真实碰撞标签。**

## 文件与状态

- `EXECUTE_NEXT.md`：完整执行任务。
- `audit_route_pairs.py`、`test_audit_route_pairs.py`、`synthetic_contract_tests.log`：已有 CSV 检查脚本及原包合成测试记录。
- `candidates/`：上轮分组复算、衰减方法函数级验证和未应用的候选补丁。衰减是第二项任务，本轮不混用、不应用到生产。
- `MANIFEST.json`：原包文件哈希与打包时状态。

**状态说明：** 原包中的 `TASK_HANDOFF_NOT_PUSHED` 和“本任务包未推送仓库”描述的是打包时历史状态，为保持原文件和哈希一致而保留；本 README 随上传提交说明入库状态。任务入库不等于算法已经修复或实验已经启动。

本次仅上传任务包，没有重新运行合成测试、真实模型或 481 帧实验，没有应用候选补丁，没有改生产算法、安全阈值、Android，也没有合并 PR #20。当前能力评分仍为 3.6/10；真实导航收益待执行与验证。
