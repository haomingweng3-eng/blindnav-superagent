# BlindNav 全项目实验计划与阶段门禁（2026-11-07）

> 状态：研究计划（不是实验结果，也不是安全认证）
>
> 本计划根据当前公开分支中的项目路线图、Last10m 协议、Harvard/Mobilio 对照报告、FillGrid 修复回放，以及 PC-first 资源审计整理。后续必须根据可复现的实验反馈更新，不以新增代码量作为进度指标。

## 0. 总原则

1. **先复用，后开发。** 优先采用已有公开数据集、成熟算法和官方实现；只有在接口、许可或能力确实不匹配时才自行适配。
2. **PC-first，手机最后。** 所有数据完整性、算法基准、深度几何和地图对照先在独立桌面环境完成。小米手机与 Android/NDK 集成是最后阶段，不是当前阶段。
3. **不把不同证据等级混为一谈。** 离线数据、桌面回放、原型、真机验证、用户研究分别报告。桌面结果不能称为真机结果；回放模型回答不能称为实时识别。
4. **安全逻辑冻结。** 在定位和几何输入通过阶段门禁前，不调整 Path Keeper、Obstacle Guardian、Safety Coordinator、STOP/运动授权语义，也不通过调阈值追求更好看的指标。
5. **每次只改变一个主要因素。** 固定帧、标注、决策逻辑和评估脚本，单独改变定位、深度、分割或地图输入，避免无法归因。
6. **失败也必须保存。** 保留失败日志、无效帧、跟踪丢失、UNKNOWN、误停和漏检，不得只展示成功片段。
7. **不作安全声明。** BlindNav 当前不是认证的助行设备；所有实验均为研究验证，不能替代盲杖或其他既有出行辅助。

## 1. 项目目标与工作流

BlindNav 有两个相关但必须分开验收的产品/研究工作流：

### A. Last10m：目标商店与入口确认

目标是让系统在最后几米中确认“用户要去的商店”与“实际入口”之间的关系，并在入口和门前地面证据充分之前拒绝授权运动。

现有链路：
`ReplayVisionReasoner / OCR → PerceptionOrchestrator → Last10mStateMachine → ArrivalAuthority → Last10mSpeechPolicy`

必须分开评估：
- 目标识别与 target lock；
- 门候选生成；
- SAME_TARGET / NEIGHBOR 入口关系；
- 缺失视角与 NeedView / ScanCoach；
- 门前地面和障碍证据；
- ArrivalAuthority 的运动授权；
- 语音提示和拒绝行为。

现有 2026-11-07 demo 是 3 个场景、7 帧的真实街景图片回放，输入来源必须标记为 REPLAY。它验证流程与拒绝场景，不代表实时端到端识别准确率。没有入口关系标注的帧必须标记 NOT_EVALUABLE，不计为正确入口。

### B. Path Keeper / Obstacle Guardian：局部可行走空间与障碍物

目标是从 RGB、深度、相机位姿和地面几何建立可信的局部空间证据，判断前方路面和三维障碍物风险，再由独立安全层决定 CLEAR / OBSERVE / UNKNOWN / BLOCKED。

现有模块：
- Path Keeper：路面、路缘、边界和行走走廊；
- Obstacle Guardian：占据人体行走扫掠空间的三维物体；
- Safety Coordinator：风险状态融合与授权约束；
- SurfaceGrid / OccupancyGrid：局部地图和时序证据。

两条工作流可共享相机、定位、深度和数据管理基础设施，但不可混用验收指标。Last10m 的“入口关系准确率”不能替代障碍物安全指标；障碍物漏检率也不能代表入口识别成功率。

## 2. 当前已知基线与限制（截至 2026-11-07）

### 2.1 Harvard / Mobilio 研究证据

- Harvard-Slade-Lab/Mobilio 的参考移植基于上游 `Vision.cs` 与 `DepthImage.cs`，上游项目标注 MIT 许可。
- 当前离线参考移植没有复现原始 ARFoundation、ARKit/ARCore、Sentis 或原始分割模型；它消费的是已经准备好的世界坐标输入。
- SANPO-Real 481 帧、3 个 session 的结果属于 DESKTOP_REAL_DATA，位姿是 recorded-pose offline VIO proxy，不是真实 RGB+IMU VIO，也不是手机验证。
- 生产 D 条件（v5 segmentation + calibrated DAV2）中，Mobilio 报告 any-danger miss 17/295（5.8%）、guidance coverage 49/481（10.2%）、UNKNOWN 275/481（57.2%）；Baseline 对应 123/295（41.7%）、220/481（45.7%）、214/481（44.5%）。这显示漏检下降伴随严重可用性损失，不能视为已成功采用。
- Harvard FillGrid parity 修复将 D 条件 any-danger miss 从 39/295 降至 9/295，但方向授权只有 13/481、UNKNOWN 405/481，确认安全路面误停 21/150。该结果是参考实现语义修正，不是部署结果。
- 现有报告对剩余危险占用漏检的几何归因约为 86.1%。这是当前归因工具和这组数据下的结果，不足以单独证明重力误差是唯一根因。

结论：Path Keeper / Obstacle Guardian / Safety Coordinator 当前处于研究冻结状态。先修复或替换空间输入，再重新评估地图和决策，不继续进行无新证据的阈值迭代。

### 2.2 Last10m 证据

- 当前公开 demo 包含 3 个真实街景场景、7 帧结构化回放。
- 回放来源明确为 REPLAY，不得称为手机实时 VLM 识别。
- 现有 Entrances 门框数据没有 target-store-to-door 关系真值，不能用门框召回率代替入口准确率。
- 入口关系准确率只在有明确 correct entrance / SAME_TARGET / NEIGHBOR 标注的帧上计算。
- Android 真机执行、完整依赖构建和用户研究仍未完成；未完成的项目必须继续标记 NOT_CONNECTED / NOT_EVALUABLE。

### 2.3 数据现状

