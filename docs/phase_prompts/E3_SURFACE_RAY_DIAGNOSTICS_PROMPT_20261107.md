# E3 执行提示词：Surface 射线失败诊断与冻结 481 帧回放

日期：2026-11-07  
文档状态：`SPEC_READY`；这是下一阶段任务书，不是 E3 已完成的报告。  
编写时核对的代码快照：`cc8acaf6234214033b1d8475ce3b323790f7d9be`。  
目标仓库：`haomingweng3-eng/blindnav-superagent`。  
目标分支：`agent/corridor-obstacle-split-ad0888814215`。

> 执行本文件下面的任务。先读取仓库最新计划和证据，记录实际 checkout 的 commit；不得把本提示词中的历史基准、此前审查结论或计划命令当作本次运行结果。若新提交改变输入或算法，先报告差异，不默默混用快照。

## 1. 任务目标与边界

本轮只做 PC 离线 E3：解释同一批 SANPO-Real 481 帧中 Surface 为什么找不到候选射线、射线在哪里截断，以及哪些信息在候选筛选时被丢弃。保留原有地图和导航决策，只增加只读诊断证据。

本轮成功是“失败原因可以被独立复算”，不是“提高放行率”。不得降低最小射线长度、放宽未知区域容忍、缩小人体范围、忽略路面/路缘风险，或修改 CLEAR / STOP / 授权规则来制造改善。

不开展 Basalt、TUM VI、EuRoC、ADVIO、Metric3D 新实验；不做手机、Android、ARCore、NDK 或用户行走测试。不下载大型数据或权重，不在未获许可时安装依赖、清理磁盘或删除用户文件。E3 是现有证据的诊断支线，不代表 P0/P1 或总计划 P3 已通过。

## 2. 必须先读的仓库材料

- [总实验计划](../BLINDNAV_EXPERIMENT_PLAN_20261107.md)，重点是第 9.2 节。
- [Mobilio 反馈与 E1/E2/E3 状态](../phase_reports/mobilio_feedback_20261107/README.md)。
- [逐层状态矩阵](../phase_reports/mobilio_feedback_20261107/layer_matrix.json)。
- [E2 结果](../phase_reports/mobilio_feedback_20261107/e2_occupancy_hint.json)及 [E2 独立复核](../verification/MOBILIO_OCCUPANCY_HINT_INDEPENDENT_AUDIT_REFRESH_20261107.md)。
- [E3 已有代码级审查](../verification/MOBILIO_SURFACE_DIAGNOSTICS_INDEPENDENT_AUDIT_20261107.md)。该审查只覆盖既有 instrumentation，不能代替本次回放。
- [冻结 CSV 索引与 SHA256](../assets/review_20261107/mobilio_feedback_20261107/README.md)。
- [四组数字更正](../verification/MOBILIO_FEEDBACK_INDEPENDENT_AUDIT_CORRECTION_20261107.md)。不要沿用被更正的 29/21/25/269 数字。
- 代码：`pc/blindnav_pc/mobilio_surface.py`、`pc/blindnav_pc/mobilio_fusion.py`、`pc/tools/mobilio_sanpo_ab.py`；测试：`pc/tests/test_mobilio_surface.py`、`pc/tests/test_mobilio_surface_diagnostics.py`、`pc/tests/test_mobilio_sanpo_ab.py`。

## 3. 先核对历史基线，再开始实验

从 `docs/assets/review_20261107/mobilio_feedback_20261107/production_per_frame.csv` 明确筛选 `condition=production AND arm=mobilio`。四组 CSV 的对应条件是 `D_v5_dav2`，不得混入 baseline 臂或 annotated 条件。

历史报告给出的待复核计数：

| 项目 | 历史值 |
|---|---:|
| production / mobilio 帧数 | 481 |
| GT danger 帧数 | 295 |
| confirmed-clear 帧数 | 150 |
| 最终授权方向 | 49 |
| 最终 UNKNOWN | 275 |
| 危险帧上给出方向命令 | 17/295 |
| confirmed-clear 上 STOP | 0/150 |
| Surface UNKNOWN | 134 |
| 最终 reason 为 `surface:no_candidate_ray` | 125 |
| `no_metric_depth` | 9 |

