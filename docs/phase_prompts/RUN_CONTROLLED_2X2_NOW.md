# 当前执行任务：运行已实现的受控 2×2，不再补写实验计划

状态：**代码已实现；11 个合成契约测试通过；真实 481 帧尚未运行**。
本说明优先于旧 `CORRECTED_2X2_GEOMETRY_ABLATION_20261107.md` 中不明确的 up 选择要求。
本次不更改原 evaluator、地图算法、融合、安全授权、Android，不合并 PR #20。

## 执行入口

`pc/tools/controlled_2x2_replay.py` 是独立 PC 实验，不是生产导航入口。
它复用现有 SANPO loader、v5、相对 DAV2、GroundCalibrator、Surface/Occupancy、融合及评分实现；
不会下载模型，不会安装依赖，也不会覆盖已有输出。使用之前成功运行 E3 的 Python 环境和数据位置。

在已有开发分支的干净隔离工作区执行；保留其他工作区的未提交改动，不 reset/clean/stash：

```bash
# SANPO_REAL、V5_CHECKPOINT 使用先前 E3 的实际值；不要重新寻找/下载数据。
# PYTHON 使用先前 E3 的 Python 可执行文件。
export HF_HUB_OFFLINE=1
export PYTHONNOUSERSITE=1
OUT="pc/out/controlled_2x2_$(date -u +%Y%m%dT%H%M%SZ)"
set -o pipefail
PYTHONPATH=pc:pc/tools "$PYTHON" pc/tools/controlled_2x2_replay.py \
  --data-root "$SANPO_REAL" \
  --v5-checkpoint "$V5_CHECKPOINT" \
  --device mps \
  --out "$OUT" 2>&1 | tee "${OUT##*/}.log"
```

其他已具备的 PC 可改为 `--device cpu` 或 `cuda`；只声明真正测过的设备。
不启动 Basalt/Metric3D/手机，不重跑 E3 的 OFF/ON 或补 12 张图。
若本机输入/环境与 E3 不同，记录一次明确错误；不要把缺数据误报成算法失败，不循环冒烟。

## 已实现的实验契约

| 条件 | 地图语义 | 深度产品 | 所有条件共享的几何 |
|---|---|---|---|
| A | GT | SANPO 参考深度 | 生产标定实际选择的 up、同一 session 高度、K、recorded pose/route |
| B | v5 | SANPO 参考深度 | 同上 |
| C | GT | 相对 DAV2 + 地面标定 | 同上 |
| D | v5 | 相对 DAV2 + 地面标定 | 同上 |

每帧 v5 推理一次、DAV2 推理一次、**有状态 GroundCalibrator 更新一次**。
四个条件各自维护独立地图；不共享/串用地图历史。C/D 共用相同深度和标定结果。
A/B 不再使用 GT plane 的 up/h；GT 仅进入明确的参考输入臂和评分，不进入 D 的推理。
保存 K、up、高度、pose、mask、depth、fit 哈希并逐帧断言因子配对关系。

**关键澄清：** 所谓“固定 up”是同一帧四个条件使用同一个 up，不是让整段视频方向恒定。
本实现选择现有生产标定实际输出的 up（没有该输出时用 recorded-up），而非在标定后硬改成另一法线。
这样不破坏 scale/shift 与投影的配套关系，并可检查 D 与旧生产 D 是否等价。
这属于**固定生产几何前提下的地图语义/深度产品比较**，不是分割模型替换的全链路总效应，也不是全知 oracle。