- 当前 SANPO-Real 481 帧来自 3 个 session，足以进行故障归因和初步对照，不足以证明跨场景泛化。
- PC-first 审计发现本机两个 SANPO smoke session 各有 12 张连续左目图，但可用位姿分别为 2/12、11/12；没有同步右目、深度或原始 IMU。
- 本机 SANPO 分割样本约 6,648 个 PNG，属于抽样图像/掩码，不能当作 VIO 序列。
- SANPO 官方说明提到真实 session 有 IMU，但已检查的两个公开对象目录和本地样本未找到 IMU 原始观测。此差异尚未解决：不得假定本地已拥有 IMU，也不得用位姿 CSV 代替 IMU。
- 现有 DAV2 Metric VKITTI Small 的 57 帧对照和 10 个 Depth Pro 锚点结果应复用，不重复消耗资源。报告没有独立测距真值，因此不能据此断言哪种深度更准确。
- 本机尚未找到 Basalt 可执行文件，也未完成 Basalt / Harvard-Mobilio 新流水线复现。

## 3. 数据与开源资源复用策略

先确认版本、数据结构、标定、许可、哈希和可用真值，再决定下载。禁止未经批准下载完整大型数据集或模型权重。

| 资源 | 主要用途 | 当前策略 | 关键限制 |
|---|---|---|---|
| SANPO | egocentric 分割、深度、姿态/IMU数据审计 | 先查官方文件清单与本地缺失原因；现有 481 帧继续用于离线归因 | IMU 对象路径尚未确认；必须使用修正位姿文件；不能把抽样分割帧当连续 VIO |
| TUM VI room1 512×512 | 手持相机 VIO 桌面基准 | Basalt 成功构建后优先使用单序列 | 审计记录文件约 1.59 GiB；下载前校验官方 URL、MD5、许可和目录格式 |
| EuRoC V1_01_easy | 标准双目+IMU VIO 基准 | 第二个基准；先确认 ETH 当前单序列下载位置、大小和许可 | 不下载整个数据包；保留官方标定和真值 |
| ADVIO-09 | 真实手机 RGB+IMU 场景与格式适配 | 后续单组测试 | CC BY-NC 4.0；限符合非商业许可的研究用途；不是 Basalt 原生 EuRoC 格式，必须验证时间戳/标定转换 |
| DAV2 Metric VKITTI Small | 深度基线 | 复用本机权重和 57 帧既有结果 | 现有对照缺少独立距离 GT |
| Metric3D v2 Small | 深度、置信度、表面法线候选 | 先核对权重许可、文件大小、输入焦距约定，再小规模对照 | 代码许可不自动覆盖模型权重许可；公制转换依赖真实相机焦距 |
| NavAble | 无障碍设施感知 | 按类别选择少量数据研究 | 不代替障碍物深度/几何数据；核对数据版本、许可和标签定义 |
| Basalt | VO/VIO 桌面基线 | 首选定位基准 | BSD-3-Clause 代码；逐项审查第三方依赖；当前尚未运行 |
| OpenVINS / VINS-Fusion / ORB-SLAM3 | 独立定位对照 | 仅在 Basalt 基线成功或出现明确能力缺口后选择一个对照 | GPL-3.0 等许可需评估；不同时集成多个框架 |
| RTAB-Map | RGB-D/视觉地图对照 | 仅在需要独立地图基准时研究 | 不作为 Android 即插即用组件 |
| Harvard Mobilio | 地图与局部路径算法参考 | 保留现有独立参考实现；先核对上游源码、坐标约定和输入契约 | 不声称已复现原始 AR 输入系统 |

## 4. PC-first 实验阶段

### P0 — 数据完整性与可复现环境审计

**目的：** 建立可信的实验输入和隔离环境。

任务：
1. 不在已有未提交改动的 BlindNav 工作树内安装或构建。
2. 建立独立 Linux/WSL 实验目录，记录 OS、编译器、CMake、Ninja、CUDA/CPU 等环境。
3. 核对 SANPO 两个 session 的官方对象清单、本地文件树、下载脚本过滤条件和 IMU 文件命名；保存查询结果和文件清单。
4. 对现有 481 帧、57 帧深度结果及其脚本、配置、模型版本建立 manifest；不重复运行既有实验。
5. 核对 TUM VI、EuRoC、ADVIO 下载源、单序列大小、校验和、数据许可。
6. 核对 Harvard Mobilio 上游源码版本、MIT LICENSE、FillGrid 和 DepthImage 坐标/高度约定。

交付物：
- `environment_manifest.json`
- `dataset_inventory.csv`
- `license_register.md`
- `sanpo_imu_gap_report.md`
- `existing_evidence_index.md`

通过门槛：
- 每个待运行数据都有明确来源、版本、许可、校验方式和真值说明；
- SANPO IMU 未找到之前，不安排 SANPO VIO；
- 不把任何本机路径、用户名、令牌或私有数据写入公开报告。

### P1 — Basalt 桌面 VIO 最小复现

**目的：** 先证明成熟定位框架能在公开基准上可重复运行，不涉及 BlindNav 地图。

顺序：
1. 在独立 Linux/WSL 环境按 Basalt 官方文档构建或使用官方二进制。
2. 首选 TUM VI room1 512×512 单序列，校验 MD5 和目录格式。
3. 运行官方 VIO 示例，保留原始命令、配置、日志、输出轨迹和程序版本。
4. 对齐官方参考轨迹，计算 ATE、RPE、有效跟踪帧比例、初始化失败、丢跟踪和恢复情况。
5. 再运行 EuRoC V1_01_easy，作为标准双目/IMU 对照。
6. 只有在 VIO 基线可复现后，再评估 ADVIO-09 的格式转换；不得直接把 ADVIO 当成 EuRoC 格式输入。

不做：
- 不下载全部 TUM VI / EuRoC / ADVIO；
- 不把 Basalt 的 KITTI VO 示例误称为 ADVIO/TUM VI VO 验证；
- 不把公开数据上的结果称为小米手机结果。

通过门槛：
- 官方数据与标定可被程序正确读取；
- 轨迹可生成并可与真值对齐；
- ATE/RPE 计算脚本可重复；
- 成功率、失败日志和数据覆盖完整报告；
- 若构建失败，先记录依赖/编译错误，不立即更换算法。

### P2 — 深度模型与几何输入对照

**目的：** 判断深度、相机内参、姿态/重力和坐标变换各自造成多少误差。

任务：
1. 复用已有 DAV2 57 帧结果，不重复跑相同模型和相同输入。
2. 核对原有报告中的图像预处理、尺度、内参、焦距、深度输出单位和后处理。
3. 在权重许可和下载量确认后，选少量相同帧运行 Metric3D v2 Small。
4. 使用有真实距离/深度 GT 的公开样本计算深度误差；没有独立真值时，只报告模型间差异，不报告准确率胜负。
5. 将分割固定，分别替换深度输入、姿态输入和地面法线输入。
6. 做受控消融：参考深度+参考位姿、预测深度+参考位姿、参考深度+预测位姿、预测深度+预测位姿。
7. 记录深度尺度、无效像素、置信度、地面法线角度、障碍物高度和空间位置误差。