A/B/C/D 的最终授权数是 29/15/57/49，各条件各臂 481 帧。不要把每条件两臂共 962 行当成单臂，也不要把 Surface CLEAR 或 observation_hint 当成最终授权。

先检查 `(condition, arm, session, frame)` 唯一性、各 session 帧键、所有布尔字段的解析规则和分母。安全指标中的 any_miss 是当前 GT 危险区域上发出方向指令的决策统计，不是感知召回率；LEFT/RIGHT 是否真正绕开危险仍需要对应轨迹真值。

最终 `reason` 是单值字段时，三个不同 reason 的行集合互斥；不同层的失败标志则可能重叠。分别统计最终主因、Surface 原因和 Occupancy 原因，不把不同层计数直接相加，也不沿用“同一单值 reason 可重叠”的表述。

## 4. G0：输入与环境预检

1. 记录实际 commit、分支、UTC 时间、Python/依赖版本、硬件、可用空间和原始 `git status`。隔离实验目录，不 reset、clean、stash、覆盖或提交其他人的未提交改动。
2. 核对现有 CSV 的实际 SHA256 与索引；同时列出重放所需原图、v5 checkpoint、DAV2 版本/缓存、内参、修正位姿、ground calibration、帧顺序及 GT 评分输入。可以复用已验证缓存，但必须绑定同一帧、同一预处理和同一来源。
3. 区分 CSV 统计可复算与完整流水线可重放。CSV 本身不能恢复丢失的射线、地图历史、深度或模型输出。
4. 若缺 SANPO 原图、几何缓存、checkpoint、磁盘或依赖，生成 `missing_inputs.json` 和最小补齐方案，结论为 `HOLD / NOT_EVALUABLE`。不得复制旧结果伪装重跑，不得从 GT 生成生产输入，不得自动换数据或安装框架。
5. SANPO 原始 IMU 缺口不阻塞使用已标明来源的 recorded-pose 离线诊断，但该结果绝不能标成 RGB+IMU VIO 或真机验证。

## 5. G1：先证明诊断能覆盖失败射线

重要：在编写本任务书时，`raycast_route()` 只把达到 `min_candidate_m` 的射线放入候选列表；列表为空时直接返回 `no_candidate_ray`，新诊断字段仍是默认 0/None。仅给最终选中 route 增加字段，不能解释这类失败。

因此，不要直接耗费资源重跑 481 帧。先用小型测试证明：

- 原始选路和返回的安全状态保持不变。
- 筛选之前的射线能够被独立 trace/collector 捕获，包括未达到最小长度而被丢弃的射线。
- 最终 route 为 UNKNOWN/无候选时，旁路诊断仍可记录实际扫描过的角度、路径长度与截断原因。
- 整张地图确实没有观测时，明确写 `NO_OBSERVATION`；诊断未执行时写 `NOT_RECORDED`；没有公制几何时写 `NO_METRIC_DEPTH`。不能用同一个数值 0 混淆这些情况。

若现有实现无法满足这些要求，允许提出或完成最小的离线只读诊断补充及对应测试，改动须限于 Surface trace、evaluator 导出和测试，不得改变判定、排序、地图更新、阈值或授权。记录完整 diff；任何需要改变算法行为的修复另开提案，本轮不执行。

建议旁路保留全部扫描射线，或保留可确定重建的逐射线记录。可额外定义“最佳被拒绝诊断射线”，采用预先固定的排序，例如连续已观测可走长度、离目标角偏差、角度作为稳定 tie-break；它必须使用独立字段，绝不替代原算法选中 route。

最小测试覆盖：空地图、存在短走廊但未达到长度门槛、未知 gap 超限、NON_WALKABLE 截断、HAZARD 截断、完整可走射线、搜索边界/最大距离，以及 trace 开关前后导航输出等价。注意末尾尚未结算的 pending unknown 与历史 unknown 总数可能不同，应分别记录，不修改原算法来掩盖差异。

