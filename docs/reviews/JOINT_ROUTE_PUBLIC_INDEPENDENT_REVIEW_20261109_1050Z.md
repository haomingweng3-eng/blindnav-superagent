# BlindNav 公开 SANPO 联合路径候选独立审查（2026-11-09 10:50 UTC）

> **ADJUST / PC G2 HOLD；安全运动授权 HOLD。** 本次仅评审新增真实离线产物；不改生产实现、不重新运行模型、不更改 PR #20。开发分支 `agent/corridor-obstacle-split-ad0888814215`，受审 HEAD `e846cebb3f117b78b6279851ba32e72b211aa315`，上一受审 HEAD `b0a23bf7731c02ca557e29a235a729137e1f8a10`；新增 1 个提交、30 个文件（包含新 PC 代码与真实输出），不是自动审查自身的文档提交。

## 1. 数据等级、可复核输入与实际运行

来自 `docs/phase_reports/joint_route_public_20261109/`：
- 新的 `Depth-Anything-V2-Small-hf` 相对深度 **36 次真实 CPU 模型推理**；来自三个 SANPO-real session，每段前 12 个标注帧（帧序号 0..55，步长 5），**不是原 481 帧生产链完整运行**。36 张深度图输出有限值；推理阶段 28.639 s（模型计算中位 0.461 s，含加载的总阶段时间不同于手机 FPS）。
- 两条件 `public_gt_reference`（GT 语义 + 参考深度/地面平面）和 `public_gt_calibration_dav2`（GT 语义/校准 mask + 新 DAV2 相对深度的标定）。分别比较 `original_mobilio` 与独立 `joint_route_candidate`；比较阶段 67.417 s，日志退出码 0，重用经过校验的推理缓存，不是重复 36 次模型推理。
- 独立从 `before_after.csv` 和 `per_frame.csv` 重新读取并计数：**72 条唯一 condition/session/frame 配对、144 条唯一 condition/arm/session/frame 记录，没有重复键**；每条件 36 帧，33 READY/3 NOT_READY；NOT_READY 不作为位置证据、保持 UNKNOWN。CSV 复算与报告 `summary.json` 一致。
- 下载清单列出 114 个数据文件及 3 个 DAV2 文件的大小、SHA256、来源；`root_verification.json` 声明运行侧重算通过。本审查读取了这些记录，但未取得本地全部原始图像/张量重新独立 hash；因此独立确认等级是 **CSV_RECALCULATED + EXECUTION_LOG_REVIEW + SOURCE_INSPECTED**，原始文件字节完整性为 **REPORTED_VERIFIED**，非本审查亲自重验。
- 运行使用的源码曾处于未提交工作树，元数据记录了各文件 SHA256，事后归档了 `fresh_inference/harness_source_used.py`；36 次推理的原 stdout 没有单独原始文件，`execution_record.json` 明示事后追溯与开始时间未知。保留此审计限制，不将归档的逐帧 JSON 伪称原 stdout。
- 参考方向来自对 READY 记录轨迹的 **非因果 +/-4 米拟合**，不是系统自行发现直行；33/36 READY 仅指记录位姿可用，不等于实时 VIO。三段短启动切片与 481 帧完整时序的目标/状态不同，**不可直接比较分子分母或宣称统计显著**。

## 2. 关键结果：本人从逐帧 CSV 重新汇总

| 输入/arm | CLEAR=授权 | UNKNOWN | OBSERVE | BLOCKED | confirmed-clear 代理授权 | danger 代理授权 |
|---|---:|---:|---:|---:|---:|---:|
| GT 参考深度·旧 | **3/36** | 4/36 | 26/36 | 3/36 | 1/9 | 0/16 |
| GT 参考深度·新 | **0/36** | 26/36 | 7/36 | 3/36 | 0/9 | 0/16 |
| DAV2 标定·旧 | **0/36** | 25/36 | 11/36 | 0/36 | 0/9 | 0/16 |
| DAV2 标定·新 | **0/36** | 30/36 | 6/36 | 0/36 | 0/9 | 0/16 |

三个 session 的新方向授权全部 0/12；旧 GT 参考条件依次是 1/12、2/12、0/12。每种输入各有 confirmed-clear 9 帧、danger 16 帧，剩余 11 帧为其他分类。**没有证明正确直行、碰撞回避率或真实绕障成功率**；17 旧危险代理放行仍来自另一份 481 帧实验，不是本 36 帧结果。不能把 BLOCKED=0 称为没有错误拒绝。