通过门槛：
- 输入图像坐标、深度像素、内参和位姿时间戳严格对应；
- 公制深度有独立真值或明确标记为不可评估；
- 误差可归因到深度、位姿、地面估计或投影环节；
- 不允许用模型输出的“米”单独解除安全告警。

### P3 — Harvard/Mobilio 地图离线适配

**前置条件：** P1 定位输入可复现，P2 几何输入具备明确质量报告。

任务：
1. 逐项核对上游 Harvard Mobilio 的坐标系、相机高度、重力方向、FillGrid 更新、可见自由空间衰减和占据高度约定。
2. 建立独立的输入适配层：深度、置信度/无效掩码、相机内参、时间戳、位姿、跟踪质量。
3. 使用同一帧、同一标注、同一冻结决策逻辑，对照 recorded-pose proxy 与 Basalt 位姿。
4. 比较地图重影、障碍物位置漂移、错误清除、UNKNOWN、危险漏检、确认安全路面误停和导航覆盖。
5. 保留 Harvard reference、BlindNav baseline 和候选地图三个独立结果；不覆盖历史产物。
6. 若地图改善但授权覆盖显著下降，必须报告为安全/可用性权衡，不得单指标宣称成功。

通过门槛：
- 坐标和时间同步单元测试通过；
- GT 只用于评分，不进入生产决策；
- 地图质量改善可在不同 session 重复；
- 危险漏检、误停、UNKNOWN 和覆盖率同时报告；
- 现有安全状态机不因实验结果而自动放宽。

### P4 — 泛化与独立验证

**目的：** 防止只对一组数据或相邻帧过拟合。

任务：
1. 以 session/场景为单位划分开发集和锁定测试集，不随机拆散相邻帧。
2. 不在锁定测试集上调阈值、选模型或修改几何参数。
3. 至少覆盖室内/室外、不同纹理、遮挡、转向、地面坡度、路缘、行人和静态障碍物。
4. 报告每个 session 的结果及跨 session 波动，不把高度相关帧视作独立样本。
5. 对任何失败类型建立原始帧、标注、投影图和根因记录。

通过门槛：
- 独立 session 上结果方向一致；
- 所有失败和低质量输入均计入分母或单独报告；
- 不允许用开发集性能代替泛化结论。

### P5 — Last10m 入口确认专项评估（与 P1–P4 并行，但单独验收）

**目的：** 验证目标商店、入口关系、缺失视角和运动授权，不把回放演示当成实时识别。

任务：
1. 保留当前 3 场景、7 帧回放作为状态机回归测试。
2. 扩充带明确 target-store-to-door 真值的公开/可许可数据；若没有可用数据，再制作小规模人工标注集。
3. 对每帧标记 target visibility、target identity、door candidates、SAME_TARGET / NEIGHBOR、ground visibility、obstruction 和 need_view。
4. 比较固定观察、OCR 触发观察、多视角主动观察。
5. 单独统计 target-lock、入口关系准确率、错误入口率、目标缺失拒绝率、视角次数、障碍识别和运动授权。
6. 所有无关系标签帧标记 NOT_EVALUABLE，不计成功。
7. Replay、真实模型推理和人工补充证据使用不同 provenance。

通过门槛：
- 错误入口不得被确认；
- 目标缺失或关系不明确时必须拒绝/重新观察；
- 未验证入口和地面之前不得授权运动；
- 回放和实时结果分开报告。

### P6 — Android / 小米设备验证（最后阶段）

只有在 P0–P5 的 PC 门槛通过后才开始。

任务：
1. 审计设备 Camera2、相机内参、镜头固定策略、陀螺仪、加速度计、传感器时间戳和热降频。
2. 设计 Camera2 与 IMU 时间同步和相机-IMU 标定。
3. 将已在 PC 验证的 PoseProvider / DepthProvider 适配到 Android/NDK。
4. 与 PC 同一数据回放结果比较，记录实时延迟、帧率、温度、丢帧和内存。
5. 真机实验先在静态/受控环境和监督模式下完成；不得直接进入无监督真实行走。
6. 真正用户研究必须另行伦理、安全和风险评估。

通过门槛：
- 设备输入时间戳、标定和尺度经过验证；
- 跟踪丢失或几何质量不足时禁止输出可通行授权；
- 性能、温度和长时间运行稳定性达到预先定义的设备门槛；
- 完成独立安全审查前，不宣称可替代盲杖或作为认证助行设备。

## 5. 统一评估指标

### 定位
- ATE、RPE；
- 有效跟踪帧比例；
- 初始化失败率、丢跟踪次数、恢复时间；
- 轨迹尺度误差和每米漂移。

### 深度与几何
- 深度 AbsRel / RMSE（仅在 GT 有效范围内）；
- 近场/中场距离分桶误差；
- 障碍物高度误差；
- 地面法线角度误差；
- 三维点位置误差；
- 无效深度比例与置信度校准。

### 地图与安全
- 危险漏检（按 road / curb / ground / 3D occupancy 分开）；
- 确认安全路面误停；
- UNKNOWN / OBSERVE / BLOCKED / CLEAR 状态比例；
- guidance coverage；
- 地图重影、错误清除和障碍物持续性；
- 决策翻转与警告延迟；
- 所有指标必须同时给 numerator / denominator 和 session 分组。

### Last10m
- target-lock accuracy；
- entrance relation accuracy（只在有真值关系的帧上）；
- wrong-entrance rate；
- target-absent refusal rate；
- need-view 次数；
- ground-obstruction detection；
- motion authorization correctness。

## 6. 实验记录和可复现要求

每次实验必须生成独立目录，不覆盖历史结果，至少包含：

- `README.md`：目的、假设、结论和证据等级；
- `run_metadata.json`：代码 commit、程序版本、运行环境、命令、配置、随机种子；
- `dataset_manifest.csv`：文件相对路径、来源、版本、许可、SHA256；
- `calibration/`：相机内参、畸变、相机-IMU 外参和时间偏移；
- `logs/`：完整 stdout/stderr、失败信息；
- `trajectory/`：估计轨迹、参考轨迹和对齐结果；
- `metrics.csv`：指标、分子、分母、单位、session；
- `failures/`：失败帧原图、标注、投影和归因；
- `visualizations/`：视频和地图可视化；
- `decision.md`：继续、调整、暂停或放弃的理由。