## 6. 必需诊断字段

### 每帧主记录

`run_id, code_commit, input_manifest_sha256, condition, arm, session, frame, timestamp, pose_source, depth_source, trace_status`；Surface/Occupancy/fused state、最终 reason、effective_command、authorized_motion，以及既有评分字段。

GT 字段仅附加在评分输出中，不允许诊断射线选择或生产算法读取它们。

### 射线旁路记录

至少包含：

- 帧键、`ray_id`、实际扫描角度、角度单位与坐标约定；
- `selected_by_original_algorithm`、是否通过原长度筛选及拒绝标志；
- 原始 `observed_distance_m`、`fully_observed`、`unknown_cells`；
- 连续已观测可走长度（另设字段，不把最远可走位置冒充连续通路）；
- `termination_distance_m`、`last_walkable_distance_m`；
- termination / last-walkable cell 的 ix、iz；
- terminal role、实际停止原因（未知 gap、非可走、hazard、最大范围等）；
- 射线总单元数、可走/未知/危险/非可走单元数、末尾 pending unknown run；
- 最佳被拒绝诊断射线的标记及固定排序规则；
- 观察来源、证据年龄等字段只在已有输入可提供时输出，否则为 null，并说明不可评估原因。

角度、长度、原图像素、相机坐标、局部地图坐标和 cell 索引必须明确对应。`termination_distance_m` 与“安全可走距离”不是同义词。旁路字段不可进入 TTS 或运动授权。

## 7. G2：冻结输入，运行 A/B 诊断回放

1. 先验证并保存旧版冻结 evaluator 的基线输出；历史 CSV 与现时基线不一致时，先出差异报告，不通过调参强行凑数。
2. 在同一环境、同一输入、同一 session 顺序、同一状态初始化下运行 baseline（trace 关闭）与 diagnostic variant（trace 开启）。固定 v5、DAV2、位姿、标定、地图更新、所有阈值和随机种子。
3. 主臂只做 production D 的 481 帧。A 条件可以作为后续对照提案，但不自动扩大本轮范围或下载。
4. 使用当前 checkout 的 `pc/tools/mobilio_sanpo_ab.py --help` 核对真实参数后保存实际命令，不编造 CLI 参数或运行输出。所有日志保留退出码；输出单独放入 `pc/out/e3_surface_<run_id>/`，不得覆盖历史实验。
5. 对齐每个帧键，逐字段比较 baseline 与 variant 的 Surface/Occupancy/Fusion 状态、effective_command、authorized_motion 及实际存在的语音决策字段。缺失 TTS 集成时标为未验证，不宣称端到端语音安全。
6. 分别报告与历史 CSV、与同输入基线的差异。导航决策字段发生变化即停止晋级；即使总数相同，也必须核对逐帧是否被交换。

## 8. 分析与可视化

按 session、GT danger / confirmed-clear / 其他、Surface/Occupancy 组合与最终主因分组输出：

- 134 个 Surface UNKNOWN 中，哪些没有几何输入，哪些实际扫描后无候选；对 125 个 `no_candidate_ray` 重点逐帧追踪；
- 短于原长度要求、unknown gap、NON_WALKABLE/HAZARD、有限视野/网格未观测等证据；没有证据时写未确定，不能单凭 reason 推断几何根因；
- 射线连续长度、终止距离、未知比例、诊断覆盖率及各组分母；
- authorized guidance、confirmed-clear guidance、any_miss、true_false_stop、UNKNOWN/OBSERVE/BLOCKED/CLEAR、翻转率；所有指标保存计数与定义；
- 代表性原图/已有几何/网格/选中射线/被拒绝射线叠加。只能画真实记录；数据缺失时标为不可视化，不绘制假路径。

原始数据许可不允许公开或含隐私时，只提交允许发布的派生证据、相对路径 manifest 与复现说明，不上传原始数据、模型权重或凭证。

E2/E3 都是非授权诊断，授权不变是设计约束。不能因为 hints 增加而称导航改善；也不能因为授权没有变化就证明唯一瓶颈在上游。只有针对某层的独立、受控证据才能支持根因结论。

