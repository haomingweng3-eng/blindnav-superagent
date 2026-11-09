# BlindNav 校正 2×2 执行代码独立静态审查（2026-11-08 09:26 UTC）

> **ADJUST / G2 HOLD。** 分支 `agent/corridor-obstacle-split-ad0888814215` HEAD `0142d193bbb37ed1758fb4244c644e39ab7b18a7`。对比上次审查基准 `6a11433ba9d922bb7cb4623a16b1ba952b713b85`：新增 1 提交、3 文件（独立离线 evaluator、合成测试、执行提示词）；没有新 481 帧结果、原始 CSV/JSON 或模型日志。**本次仅代码审查和历史 CSV 统计复核，没有运行模型/测试，也没有进行真机验证。**

## 实质变化与正确性

- 新入口 `pc/tools/controlled_2x2_replay.py` 是隔离的 PC-only 消融实验，并未修改原 `mobilio_sanpo_ab.py`、`SafetyCoordinator`、`authorized_motion`、`CLEAR/UNKNOWN/STOP`、Android 或 PR #20。
- 每帧 v5 和相对 DAV2 推理各一次；同一有状态 `GroundCalibrator.update` 调用一次，固定 v5 校准 mask；四个条件共享同一帧的 K、生产 fit 选出的 up（失败则 recorded-up）、session camera height、recorded pose/route。
- A/B：SANPO 参考深度；C/D：同一 DAV2 标定深度；A/C 用 GT map semantics，B/D 用 v5 map semantics。四条件地图状态各自独立、初始相同。静态代码符合**固定生产标定条件下的地图输入消融**，不是“替换分割模型的端到端总效应”，也不是独立真实 VIO 或可部署导航性能。
- `check_pairs` 核验同帧共同几何、fit、校准 mask、pose 等哈希以及两个因子的成对一致性；`history_check` 要求新 D 与历史 production D 的受检字段逐帧等价。预测深度缺失没有 GT fallback。
- `run_map` 复用原 Surface、Occupancy、`fuse` 与 `_candidate_row`，仅统计候选 Mobilio 臂，不重复旧 split-C。没有发现本次提交直接放宽运动授权的代码。
- 已将相对 DAV2 Small（`Depth-Anything-V2-Small-hf`）与历史 **Metric VKITTI Small 57 帧**数据区分。旧四路 A/B 和 C/D 的语义简单效应仍能在其各自固定深度条件下有限比较；旧 A/C、B/D 深度效应及 interaction 因 up/height 改变而不能干净归因。

## 未验证项和执行前必须守住的门槛

1. 新执行说明**声明** 11 项合成契约测试通过，但当前提交未附新的独立测试运行日志；本审查未重跑。标记为 `REPORTED_SYNTHETIC_TEST`，不能当作 `DESKTOP_REAL_DATA_REPLAY`。
2. 还没有实际的 `per_frame.csv`、`metrics.json`、`complete_geometry_pairs.json`、`historical_D_parity.json`、`run_metadata.json` 或新完整运行日志。不能复算不存在的 A/B/C/D 新主效应和差中差，也不能宣称 G2 GO。
3. `validate_trace_code_state(code, dirty, code)` 中 expected commit 来自同一 `git rev-parse HEAD`，能拒绝已跟踪文件 dirty，却**没有把实验代码固定在某个外部预期 SHA**。不改算法：在现有执行提示词新增 `git rev-parse HEAD:pc/tools/controlled_2x2_replay.py` 的预期 blob SHA 校验，并在输出中记录完整 HEAD；新评审文档提交不应让实验代码 blob 锁失效。
4. 如果历史 D 的状态/授权逐帧 parity 不为零，标记 HOLD 并保留差异；不能为了匹配而改旧数据、降低阈值或抹去失败帧。输出恰为 481 配对帧、1,924 条候选行时再做配对复算。
5. `summarize` 的 `false_stop` 仅是 confirmed-clear 中最终 STOP 数，不包含因 UNKNOWN/OBSERVE 被阻断的可用性损失；明确危险识别召回和 LEFT/RIGHT 轨迹正确率仍然 NOT_EVALUABLE。
6. 同一 3-session 481 帧是开发集，帧强相关；不能将描述性效应当成独立测试集显著性结论。

## 历史指标交叉复核（不是新实验）

从 `docs/phase_reports/e3_surface_6813160/per_session.csv` 的三个 session 求和：

| 历史指标 | baseline_split_c | Mobilio |
|---|---:|---:|
| 处理帧 | 121+184+176=481 | 481 |
| danger proxy | 42+91+162=295 | 295 |
| confirmed-clear proxy | 61+76+13=150 | 150 |
| 方向授权 | 220/481 | 10+37+2=49/481（10.19%） |
| confirmed-clear 授权 | 76/150 | 8+21+0=29/150（19.33%） |
| danger proxy 的 any_miss | 123/295 | 2+13+2=17/295（5.76%） |
| UNKNOWN | 214/481 | 74+53+148=275/481（57.17%） |

旧 E3 记录的 472×181=85,432 条射线和另外 9 帧无公制深度是既有材料，并非这次新提交运行。未下载/重算原始模型权重，也未读取私有本地 SANPO；以上只独立聚合仓库现有 per_session 派生表。