不得把本机用户名、绝对个人目录、访问令牌、私有数据或不可再分发模型权重提交到公开仓库。数据文件原则上只保存 manifest、下载说明和校验值，不把大型原始数据集复制进 Git。

## 7. 反馈驱动的计划调整机制

每个阶段结束后必须开一次证据评审，并按以下模板更新本计划：

| 字段 | 必填内容 |
|---|---|
| 假设 | 本轮试图验证什么 |
| 实际执行 | 真实运行的命令、版本和输入 |
| 结果 | 数值、分母、session 分组和失败案例 |
| 证据等级 | DATASET / DESKTOP_REPLAY / PROTOTYPE / DEVICE_VERIFIED / USER_STUDY |
| 偏差 | 与预期不同在哪里 |
| 根因 | 已证实原因与待验证假设分开 |
| 决策 | GO / ADJUST / HOLD / STOP |
| 下一步 | 只列最小、可证伪的下一项实验 |

调整规则：
1. 若工具安装或数据格式失败，先解决环境/数据问题，不换算法。
2. 若 VIO 轨迹失败，先区分标定、时间同步、数据格式、初始化和算法问题。
3. 若轨迹合格但几何失败，单独检查深度尺度、相机内参、重力、地面法线和时间对齐。
4. 若几何合格但地图失败，才检查地图更新、可见自由空间和坐标契约。
5. 若危险漏检下降但误停/UNKNOWN 大幅上升，标记为安全/可用性回归，不得单方面判成功。
6. 只有新证据支持时才更改参数；参数变更必须在开发集完成，并在锁定测试集一次性评估。
7. 如果某条开源路线成本高、许可不适合或无法复现，应记录 STOP 理由并选择最小替代方案，不无限追加依赖。

## 8. 当前立即执行的队列

1. **P0.1：** 核对 SANPO 两个 session 的官方对象清单与本地文件差异，定位 IMU 发布路径。
2. **P0.2：** 建立独立 Linux/WSL 环境清单；检查 Basalt 构建依赖，不触碰现有工作树。
3. **P0.3：** 核实 TUM VI room1 官方文件、MD5、许可和 Basalt 配置。
4. **P1.1：** 在批准下载后，仅下载 TUM VI room1 单序列，运行 Basalt VIO。
5. **P1.2：** 运行 EuRoC V1_01_easy；下载前确认 ETH 当前官方数据条目、大小和许可。
6. **P1.3：** 评估 ADVIO-09 单组转换；遵守 CC BY-NC 4.0，不将其作为无商业限制的数据。
7. **P2：** 复用 DAV2 57 帧旧结果；核实 Metric3D 权重许可后再决定是否运行。
8. **P3：** 只有 P1/P2 通过后才做 Harvard/Mobilio 输入适配对照。
9. **P5：** 保持 Last10m 当前回放回归测试，另行补充带入口关系真值的数据。
10. **P6：** 手机与 Android 验证保持最后，不在本轮执行。

## 9. 当前明确不做的事情

- 不在当前有未提交改动的工作树内安装/构建 Basalt；
- 不下载完整 SANPO、TUM VI、EuRoC 或 ADVIO；
- 不下载未经许可核实的模型权重；
- 不重复运行已有 DAV2 57 帧实验；
- 不修改现有生产决策代码和安全授权语义；
- 不把 SANPO recorded pose 说成真实 VIO；
- 不把 Last10m replay 说成实时模型识别；
- 不把 Harvard/Mobilio 离线参考移植说成完整原始系统复现；
- 不修改或合并 PR #20；
- 不进行手机验证，直到 PC 阶段门槛通过。

## 9.1 P0 证据评审（2026-11-07）

| 字段 | 本阶段记录 |
|---|---|
| 假设 | SANPO 本地缺失的 IMU 可能只是选择性下载或路径判断错误；P0.3 应能确认 TUM-VI/Basalt 的最小可复现入口。 |
| 实际执行 | 通过公开 GCS JSON API 对三个 SANPO-Real session 前缀做递归分页清单；记录 chest/head `fixed_camera_poses.csv` 元数据；核对 TUM-VI 官方 512x512 Euroc/DSO 导出路径、CC BY 4.0、MD5 sidecar 机制和 Basalt `basalt_vio --dataset-type euroc` 命令契约；只读检查本机编译工具、容器入口和剩余磁盘。 |
| 结果 | 三个 session 对象数为 5,462、8,286、7,986；每个 session 的 IMU/gyro/accelerometer/sensor 文件名匹配数均为 0；每个 session 都有 chest/head corrected pose。TUM-VI 官方源、许可和 checksum 机制已记录，但具体 room1 文件 MD5 尚未取得。 |
| 证据等级 | DATASET / DESKTOP_ENVIRONMENT_AUDIT；没有 VIO 运行结果。 |
| 偏差 | 当前是 macOS ARM64，不是计划要求的独立 Linux/WSL；没有 `gcloud`，使用只读 GCS JSON API 替代；本地没有 `ninja`、Java runtime 或 Docker/Podman/Colima，剩余磁盘约 66 MB。 |
| 根因 | 目前能证实的是：三份指定 SANPO session 的公开对象清单中确实没有原始 IMU 观测对象。官方“SANPO-Real 包含 IMU”的总体说明与这三个对象前缀存在未解释差异，不能继续把差异归因于本地文件名扫描。 |
| 决策 | `P0.1=GO/完成`；`P0.2=HOLD`；`P0.3=HOLD/部分完成`；`P1=HOLD`。不运行 SANPO VIO，不下载 TUM-VI，不把 recorded pose 写成 IMU/VIO。 |
| 下一步 | 先取得独立 Linux/WSL 实验环境和 TUM-VI room1 单文件 MD5；满足后只下载一个 512x512 room1 序列，按 Basalt 官方命令做 P1.1。若环境或 checksum 仍不可得，记录 STOP 原因并不切换算法。 |

阶段文件：[`docs/phase_reports/p0_20261107/`](phase_reports/p0_20261107/)。

## 9.2 执行侧反馈评审（2026-11-07）

