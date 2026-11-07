# 下一阶段唯一执行提示词：E3 Surface 因果叠加（不调安全阈值）

> **项目指导决定：只执行本任务，不并行引入新模型，不重复三轮 smoke。** 目的：从 E3 481 帧的实际失败证据中定位一个可证伪的几何/语义/网格根因。

## 交给执行者的任务

请在仓库 `haomingweng3-eng/blindnav-superagent` 分支 `agent/corridor-obstacle-split-ad0888814215` 上，基于 E3 冻结代码和现有本机数据，完成 **12 个预先指定、类别分层的因果诊断案例**。本轮只读审查生产决策；不修改 `SafetyCoordinator`、融合、授权、安全阈值和 Android 代码，不启动 Android/手机验证。

### 一、冻结事实和输入

- 现有回放目录：`docs/phase_reports/e3_surface_6813160/`。审查时报告提交 `e1e7615d8a1cead460d0774e1a6d5dc597a8d13a`。
- 诊断原始代码 commit：`6813160c555e297b03a599eb689b8083fe4ce36a`。
- 输入 manifest：`docs/phase_reports/e3_input_audit_20261107/input_inventory.json`；SHA256 `2b4997b6c3e561e62a046fb812edbd31de2ad231596da0ab9219755f2a20e37c`。
- 核对当前本机输入仍对应冻结 manifest，旧数据/权重的哈希不能用“路径相同”替代。
- 先读取 `on_per_frame.csv.gz`、`rays.jsonl.gz` 和 `per_group.csv`，不要重新跑 OFF/ON 全套，只为当前新问题做最小必要运行。

### 二、预先选择 12 个代表帧，保存精确键

从 ON `production/mobilio` 逐帧表按 `(session,frame)` 去重、稳定排序。选择：

1. **6 帧 confirmed_clear + 最终 `reason=surface:non_walkable`**：三个 session 各 2 帧，选该组最早和中位（有多个中位取靠前）帧；如与其他组重叠，选择下一不重叠帧。
2. **4 帧 danger + 最终 `safety_state=CLEAR`**：按三个 session 分配 1 / 2 / 1，按帧号升序、从最早帧选取；如某 session 不足，注明缺口并从其他有资格 session 补齐。
3. **2 帧 confirmed_clear + `reason=surface:no_candidate_ray`**：第一和第三 session 各 1，选最早合格帧。

三组必须互斥，输出 `selected_keys.csv` 和分组数量、真实条件名、原始行哈希/源文件哈希。没有足够样本不得静默替换类别；应明确 HOLD 和实际可用数量。

**理由：** 当前 125 个 no_candidate 中 108 个是 danger 代理帧；confirmed_clear 的 121 个未 CLEAR 中 77 个是 surface:non_walkable，另有 17 个 danger CLEAR。优先找模型/几何/网格的反向失败，而不是只优化“没候选”。

### 三、产出原图—模型—几何—网格—射线证据链

对每个目标帧，输出一页可审查的静态诊断图，以及结构化 JSON/CSV。每帧至少对齐以下内容：

1. SANPO 原始 RGB 与官方 GT segmentation/代理标签（GT **仅用于审查，不得进入生产判定**）。
2. 当前 v5 预测类别 mask（含 class-id 与 walkable/non-walkable 映射；相机输入和图片尺寸对应）。
3. DAV2 原始输出、地面标定结果、实际公制深度和有效掩码；相机内参/姿态/高度约定；如公制深度不可用如实标注。
4. `depth_to_level_points` 投影后的点云或 BEV、相机/世界坐标、Surface/Occupancy 网格状态；区分本帧观测与历史累计证据，不得将旧地图作为当前 GT。
5. 原始候选方向、实际选中/被拒射线、DDA 穿过的网格单元，入口/出口距离、连续 WALKABLE 长度、UNKNOWN gap、NON_WALKABLE/HAZARD 终止单元。对近零长度角点穿格另行标记。
6. 最终 Surface、Occupancy、融合状态、`effective_command`、`authorized_motion`、GT 标签，解释该帧失败源自哪一层。
7. 必须附 `overlay_manifest.json`（代码/输入/模型哈希、选帧键、生成脚本、坐标约定、必要中间量的哈希与解释）。

需要的历史网格状态如果没有被现有输出保存，允许在**同一冻结代码、同一冻结配置**下做**一次最小顺序诊断回放**，在目标帧只读导出网格快照/图像；不得修改生产决策路径。导出时优先使用现有 `mobilio_viz.py` / `SurfaceGrid2D.snapshot` / `OccupancyGrid3D.snapshot`；必要时在单独诊断脚本添加只读 hook。不重复 481 帧 OFF/ON 成对回放，不运行无假设阈值扫描。

### 四、根因判定与安全对照

将每个案例按以下失败层标注，附可核查证据，不强行归因：`V5_SEMANTIC`、`DAV2_SCALE_OR_CALIBRATION`、`CAMERA_POSE_OR_COORDINATES`、`GRID_UPDATE_OR_STALE_EVIDENCE`、`DDA_RAY_SELECTION`、`OCCUPANCY_FUSION`、`GT_PROXY_AMBIGUOUS`、`UNKNOWN_CAUSE`。

请明确展示这两个相反风险：
- confirmed_clear 被 `surface:non_walkable` 拒绝，是可用性瓶颈候选；
- danger 却进入 CLEAR，是危险代理漏放，需要独立解释，不能靠降低防护阈值改善。

样本选自失败类别，**不能**从 12 个样本推断总体根因比例。若发现两个原因需要对照实验，下一阶段只挑其中一个可证伪实验，不自动改算法。

### 五、技术核验与性能预算

- 保留全部 `SafetyCoordinator`、`fuse`、STOP、UNKNOWN、`authorized_motion` 语义；诊断输出不得注入决策。
- 如果为了只读导出需要增加代码，仅在离线工具/诊断记录处修改，并对 12 帧运行一次定向契约/决策等价检查。若改动触及生产算法，则 HOLD 交回差异，不继续。
- 注意 SegFormer 日志中的 19→5 classifier mismatch：必须结合后续 v5 checkpoint `load_state_dict` 成功与 hash 核验，不能仅凭加载警告声称“分割模型随机”。
- 只使用已获授权、现有本地数据/模型。**不要下载大型数据、模型、安装新依赖或删除原始用户文件。** 如果发现必需的缓存/数据缺项，仅做一次定向诊断并 HOLD；不要反复 smoke。
- 若需要公开图片，先按 SANPO 官方 Creative Commons V4.0 条款核对署名、隐私及可再分发范围，必要时只提交脱敏派生叠加图。

### 六、交付与门禁

在新的 `docs/phase_reports/e3_surface_causal_overlay_20261107/` 中交付：
- `README.md`：状态、具体结论、12 个样本为何被选、不能证明什么；
- `selected_keys.csv`、`case_findings.csv`、`overlay_manifest.json`；
- 12 个有一致标尺/图例的逐帧叠加图（注意不得暴露私人路径/令牌）；
- 只读渲染/抽样脚本，复现命令、耗时和环境信息；
- 一份独立可复查的分组统计/静态图检查；
- 对项目指导者的一条下一步建议：**唯一最值得验证的根因、最小有 GT 的对照、预期改善指标、失败即停止条件**。

验收以“能在 12 帧中明确区分至少一个失败层，并为更广泛 481 帧提出可测假设”为目标；不能根据展示图漂亮就宣告根因已解决，不能因为 UNKNOWN/hint 数字变好就宣告导航能力提升。**当前 G2 HOLD，下一轮由项目指导者看证据决定 ADJUST/GO。**