## 失败环节与唯一下一步

当前最可能的可用性瓶颈位于**语义/相对深度标定 → 公制投影 → Surface/Occupancy 地图**，但缺少严格受控因子效应，不能宣称已经找到根因。单纯新增输出 `observation_hint` 不提供运动授权。

**唯一下一步：直接执行** [`RUN_CONTROLLED_2X2_NOW.md`](../phase_prompts/RUN_CONTROLLED_2X2_NOW.md)，复用 E3 原有 481 帧、v5 checkpoint 和本地模型缓存，固定输入 hash，运行一次四条件、核对 D parity。没有数据则一次性列出具体缺项停止，不重复 OFF/ON E3、不多轮 smoke、不重新下载。

得到配对结果后只选一项改进方向：
- 语义 simple effect 主要决定差异：先查 class remap、walkable/curb/obstacle 角色映射；成本低、复用 v5，不触碰安全策略。
- 深度产品 effect 主要决定差异：先查 q→metric scale/shift、相机高度、法线/点投影；复用 DAV2，相比直接加入 Metric3D 更便宜、许可和集成风险低。
- 四条件都差：查看共同几何/地图更新和 session 场景构成；仅在证据要求时做少量可复核诊断，不因一个实验否定 Harvard/Mobilio 架构。

## 复用技术速览（只描述已有证据）

| 资源 | 状态 | 用途/注意 |
|---|---|---|
| Basalt | SELECTED / NOT_RUN | PC VIO 首选；BSD-3-Clause，依赖适配另核 |
| TUM VI room1 | CHECKSUM_PENDING / NOT_RUN | 首个标准 VIO 基准；需先解决环境/数据大小许可 |
| EuRoC V1_01_easy | SELECTED_BACKUP / SOURCE_LICENSE_PENDING | 第二 VIO 基准，暂无新证据 |
| ADVIO-09 | DEFERRED | 手机数据域 VIO；CC BY-NC 4.0，不用于未授权商业用途 |
| DAV2 relative Small + ground calibration | IMPLEMENTED / HISTORICAL_REPLAY | 当前新实验真实调用的深度产品，注意单位和标定 |
| DAV2 Metric VKITTI Small | HISTORICAL_57_FRAMES / NOT_THIS_RUN | 旧 57 帧另份证据，无独立测距真值；不得冒充相对模型 |
| Metric3D v2 Small | DEFERRED / LICENSE_PENDING | 深度替代候选，需核权重/焦距/尺度 |
| Harvard/Mobilio | REFERENCE_PORT / NOT_FULL_UPSTREAM_REPRODUCTION | 离线地图参考移植，当前评估地图层 |
| SANPO | EXISTING_481_FRAME_DEV_SET / RAW_IMU_NOT_VERIFIED | 胸前视角 GT 分割/深度、recorded pose proxy；许可按来源归档 |
| NavAble | DATA_CANDIDATE / NOT_DOWNLOADED | 无障碍语义补充，不替代几何 |
| OpenVINS/VINS-Fusion/RTAB-Map | BACKUP_ONLY / NOT_INTEGRATED | 只有明确定位/地图缺口才逐项选用，需核许可证 |

本提交没有新增模型/数据权重，不涉及新的大体积下载或付费。历史许可依据见总计划：Mobilio 上游 MIT、SANPO 文档 CC BY 4.0、DAV2 Small 模型 Apache-2.0；对具体分发权利仍按实际文件许可确认。

## 固定评分与阶段门禁

固定权重（架构10%、感知10%、定位15%、几何20%、地图15%、安全证据20%、可复现性10%）：
- 架构 7/10、感知 5/10、定位 2/10、几何 3/10、地图 3/10、安全证据 2/10、可复现性 6/10；
- 加权原值 **3.55/10**，按一位小数口径 **3.6/10**，**与上一轮不变**，因为仅 CODE_IMPLEMENTED/REPORTED_SYNTHETIC_TEST，没有新的真实能力验证。
- 选型成熟度：地图输入基线已有代码和历史真实数据、**受控比较 IMPLEMENTED/NOT_RUN**；定位 Basalt SELECTED/NOT_RUN；替代深度 DEFERRED。
- **P0–P6 完整门禁：0/7。** P0 输入审计部分完成、P1 VIO HOLD、P2 既有几何研究但新配对未运行、P3 地图离线代理有历史证据但 G2 根因 HOLD、P4 泛化未通过、P5 Last10m 回放非真实识别验收、P6 真机 HOLD。证据级别：本轮 `CODE_REVIEW / REPORTED_SYNTHETIC_TEST`；历史 E3 `DESKTOP_REAL_DATA_REPLAY`，不得提升为 DEVICE_VERIFIED。
- 测试耗时与覆盖：本审查未执行新测试（额外运行耗时 0；旧 11 tests 的原始运行时长未在新提交证明），无模型运行、无新指标；遵守不重复 smoke。审批提醒：无新的大下载/许可/敏感发布要求。

**裁决：ADJUST（执行入口成熟度提升）/ G2 HOLD（无新实测结果）。** 继续 PC-first、无 ARCore 必需依赖、Last10m 单独验收、冻结生产授权，禁止启动 Android 或合并 PR #20。