执行侧反馈正确指出：A 条件也只有 29/481 授权方向，D 条件的 49/481
覆盖不能归因于 VIO 或 DAV2 单一瓶颈；`any_miss` 不能当作危险感知召回。
但反馈中的 Harvard A/D `23/481`、`80/481` 是 surface-layer candidate
数量，不是当前修正回放的最终授权方向；当前冻结 CSV 的最终方向为 8/481、
71/481，最终 UNKNOWN 为 168/481、347/481。两套分母必须分开。

下一轮不增加新模型、不改 VIO、不放宽安全阈值。先做同一批 481 帧的
E1 layer-certificate replay，固定输出 Surface、Occupancy、Fusion、最终命令
和 GT danger 的交叉矩阵；再只选择一个证据层的诊断性改动。E2 优先检查
Occupancy 的部分观测射线如何作为 `observation_hint` 暴露，保留 CLEAR 证书、
STOP 和 `authorized_motion` 语义不变；E3 只有在 E2 无法解释主要瓶颈时才检查
Surface `no_candidate_ray`。两项都必须在独立验证后才允许进入下一阶段。

E2 已在冻结 production D CSV 上执行为诊断版：暴露 37 个部分观测 Occupancy
hint，但授权方向仍为 49/481，危险漏放仍为 17/295，确认安全路面误停仍为
0/150。因此 E2 是“安全不回退、可用性未改善”的结果；在独立复核前不进入
E3，也不把 hint 当作导航能力提升。E2 已由独立 Agent 逐行复核；E3 当前
`NOT_EVALUABLE`，因为冻结 CSV 没有 Surface 射线截断位置和候选角度，不能
后处理制造 Surface hint。需要先扩展离线诊断字段并重新生成 481 行，再交给
独立验证。当前 evaluator 已加入终止距离、最后可走距离和网格单元坐标的
诊断输出，并通过单元级射线测试；完整 481 帧 E3 重放仍待具备 SANPO/模型
输入后执行。

详细反馈与当前 CSV 的逐层矩阵见
[`docs/phase_reports/mobilio_feedback_20261107/`](phase_reports/mobilio_feedback_20261107/)。

## 10. 相关现有文档

- [项目现状与路线图](PROJECT_REVIEW_AND_ROADMAP_20261107.md)
- [Last10m 评估协议](LAST10M_EVALUATION_PROTOCOL_20261107.md)
- [Last10m Demo 报告](LAST10M_DEMO_REPORT_20261107.md)
- [Harvard / Mobilio 实现差异](MOBILIO_REFERENCE_DIFF_20261106.md)
- [FillGrid 修复回放](MOBILIO_FILLGRID_REPLAY_20261107.md)
- [集中审核材料索引](assets/review_20261107/README.md)

---

**本计划的成功标准不是完成所有阶段，而是每个阶段都能产生可复现、可证伪的结论，并据此决定下一步是否值得投入。**


---

# 11. 数据集与技术基准完整目录（PC-first）

本节把项目中提到过的数据集、算法和测试基准统一登记。目录是候选清单，不表示数据已下载、许可已批准、模型已运行或结果已复现。每次真正使用前，必须保存具体版本、下载页面、数据许可、校验和及适用限制。

## 11.1 定位 / VIO / VO 基准

| 数据集 | 可用模态与真值 | 对 BlindNav 的用途 | 局限与使用规则 | 优先级 |
|---|---|---|---|---|
| TUM VI | 双目灰度图、IMU；约 20 Hz 图像、200 Hz IMU；部分序列起止附近有 mocap pose | 手持/头戴视觉惯性基准；先用 room1 512×512 EuRoC 格式做 Basalt | 并非所有长序列都有全程 mocap 真值；只在有效 GT 区间算绝对轨迹误差。数据许可页面需随下载版本归档（当前官方信息标 CC BY 4.0） | P1 首选 |
| EuRoC MAV | 双目图像、IMU、相机/IMU 标定、Vicon 或 Leica 参考轨迹 | 标准 VIO 构建与回归基准；V1_01_easy 优先 | 无人机运动和视角与行人手机不同；适合验证算法管线，不代表手机步行泛化。下载前核对 ETH 新下载入口、单序列大小和数据条款 | P1 |
| ADVIO | 真实手机视频、图像时间戳、加速度计、陀螺仪、参考轨迹/标定资料 | 最接近普通手机采集的 VIO/VO 数据；先 advio-09，必要时再 advio-20 | 数据格式不是 Basalt 原生 EuRoC；必须验证时间同步、相机内参和 IMU 轴向。CC BY-NC 4.0，限许可允许的非商业研究，不作为商业产品训练/发布的默认数据 | P2 |
| KITTI Odometry / Raw | 车载单目/双目图像；odometry 有 22 序列，其中 00–10 有 GT、11–21 用于测试；Raw 还含 GPS/IMU、Velodyne 和标定 | 视觉里程计算法外部 sanity check；可验证 VO 输出格式和轨迹评估 | 车载道路域与胸前/手持手机差异大；odometry benchmark 不是同步手机 IMU VIO 数据。灰度包约 22 GB、彩色约 65 GB，首轮不下载 | P3 |
| TartanAir V1/V2 | 合成 RGB、深度、分割、相机 pose、IMU、LiDAR、光流等；V2 支持多相机模型 | 可控环境下验证 VIO/SLAM、深度、地图和动态环境压力测试 | 合成域；不得把合成表现当真实街道效果。V2 数据 CC BY 4.0，工具包 MIT；按单环境/轨迹下载，不整库下载 | P2 对照 |
| TUM-VIE | 手持/头戴高分辨率立体、事件相机、IMU、部分 mocap | 若后续研究快速运动/事件视觉再使用 | 比当前需求复杂，先不引入 | P4 |
| SANPO-Real | 胸前/眼平视角视频、可选双目、深度/分割/位姿及官方说明中的 IMU | BlindNav 目标视角和户外可行走区域研究；已有 481 帧继续做离线归因 | 当前本机已审计 session 未发现原始 IMU 对象；不得把官方总体说明推断为本地文件已齐全。继续查对象清单与下载路径 | P0 数据审计 |
| SANPO-Synthetic | 合成 egocentric 场景、视频与多种标注 | 补充障碍物、地面、天气/场景变化和分割测试 | 合成数据域差；与真实数据分开报告 | P2 |
| BDD100K video | 100K 行车视频；GPS/IMU 信息另有约 3.9 GB 包；图像和语义任务标注 | 可作为视频/传感器数据格式和动态场景辅助研究 | 车载域；完整视频约 1.8 TB，禁止首轮下载。数据许可不是代码仓库 BSD 许可证；商业使用受 BDD/BAIR 成员资格及单独许可约束 | P4 |