## 9. 安全不变量与门禁

| 门禁 | 通过要求 |
|---|---|
| 输入 | 481 个冻结生产臂帧键可追溯，哈希与实际文件绑定；缺失/重复有明确处置 |
| 诊断有效性 | 存在已观测短射线但原算法无候选的测试中，旁路诊断不再丢失实际射线 |
| 非干预 | 同输入 trace 开关前后逐帧导航状态和授权字段完全一致；不得只填一个恒为 true 的标志 |
| 指标复算 | 独立程序从逐帧数据重算，481/295/150 等分母及历史差异有解释 |
| 完整性 | 真实运行日志、版本、配置、输入哈希、失败记录与诊断文件齐全 |

`GO`：完整 481 帧诊断可重放、非干预断言通过、根因证据可独立复查；仅允许进入下一次诊断评审，不等于部署批准。  
`ADJUST`：诊断字段仍丢失失败射线、汇总定义错误或需补测试；只修诊断，不调安全策略。  
`HOLD`：输入/磁盘/依赖不足或无法完成真实回放；提供精确最小阻塞清单。  
`STOP`：发现 GT 泄漏、基线被覆盖、授权变化、危险行为、违规上传或证据伪造；保留日志并停止当前变体。

单测通过只能标 `UNIT_VERIFIED`；完整运行后才标 `REPLAY_COMPLETED`；独立复算通过后才标 `INDEPENDENTLY_CHECKED`。不能把交付文件完整性 PASS 当成实验性能 PASS。

## 10. 交付物与独立复核

报告目录建议 `docs/phase_reports/e3_surface_<run_id>/`，每次运行独立，不覆盖旧结果：

- `README.md`：假设、真实执行、结果、限制、GO/ADJUST/HOLD/STOP；
- `run_metadata.json`：代码 SHA、UTC 时间、环境、命令、退出码、配置和模型哈希；
- `input_manifest.csv` 与必要时 `missing_inputs.json`；
- `per_frame.csv`、`ray_diagnostics.jsonl`（或等价格式）；
- `metrics.json`、`per_session.csv`、`failure_buckets.csv`；
- `decision_equivalence.json`：被比较字段、帧键、差异计数和差异样本；
- `tests.log`、`replay.log`、独立复算脚本与日志；
- 适量、许可允许的可视化与 `artifact_manifest.sha256`。

独立核查必须读取同一 commit 的实际文件，检查 schema/哈希/唯一帧键，并用独立聚合逻辑复算，不只调用原脚本后宣称独立。报告必须区分检查了代码、运行了单测、重算了 CSV、重跑了完整模型流水线四种证据。

首轮可尝试的定向单测命令（先确认这些文件在实际 checkout 中存在）：

```bash
PYTHONPATH=pc python -m pytest -q \
  pc/tests/test_mobilio_surface.py \
  pc/tests/test_mobilio_surface_diagnostics.py \
  pc/tests/test_mobilio_sanpo_ab.py
```

依赖缺失时保存失败日志，不自动安装庞大依赖，不把没有执行的测试记成通过。

## 11. 仓库操作限制与最终回复

本提示词的发布仅新增本 Markdown，不启动实验。执行阶段也不改 main、不新建分支、不改 PR #20 元数据、不合并、不 force-push、不修改 Android 或安全决策实现。

新诊断代码和产物先在隔离环境生成并审查；提交只限明确获准的诊断/测试/报告路径，不使用 `git add .`。用户明确授权提交阶段报告时，只上传脱敏且可公开的小型成果；推送现有 PR 的源分支会使 PR diff 增加，必须在变更说明中透明记录，但不更改 PR 状态。

最终向用户报告实际执行到了哪个门禁、哪些数值独立复核通过、哪些仍未执行、阻塞是什么、具体变更文件与 commit，以及下一步最小实验。不要再提供空的“复制提示词”按钮，不承诺后台自动推进，不以换模型或放宽安全规则绕开失败。