独立核对旧 `historical_csv_audit/authorized_route_pairs.csv` 和按 session 汇总：旧 481 帧原授权 49 条中 **45/49** 存在地面/占据独立选择方向的角度差；按 session 为 9/10、34/37、2/2，风险代理中 16/17。此为**接口契约缺口**，角度差异不等于错误转向或碰撞；角度一致也不能证明人体通行带安全。

## 3. 失败归因、代码审查与必须纠正的主张

- 新候选的 36 帧拒绝原因：GT 参考条件 **23 路径地面未知 + 7 即时未确认占用 + 3 已确认即时占用 + 3 NOT_READY**；DAV2 条件 **20 路径地面未知 + 6 未确认即时占用 + 7 标定无公制几何 + 3 NOT_READY**。从 `failure_analysis.json` 与 `before_after.csv` 逐条件复核分母均为 36。
- DAV2 标定 26/33 READY 成功（19 当次接受、7 复用）；另 7 个失败均集中于 session `-PqSDmiEe2pXjmYHgxh4YEBsj0T5LU10` 的帧 5..35（步长 5），原因 `low_confidence+stale`，置信度 0.281–0.347。足够多的地面采样像素不能替代拟合可靠性；不能直接调低最小置信度。
- 完成路径搜索的 GT 参考 23/23 与 DAV2 20/20 帧，候选通行带在**起始足迹处**即存在地面/障碍未知；这一共同模式说明近身可观测性/网格证据与严格授权不匹配，不能把全部问题归咎于 DAV2。短启动片段不能推断完整序列的地图覆盖。
- `pc/blindnav_pc/mobilio_joint_route.py` 是新的 **独立 PC 候选**：在同一 origin/angle/snapshot 对 surface 与 occupancy 扫掠带检查；`raycast_clearance` 旧邻域重搜索不能当作单方向检验；当前新实现避免了这一用法，保留即时人体占用优先、未知不填 FREE、只做候选证书。旧 `mobilio_fusion.py` 和生产/Android 逻辑未修改。
- **关键安全限制：**候选障碍层的观察是“ground-height observed column”约定，未证明人在 0.15–1.7 m 的整个三维体积都空闲。源码允许离线 `movement_authorized` 与 `full_volume_visibility_certified=False` 并存，构造地图测试也验证了这一点。因此它仅是地图契约的离线授权标签，**严禁映射为真人/手机运动许可**。共同验证范围约前 2 m 的障碍带，地面带约 5 m，不是 5 m 的完整三维安全通道。
- **纠正原判断：**此前单凭异向融合反例提出“修同路方向就能解决主要覆盖问题”证据不足；本轮实际反例修复候选在 36 帧里**0/36 授权**。它解决了同方向接口设计，但暴露近场和标定缺口，没有带来可用性收益。将“修复完成”限于合成契约与离线组件，不把它说成生产复现、物理避障。
- 参考深度和 DAV2 条件不仅换深度，还换了标定/地面平面及有效高度；**不能把 3/36 对 0/36 解释为深度模型纯因果作用**。旧校正 2×2 分析限制依旧适用。
- 原 v5 权重 `segformer_sanpo_b0_v5.pt` 尚不可在这次执行环境取得；历史文件记录 14,947,603 B，SHA256 `952c2a156ab43090aa7aa4088a812b21f0639e75bb2eb8d8d7851696a17696bf`。这是完成原 481 帧生产条件复验的**新确认执行阻塞**，不是模型精度差的证据。不得以另一公开权重冒名，也不得将受限权重公开提交。

## 4. 许可、技术路线与唯一后续任务