官方入口：
- [TUM VI](https://cvg.cit.tum.de/data/datasets/visual-inertial-dataset)
- [EuRoC MAV](https://projects.asl.ethz.ch/datasets/euroc-mav/)
- [ADVIO GitHub](https://github.com/AaltoVision/ADVIO) / [Zenodo v3](https://zenodo.org/records/1476931)
- [KITTI Odometry](https://www.cvlibs.net/datasets/kitti/eval_odometry.php) / [KITTI Raw](https://www.cvlibs.net/datasets/kitti/raw_data.php)
- [TartanAir](https://tartanair.org/)
- [TUM-VIE](https://cvg.cit.tum.de/data/datasets/tumvie)
- [SANPO](https://google-research-datasets.github.io/sanpo_dataset/)
- [BDD100K](https://bdd-data.berkeley.edu/)

## 11.2 单目深度 / 公制深度 / 几何基准

| 数据集 | 主要数据与 GT | 适合验证什么 | 不适合什么 / 注意事项 | 优先级 |
|---|---|---|---|---|
| NYU Depth V2 | 室内 RGB-D（Kinect）；常用 Eigen test 654 张 | 室内近距离深度、平面、家具/门口等几何 | 不含 VIO 所需同步 IMU/长序列 pose；不能代表户外；使用官方 split，不混入训练 | P2 |
| KITTI Depth / Eigen | 车载 RGB + 稀疏 LiDAR 深度 | 户外尺度、道路深度与远距离误差 | 车载域；LiDAR 投影稀疏，不能把无返回像素当作 0 深度；与 KITTI VO 的序列/协议要区分 | P3 |
| DIODE | 室内+室外 RGB、激光扫描深度、有效掩码、表面法线 | 高质量深度、地面/平面法线、跨室内外泛化 | 全量归档可能约 84 GB；首轮只取评估子集，核对原始许可和官方 split | P2 |
| ETH3D | 室内/室外高分辨率图像、激光扫描/高精度深度；常见深度评估子集约 423–454 张（按协议版本） | 精细几何、边界、平面、室内外尺度 | 数据许可为 CC BY-NC-SA 4.0；不能默认用于商业训练或重新分发 | P2 |
| iBims-1 | 100 张室内 RGB 与高精度扫描深度 | 深度边界、薄物体、平面和几何结构质量 | 小型定向 benchmark，不代表场景分布；下载前确认数据条款 | P2 |
| Hypersim | 合成室内 RGB、深度、法线、语义与相机参数 | 室内几何受控实验、法线和深度算法调试 | 合成数据，不作为真实场景最终验收；核实数据许可与使用限制 | P3 |
| Virtual KITTI 2 | 合成驾驶 RGB、稠密深度、内外参、多天气/光照变体 | 户外深度模型压力测试、天气变化和像素深度映射 | 驾驶场景域差；官方数据许可 CC BY-NC-SA 3.0；全量 RGB+depth 约 15 GB，不首轮下载 | P3 |
| TartanAir | 合成 RGB、深度、pose、IMU、分割、光流、LiDAR | 多视角几何、深度与时序融合的可控实验 | 合成域；与真实数据分别报告 | P2 |
| ScanNet | 室内 RGB-D 视频、相机 pose、重建表面、实例语义；约 2.5M views / 1500+ scans | 室内局部地图、点云、表面和语义地图验证 | 需接受 ScanNet Terms of Use / 申请访问；不是手机 VIO 基准，不公开传播原始数据 | P3 |
| TUM RGB-D | RGB-D 序列、时间戳、标定和 mocap/机器人轨迹 | 地图、RGB-D SLAM、坐标变换单元测试 | 不含手机 IMU 的标准 VIO 替代；使用官方 benchmark split | P3 |
| SUN RGB-D | 多传感器室内 RGB-D、3D 框与语义 | 室内障碍物类别、3D 框和场景理解 | 不等于连续手机步行数据；核对数据许可 | P4 |
| Make3D | 室外单目 RGB 与深度参考 | 远距离户外深度的分布外测试 | 数据规模较小、采集设备/域与手机不同 | P4 |

官方入口：
- [NYU Depth V2](https://cs.nyu.edu/~fergus/datasets/nyu_depth_v2.html)
- [KITTI](https://www.cvlibs.net/datasets/kitti/)
- [DIODE](https://diode-dataset.org/)
- [ETH3D](https://www.eth3d.net/)
- [iBims-1](https://www.albert-janesch.de/ibims/)
- [Hypersim](https://github.com/apple/ml-hypersim)
- [Virtual KITTI 2](https://europe.naverlabs.com/proxy-virtual-worlds-vkitti-2/)
- [TartanAir](https://tartanair.org/)
- [ScanNet](https://www.scan-net.org/ScanNet/)
- [TUM RGB-D](https://cvg.cit.tum.de/data/datasets/rgbd-dataset)

### 深度评估协议必须统一

对每个模型同时报告两套结果：
1. **Metric / no-scale-alignment**：保留模型原始尺度，评估能否直接用于以米为单位的安全几何。
2. **Scale-aligned**：允许按 benchmark 规定进行尺度对齐，仅用于分析相对结构能力，不能代替公制尺度验收。

至少报告 AbsRel、RMSE、log-RMSE、δ1，以及有效深度像素覆盖率；按距离段（近场、2–6 m、中远场）和区域（地面、障碍物、边界）分别统计。深度模型输出的单位/尺度、焦距条件、裁剪、resize、无效值和相机内参必须写入 manifest。

## 11.3 语义分割 / 道路 / 无障碍设施数据

| 数据集 | 标签与场景 | BlindNav 用途 | 限制 |
|---|---|---|---|
| NavAble | 盲人/低视力导航相关设施类别、真实与合成图片/3D 资产 | 门按钮、扶手、电梯、过街按钮等无障碍设施识别 | 不直接提供完整手机 VIO；不能代替障碍物公制深度。数据版本、论文统计口径和使用许可逐项核实 |
| SANPO | egocentric 户外场景、可行走/障碍相关语义和时序视频 | 最贴近 BlindNav 视角的 segmentation / path / obstacle 验证 | 优先复用已有 split；检查 IMU 文件缺口 |
| Mapillary Vistas | 全球街景、细粒度道路/人行道/路缘/设施语义类别 | 补充城市街道语义类别和跨城市外观 | 研究版需申请/接受相应条款；数据和图片使用限制独立于代码许可。V2 镜像显示 CC BY-NC-SA 4.0，必须以官方实际下载协议为准 |
| BDD100K | 驾驶视频、道路对象、drivable area、lane 等标注 | 车辆/行人/道路语义和天气变化辅助对照 | 车载视角；数据不是 BSD-3-Clause 代码许可证。商业使用有额外限制 |
| Cityscapes | 欧洲城市道路、fine/coarse semantic labels、instance labels | 道路、人行道、路缘、行人、车辆语义分割基准 | 需注册并遵守非商业研究条款；禁止把原始数据或可重建数据的衍生物公开分发 |
| Mapillary 开放贡献图像 | 街景图片，用户贡献内容 | 只用于在符合 CC BY-SA 和归属要求的前提下做外观研究 | 不把开放图像许可与 Vistas Research Edition 许可混为一谈 |

官方入口：
- [NavAble 数据集](https://huggingface.co/datasets/NavAble/NeurIPS_2026_BLV)
- [SANPO](https://google-research-datasets.github.io/sanpo_dataset/)
- [Mapillary Vistas](https://www.mapillary.com/dataset/vistas/)
- [BDD100K](https://bdd-data.berkeley.edu/)
- [Cityscapes](https://www.cityscapes-dataset.com/license/)
- [Mapillary 开放数据许可说明](https://help.mapillary.com/hc/en-us/articles/115001770409-CC-BY-SA-license-for-open-data)

### 语义数据的适用边界

Mapillary Vistas、BDD100K、Cityscapes 是道路/街景语义数据，不是胸前视角 VIO 数据，也不提供可直接用于障碍物三维定位的同步深度和 IMU。它们只能作为分割预训练/类别补充或外观泛化测试。必须做标签映射，不能把 road、sidewalk、curb、terrain、obstacle 的不同定义直接合并。

## 11.4 SLAM / 地图 / 三维重建软件基准

| 工具 / 项目 | 适合验证 | 不应误认为 |
|---|---|---|
| Basalt | 第一 PC VIO 基线；EuRoC/TUM VI；可比较 VO/VIO 能力 | 已经在 BlindNav 或 Android 复现 |
| OpenVINS | 独立 VIO 估计器、协方差、IMU 标定对照 | 现成 Android SDK |
| VINS-Fusion | 单目+IMU/双目+IMU 研究对照 | 无需数据适配的 Basalt 替代 |
| ORB-SLAM3 | 单目/双目/视觉惯性 SLAM、重定位和跟踪恢复 | 密集障碍物占据地图 |
| RTAB-Map | RGB-D/stereo SLAM、回环和地图融合对照 | 普通手机无 ARCore 的即插即用方案 |
| evo | 轨迹对齐、ATE、RPE 和结果可视化 | VIO 算法本身 |
| Open3D | 点云、网格、ICP、几何误差和可视化 | 安全决策或实时手机定位系统 |
| COLMAP | 离线 SfM/MVS 和相机几何 sanity check | 实时 VIO 或动态障碍物地图 |

官方入口：
- [Basalt](https://github.com/VladyslavUsenko/basalt)
- [OpenVINS](https://github.com/rpng/open_vins)
- [VINS-Fusion](https://github.com/HKUST-Aerial-Robotics/VINS-Fusion)
- [ORB-SLAM3](https://github.com/UZ-SLAMLab/ORB_SLAM3)
- [RTAB-Map](https://github.com/introlab/rtabmap)
- [evo](https://github.com/MichaelGrupp/evo)
- [Open3D](https://www.open3d.org/)
- [COLMAP](https://github.com/colmap/colmap)

许可证原则：每个代码仓库、第三方依赖、模型权重和数据集分别审查。Basalt 代码 BSD-3-Clause；OpenVINS、VINS-Fusion、ORB-SLAM3 的仓库采用 GPL 系列许可；RTAB-Map/Open3D/COLMAP 的具体组件和依赖仍须按发布版本核实。不得用代码许可证推断数据或权重许可证。

## 11.5 Last10m 入口关系专项数据

当前公开数据集中，很多资源只标记 door / entrance / building 类别，并没有“用户目标商店—正确入口门”关系真值。因此，不能把一般门检测数据集包装成入口识别 benchmark。

候选来源：
- [LSVT / Large-Scale Street View Text](https://github.com/ku21fan/STR-Fewer-Labels)：街景文字识别和定位辅助；不等于店铺入口关系真值。
- [Mapillary Vistas](https://www.mapillary.com/dataset/vistas/)：街景语义类别和道路上下文；不保证门与商店关系标注。
- [Open Images](https://storage.googleapis.com/openimages/web/index.html)：通用对象类别；不提供完整目标商店—入口关系。
- 当前 Last10m 三个场景、七帧 REPLAY：继续作为状态机回归用例。

如果以上公开资源都没有所需关系标签，才制作最小人工标注集。每个 case 必须包含目标商店、候选门、正确入口 ID、SAME_TARGET / NEIGHBOR、目标可见性、门前地面和障碍物、视角需求；标注者分歧需保留，不能只记录模型输出。

## 11.6 最小下载清单与顺序

| 顺序 | 资源 | 最小选择 | 目的 | 下载前条件 |
|---|---|---|---|---|
| 0 | 现有本机 SANPO 481 帧 / DAV2 57 帧结果 | 不下载 | 复用已有证据 | 建立 manifest 和哈希 |
| 1 | TUM VI | room1 512×512 单序列 | Basalt 手持 VIO 桌面基线 | 官方 URL、MD5、数据条款核实 |
| 2 | EuRoC | V1_01_easy 单序列 | 标准双目 VIO 回归 | ETH 当前单序列下载和许可核实 |
| 3 | ADVIO | advio-09 单组 | 真实手机格式适配 | CC BY-NC 4.0；确认非商业研究范围 |
| 4 | 深度基准 | NYU Eigen test 小子集 + iBims-1 + ETH3D 官方评估子集 | 室内近场、边界、室内外几何 | 官方 split、许可、校验值 |
| 5 | 深度/几何补充 | DIODE 小评估子集；必要时 TartanAir 单轨迹 | 室内外深度与可控时序几何 | 不下载 DIODE 全量（可能约 84 GB） |
| 6 | 语义补充 | NavAble 相关类别子集；SANPO 现有 split；必要时 Mapillary/BDD/Cityscapes | 无障碍设施与道路类别 | 数据条款和商业使用限制核实 |
| 7 | 地图基准 | ScanNet 单 scene 或 TUM RGB-D 短序列 | 离线地图/点云几何对照 | ScanNet 使用协议或 TUM 条款核实 |

## 11.7 哪些数据绝不能互相替代

- **单张深度图数据集不能验证 VIO。** NYU、DIODE、ETH3D、iBims-1、Hypersim 的深度评估样本通常不提供手机同步 IMU 和连续 pose。
- **车载数据不能代表胸前手机视角。** KITTI、BDD100K、Cityscapes、Virtual KITTI 2 对道路语义/户外深度有帮助，但视角、运动、目标尺度和遮挡分布不同。
- **合成数据不能单独证明真实泛化。** Hypersim、Virtual KITTI 2、TartanAir 必须与真实数据分开报告。
- **稀疏 LiDAR 深度不能当稠密 GT。** 只在有效返回区域计算深度误差，并单独报告覆盖率。
- **记录位姿不等于 IMU。** SANPO corrected pose CSV 可用于离线轨迹参考或输入代理，不能替代原始 gyro/accelerometer 测量。
- **语义类别不等于可通行性真值。** sidewalk、road、curb、terrain、obstacle 的标签语义和 BlindNav 风险策略需要显式映射与人工核验。
- **门框检测不等于入口关系。** Last10m 必须有 target-store-to-door 关系标注。
- **模型的名义米制输出不等于可靠公制测量。** 必须在独立深度 GT 和正确相机内参下验证原始尺度；scale-aligned 指标单独报告。

## 11.8 统一基准执行矩阵

第一轮只执行粗体项目，其他项目在出现明确缺口后再启用：

| 能力 | 主基准 | 第二基准 | 主要指标 |
|---|---|---|---|
| VIO | **TUM VI room1 + Basalt** | EuRoC V1_01_easy | ATE、RPE、跟踪率、初始化/恢复 |
| 手机 VIO 域适应 | **ADVIO-09** | ADVIO-20 | 时间同步、尺度、ATE/RPE、格式转换有效率 |
| 单目深度 | **现有 DAV2 57 帧结果** | NYU / iBims-1 / ETH3D | AbsRel、RMSE、δ1、有效像素率 |
| 公制深度 | **有 GT 的 ETH3D / DIODE 子集** | KITTI depth | no-scale-alignment RMSE/AbsRel |
| 地面/表面法线 | **DIODE / Hypersim 子集** | iBims-1 | 法线角度误差、平面拟合残差 |
| egocentric 可行走区域 | **SANPO** | SANPO-Synthetic | IoU、boundary F1、危险区域漏分 |
| 无障碍设施 | **NavAble** | Mapillary Vistas | per-class AP/IoU、跨域表现 |
| 城市道路语义 | Mapillary Vistas | Cityscapes / BDD100K | 类别映射后的 mIoU；明确域差 |
| 三维地图 | TUM RGB-D / ScanNet 单 scene | TartanAir 单轨迹 | 点云对齐误差、重影、地图一致性 |
| Last10m | 当前 REPLAY 回归集 | 新增带关系 GT 的独立 case | 入口关系准确率、错误入口率、拒绝率 |

## 11.9 本目录的反馈更新规则

每个数据集/技术基准使用以下状态之一：
- `DISCOVERED`：已找到官方来源；
- `TERMS_CHECKED`：许可和访问条款已核对；
- `DOWNLOADED`：已下载并校验；
- `FORMAT_VALIDATED`：数据格式和标定通过；
- `RUN_COMPLETED`：算法真实运行完成；
- `REPRODUCED`：指标可重复复现；
- `REJECTED`：因许可、格式、域差或成本原因停止。

目前除 BlindNav 原有报告明确记载的实验外，新列出的资源均不得标记为 DOWNLOADED / RUN_COMPLETED / REPRODUCED。每次状态变化都需附上证据路径、日期、版本、SHA256 和运行日志。



## 12. 状态增补：E3 全量冻结回放已完成，G2 仍 HOLD（2026-11-07 10:02 UTC）

本节取代上文截至较早审查时“E3 完整 481 帧未运行”的旧状态；**不改变 P0–P6、Last10m 或任何安全门禁**。

- E3 执行代码快照 `6813160c555e297b03a599eb689b8083fe4ce36a`，已提交真实 PC 离线 481 帧 × 2 臂的 OFF/ON 回放、独立核对文件、gzip 原始派生表、射线日志、历史候选版本回放和分组表；材料位于 [E3 回放目录](phase_reports/e3_surface_6813160/)。
- 独立读取未压缩 CSV，复算 confirmed_clear 150、danger 295、other 36；最终 CLEAR 49、OBSERVE 157、UNKNOWN 275；`surface:no_candidate_ray` 125 帧中 danger 108，confirmed_clear 7；**confirmed_clear 的 77 帧 `surface:non_walkable` 是当前更大的可用性失败簇**。danger 的 17/295 被放行仍是安全代理风险，不得降低阈值来追求覆盖。
- E3 **REPLAY_COMPLETED / PARTIAL_INDEPENDENT_CHECK / G2 HOLD**；不等于导航提升、实际 VIO、真机安全或 G2 GO。叠加到原图、GT、深度、BEV/网格和射线的因果证据尚缺。
- 项目指导决定当前唯一任务为 [E3 因果叠加执行提示词](phase_prompts/E3_SURFACE_CAUSAL_OVERLAY_20261107.md)，审查依据为 [E3 独立技术复核](reviews/E3_FROZEN_REPLAY_INDEPENDENT_REVIEW_20261107_1002Z.md)。不重复运行无价值的 smoke/全量 OFF-ON；冻结授权语义不变。
- 固定权重评分已校正：同分项上次加权应为 **3.6/10**，本轮 **3.7/10**，仅复现性 +1 分项；原主观总览的 **4.0/10** 与固定权重旧分项未数学对齐，不能当成可比基线。P0–P6 目前完整通过仍为 **0/7**，P0/P2 有部分研究证据，P1 Basalt/TUM VI 尚未运行，P6 手机继续 HOLD。