当前 `depth.py` 默认是 `Depth-Anything-V2-Small-hf` 相对深度，不是 Metric VKITTI checkpoint。
旧 57 帧 Metric VKITTI 结果不能冒充本次推理输入。参考：
[相对模型](https://github.com/DepthAnything/Depth-Anything-V2)、
[官方 metric 分支](https://github.com/DepthAnything/Depth-Anything-V2/blob/main/metric_depth/README.md)。

## 交付与解释

输出恰为 **481 个配对帧、4 个 Mobilio 条件，共 1,924 行**；本轮不重复计算 split-C 基线。
每个 session 的地图按原帧顺序运行，模型快照和输入清单复用 E3 哈希。

输出文件：
- `per_frame.csv`：逐帧结果、实际几何来源/哈希、深度有效率、投影点数；
- `metrics.json`：全体及逐 session 的计数、条件内配对率差、差中差；
- `complete_geometry_pairs.json`：只在四条件均有几何的配对帧上补充分析，不替代全体分母；
- `historical_D_parity.json`：新 D 与旧 D 的受检状态/授权逐帧对照；
- `run_metadata.json`：实际代码、时间、模型版本、运行状态和限制；
- 上面命令保存的终端日志。

退出码 0：运行及历史 D 的受检字段对照完成，不等于性能提升/安全验收。
退出码 3：已经保存实验结果，但 D 的受检字段与历史不同；先看差异，不调参凑数。
退出码 2：输入/环境/契约错误；报告首个确切阻塞，不反复重试相同操作。

指标同时看 confirmed-clear 授权数、danger 代理帧授权数、UNKNOWN、STOP、几何缺失及 session 分层。
STOP 为 0 不代表没有误拒绝；UNKNOWN 和 OBSERVE 也会阻断可用性。
不把帧级危险代理等同路线碰撞真值，不计算没有标注支持的方向正确率。
三 session、相关帧的效应是描述性结果，不写“统计显著”；现有 481 帧是开发集，不是新独立测试集。

## 完成后的技术裁决

有清楚的语义简单效应：下一步修类别映射/分割适配；先不换定位算法。
有清楚的深度产品效应：下一步修深度/标定，已有 Metric VKITTI 权重可作为一个候选，不同时加多个模型。
四条件都不理想：检查共同几何、地图历史和标签定义；不能仅凭这一点宣称 Harvard 算法无效。
本轮不自动放宽 UNKNOWN/CLEAR/STOP；实验后只选择一项有证据的离线修复，再做一次同输入前后对照。

Basalt + TUM VI 保留为独立定位基准，不是修复当前 recorded-pose 地图实验的先决条件。
原 E3 诊断可结束于它已经证明的范围；不再因缺精确日志时刻或额外图例无限阻塞本实验。

## 本次实际验证范围

在审查执行环境运行：
`python -m pytest -q pc/tests/test_controlled_2x2_replay.py`，**11 passed**。
这些是合成契约测试：共享几何、因子配对、重复/缺失行、分母、逐帧 D 对照、每帧一次标定及禁止 GT 深度回填。
**没有在该环境加载真实 SANPO/v5/DAV2，也没有运行全流水线集成测试或新的 481 帧实验。**
原仓库源码接口已核对；真实数据运行如发现集成错误，应修具体调用而不是重新换路线。
项目分数仍维持原口径 3.6/10；不能因为新脚本或合成测试加导航能力分。


---

## 2026-11-08 独立静态审查补充（唯一下一任务仍是本文件）

审查报告：[`CONTROLLED_2X2_CODE_REVIEW_20261108_0926Z.md`](../reviews/CONTROLLED_2X2_CODE_REVIEW_20261108_0926Z.md)。

**运行前新增最小代码版本锁**（新审查文档 commit 会使 HEAD 与原功能提交不同，故只锁本实验入口 blob，不要求 HEAD 等于 `0142d19`）：

```bash
EXPECTED_EXPERIMENT_BLOB=e4914d023f1c73a9e873d7220894ceb1ca65e4c4
ACTUAL_EXPERIMENT_BLOB="$(git rev-parse HEAD:pc/tools/controlled_2x2_replay.py)"
test "$ACTUAL_EXPERIMENT_BLOB" = "$EXPECTED_EXPERIMENT_BLOB" || { echo BLOCKED_EXPERIMENT_CODE_CHANGED; exit 2; }
git rev-parse HEAD
git status --porcelain --untracked-files=no
```

执行前确保隔离工作区的已跟踪文件没有未提交修改；实验入口本身已有后续内置检查。不要重复单测或 OFF/ON；新增文档不触发 smoke。

历史 D 的 parity 必须逐帧检查；若 `historical_D_parity.json` 有差异，不以汇总数字相近视为通过。当前入口已经实现，任务是**运行一次真实 481 帧，交回五个结果文件及退出码、命令日志**，不是再写新入口。11 个测试通过系先前执行说明的记录，本次代码审查并未独立重跑。