- 官方 SANPO 数据主页说明 Creative Commons 4.0 许可，项目既有资料标识 **CC BY 4.0**；数据署名、来源和可能的隐私约束须随数据发布保留。SANPO 仓库代码的 Apache-2.0 不是原始数据许可。参见 https://google-research-datasets.github.io/sanpo_dataset/ 。
- `depth-anything/Depth-Anything-V2-Small-hf` 模型卡明确 **Apache-2.0**，revision `5426e4f0f36572d16453bbda7a8389317b1bef99`，不能将 Small 与 Base/Large 的非商业条款混淆。参见 https://huggingface.co/depth-anything/Depth-Anything-V2-Small-hf 。原 v5 微调权重的重新分发授权需单独核查，不直接公开上传。
- **首选（GO 到下一项离线实验，不是导航 GO）：**回到原 E3 执行机寻找已使用的 v5 权重与冻结 481 帧，核验 SHA 与输入；仅在同一录制时序/标定下完成 **一次** 481 帧 old-vs-joint 独立地图对照；导出每条路径的近场观测证书和分 session 结果。可复用缓存，最符合已有研发成本。
- **备选：**原权重无法恢复则只用已归档的 36 份 DAV2 + GT 证书做 **一次近场可观测性分段归因**（近场实际未观测 / 有标注但投影丢失 / 时序地图未保留），不可计算 v5 生产效果。不要反复下载/运行 DAV2。
- **不建议：**降低 UNKNOWN 或占据阈值、给近身未知格填 FREE、使用未来 GT 轨迹当直行推断、直接接 Android、直接切 Metric3D/RTAB-Map/OpenVINS，或继续做演示网页。新的公开下载或依赖安装需另获用户批准；现有本地权重的私有校验与复用可由执行端完成。
- 已有复用状态：相对 DAV2 Small **REAL_36_INFERENCE**（GT 语义、不是 v5 链）；Mobilio-inspired 地图 **PC_JOINT_CANDIDATE_EXECUTED / NO_UTILITY_GAIN**；SANPO **PUBLIC_36_SLICE / HISTORICAL_481_DEV**；Basalt/TUM VI **SELECTED / NOT_RUN**，不能阻断当前地图实验；原 v5 **HISTORICAL_RUN / FILE_MISSING_ON_NEW_RUNNER**；Android **NOT_VALIDATED**；Last10m **SEPARATE_HOLD**。

**唯一执行入口：** [`docs/phase_prompts/RECOVER_V5_AND_VALIDATE_JOINT_ROUTE_20261109.md`](../phase_prompts/RECOVER_V5_AND_VALIDATE_JOINT_ROUTE_20261109.md)。原 `PC_ACCEPTANCE_TODAY.md` 保留为上阶段语境，下一项以新执行入口为准。优先原执行机，避免本地无用下载。

## 5. 固定评分与门禁

| 维度（权重） | 分项 | 本次变化 |
|---|---:|---|
| 架构 10% | 7/10 | 0：接口候选是隔离实验，不是已部署导航 |
| 感知 10% | 5/10 | 0：36 个 DAV2 推理不包含 v5 实际分割 |
| 定位/时序 15% | 2/10 | 0：仅 SANPO 记录位姿，无新 VIO |
| 深度/几何 20% | 3/10 | 0：7 次标定失败与近场证据缺失未修复 |
| 地图/导航可用性 15% | 3/10 | 0：新候选 0/36 授权、无可重复收益 |
| 安全证据 20% | 2/10 | 0：未有完整人体体积可见性或闭环安全验证 |
| 可复现性 10% | 6/10 | 0：真实日志与哈希链改善审计，但原 v5 完整链仍缺，且推理 stdout 为追溯记录 |

**加权 3.55/10 → 既定口径 3.6/10；与上次一致。** P0–P6 完整门禁仍 **0/7 GO**；局部真实组件回放并不等于 G2 通过。技术成熟度：独立路径候选已 CODE+CONTRACT_TEST+PUBLIC_COMPONENT_RUN，原生产链匹配和独立盲人导航试验均 NOT_VERIFIED；证据等级为 DESKTOP_COMPONENT_REPLAY / GT_ORACLE_ASSISTED，低于 PRODUCTION_PIPELINE_REPLAY 和 DEVICE_VERIFIED。

**测试时间：** 原报告记载模型阶段 28.639 秒、配对阶段 67.417 秒、17 项合成/旧融合测试通过（未提供本次测试总耗时）；本轮独立审查只读 CSV/JSON/源码，**额外模型运行 0 次、额外 smoke 0 次**。不将 17 个测试通过视为导航收益。

**及时提醒：**新确认的原 v5 私有资产缺失、近身证据无法验证及候选无实际授权正例均需执行侧立即处理。禁止公开上传可能受限的权重；不请求安卓或真人实验。只有真实新能力证据才调分；同一阻塞后续不重复通知。
