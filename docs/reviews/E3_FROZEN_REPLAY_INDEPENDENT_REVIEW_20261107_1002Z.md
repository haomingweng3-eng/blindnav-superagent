# BlindNav E3 冻结回放：独立技术复核与阶段决策（2026-11-07 10:02 UTC）

> **独立审查结论：ADJUST / G2 HOLD。** 本轮新增了完整的 PC 离线 E3 回放与派生证据，属于真实技术审查材料，值得更新记录；**并未证明导航能力改善或安全门禁通过**。
>
> 被审查分支：`agent/corridor-obstacle-split-ad0888814215`；审查时 HEAD `e1e7615d8a1cead460d0774e1a6d5dc597a8d13a`。上次项目状态审查基线 `d8fe2d52f25547f197cfc33832280462f1f8812a`（本分支较其前进 11 个提交）。本轮的实验代码快照为 `6813160c555e297b03a599eb689b8083fe4ce36a`。

## 1. 新材料和来源

- `5e9fad7`：E3 原始输入清单；481 帧、1,449 个必需文件，未发现缺项；manifest SHA256 `2b4997b6c3e561e62a046fb812edbd31de2ad231596da0ab9219755f2a20e37c`。
- `6813160`：PC 执行器新增射线诊断、模型缓存冻结、模型/代码状态核验与相关测试；修改了 `mobilio_surface.py`、`mobilio_sanpo_ab.py`、`depth.py`、`corridor_truth_eval.py` 和测试。它不是导航安全规则放宽的授权。
- `6e97150`：OFF/ON 完整回放及独立结果（各 481 个 frame × 2 arms = 962 行），17 个已核对的决策、授权、时间与元数据字段无差异。
- `e1e7615`：互斥分组 CSV、射线示例 SVG、gzip 逐帧和射线记录、历史候选版回放、环境日志与文件清单、独立分组核查。

参考路径：`docs/phase_reports/e3_surface_6813160/`；原始实验数据不覆盖、不重写。

## 2. 本轮可独立复算的数字（直接读取当前仓库两份未压缩 CSV）

从 `per_group.csv` 的 66 个互斥组复算：

| 指标 | 复算结果 | 解释 |
|---|---:|---|
| 会话帧数 | 121 + 184 + 176 = **481** | 三个 SANPO session，`production/mobilio` |
| GT 代理标签 | danger **295**；confirmed_clear **150**；other **36** | 295 + 150 + 36 = 481，标签不重叠 |
| Mobilio 最终状态 | CLEAR **49**；OBSERVE **157**；UNKNOWN **275** | 49 + 157 + 275 = 481 |
| E3 trace 状态 | RECORDED **398**；NO_OBSERVATION **74**；NO_METRIC_DEPTH **9** | 398 + 74 + 9 = 481 |
| `surface:no_candidate_ray` | **125/481** = 25.99% | 不是 125 个被证实的误拒绝 |
| 在 no_candidate 内 | danger **108**；confirmed_clear **7**；other **10** | **108/125 = 86.4%** 属于危险代理标签 |
| no_candidate 扫描观测 | NO_OBSERVATION **74**；RECORDED **51** | 74/125 = 59.2% |
| no_candidate 被拒射线分组 | 无正长度被拒射线 **102**；NON_WALKABLE **16**；UNKNOWN_GAP **7** | 102 + 16 + 7 = 125；来自 `no_candidate_buckets.csv` 13 组 |
| confirmed_clear 的最终结果 | CLEAR **29**；OBSERVE **80**；UNKNOWN **41** | 121/150 未获得 CLEAR |
| confirmed_clear 最终原因 | **77/150** `surface:non_walkable`；**7/150** `surface:no_candidate_ray` | **77/121 = 63.6%** 的未 CLEAR 样本落在前一原因；目前是现象，不是根因 |
| danger 的最终结果 | CLEAR **17**；OBSERVE **73**；UNKNOWN **205** | 危险代理帧中最终 CLEAR **17/295 = 5.76%**，不能宣称安全通过 |

`independent_results.json` 中 baseline 的 `any_miss=123/295`（41.69%）、Mobilio `any_miss=17/295`（5.76%）、`true_false_stop=0/150` 等数字与 E3 报告相符；但本轮**不能通过 GitHub 文本连接器解压直接检查**提交的 `.csv.gz` / `.jsonl.gz` 二进制文件，因此不能将这些原始逐帧比较冒充本审查者全量独立重算。本轮实际独立复算的是上面的可读分组 CSV。原独立程序声称已经对 gzip 原始产物核验，仍需按 G2 门禁逐项检查。

17 项 OFF/ON 无差异来自 `independent_results.json` 与独立脚本；历史旧版 `2ee1f58` 与旧 CSV 的 11 项字段一致，而当前 OFF 相比旧版只有 **4 帧 guidance** 改动，均不等于运动授权。旧版 checkpoint 快照未锁，原历史 CSV 的生成 commit 未记载，故只能视为**有限度的历史一致性**。

其他核验：
- 472 帧 × 每帧 181 条 = **85,432 条射线**；9 帧无公制深度。不得将“射线有观测”理解成“连续可通行长度足够”。
- manifest 记录了两个 Hugging Face 快照 revision（SegFormer `21b3847...`、DAV2 `5426e4f...`）和 v5 checkpoint SHA256 `952c2a156...`。E3 执行器源代码有 SHA256 和加载路径检查；文件现场复核没有在本次远端审查重做。
- `replay_on.log` 中 SegFormer 提示 **19 类上游头 vs 5 类构造头，暂时重初始化**。核对 `pc/tools/corridor_truth_eval.py:199-214`，随后 `model.load_state_dict(ckpt["state_dict"])`（默认严格加载）覆盖五类头；因此**不能仅凭这条警告断言最终 v5 分割头随机或本轮无效**。仍需以 v5 checkpoint 哈希/加载成功和独立 GT 样本核验。
- `pc/tools/mobilio_sanpo_ab.py:808-817` 检查 Git HEAD 与干净状态；`:890-929` 校验模型缓存并从快照加载；`:617-640` 先计算 Surface 路线、只读 trace，然后 Occupancy 和融合。OFF/ON 同字段一致支持诊断旁路未改变当前最终决策，但不覆盖 Android/TTS 或全部未检查的行为。
- 数据使用：SANPO 官方网站 https://google-research-datasets.github.io/sanpo_dataset/ 标注 Creative Commons V4.0 且可分享改编；发布派生物仍须按实际许可文本执行署名、隐私与再次分发核查。记录里的原始画面和人员隐私不能因许可证而忽略。

## 3. 必须纠正的技术决策：不该直接优化 125 个 no_candidate

原 E3 重点假设是 `surface:no_candidate_ray` 影响可走方向。但新数据表明，**125 个 no_candidate 中 108 个来自 danger 代理帧，仅 7 个来自 confirmed_clear。** 将所有 125 个当作“错误的拒绝”并放宽射线阈值，可能把已有的谨慎 UNKNOWN 变为危险的 CLEAR，违反安全边界。

在 confirmed_clear 的 121 个未 CLEAR 帧中，**77 个原因是 `surface:non_walkable`**，比 no_candidate 的 7 个多得多。同时 danger 帧上仍有 17 个 CLEAR。**本轮唯一值得投入的近端技术瓶颈应改为“语义分割→公制深度/地面投影→Surface 网格更新→射线判定的因果证据可靠性”**，而不是无假设地调 `min_candidate_m` 或 `max_gap_cells`。

`surface:non_walkable` 只是一层输出原因，**尚不能定位**是 v5 语义、DAV2 尺度、标定/姿态、投影错误、旧网格污染/衰减、DDA 穿格，还是 GT 代理本身过宽。没有原图/GT、预测 mask、投影后的 BEV/网格和实际射线叠加，不能由计数推导根因。

## 4. 新阶段裁决与唯一下一阶段实验

**决策：ADJUST / G2 HOLD（E3 实际回放完成、诊断旁路等价性具备证据、根因验收未通过）。**

唯一任务：执行 `docs/phase_prompts/E3_SURFACE_CAUSAL_OVERLAY_20261107.md`：对现有 481 帧中预先挑选的 12 个代表帧，输出**原图/GT + v5 mask + DAV2 深度/尺度 + 地面投影/网格单元 + 选中/被拒射线**的逐层诊断叠加；必须覆盖 confirmed_clear `surface:non_walkable`、danger CLEAR 和少量 no_candidate 控制样本。

不再运行多轮空 smoke；如果持久地图状态无法从现有存档重建，只允许**一次**必要的顺序诊断回放以取得 12 个目标帧的网格快照；不得重复 OFF/ON 481 帧进行同义验证。按可观察中间量判定故障层，避免预先认定单一根因。只有获得因果证据后才提出单一可证伪修正；不得改变生产授权策略。

P1 Basalt/TUM VI 仍为 PC VIO 首选，但当前未运行，独立环境、磁盘和 TUM VI 校验未过，不将 E3 等同 P1 GO。手机/Android 与 Last10m 门禁不变。

## 5. 首选、备选和不建议技术路线

| 方向 | 决定 | 理由、成本、许可证/适配约束 |
|---|---|---|
| **现有 v5 + DAV2 + Surface/Occupancy 逐层因果诊断** | **本轮首选** | 用现有快照和 12 帧分层叠加直接分辨错误环节，最省依赖/磁盘；现有 DAV2 Small 许可可单独核对 |
| **GT 语义/GT 深度的受控替换消融** | **备选，只有叠加后** | 只用于离线归因，不进入生产路径；若安全代理和可用性改善须在固定数据上重核 |
| **Basalt + TUM VI room1 VIO** | **下一主线，P1 HOLD** | 成熟 VIO、BSD-3-Clause 代码；需实际 dataset、IMU/标定和构建资源并输出 ATE/RPE，不能用 SANPO recorded-pose 代替 |
| **OpenVINS / VINS-Fusion / RTAB-Map 或阈值大扫描** | **当前不建议** | 叠加环境/许可/部署成本，不解决目前已量化的 Surface 根因，也不证明净收益 |

## 6. 固定评分卡（严格按 10/10/15/20/15/20/10 权重）

| 维度 | 权重 | 上轮评分依据 | 本轮评分 | 变化理由 |
|---|---:|---:|---:|---|
| 架构与边界 | 10% | 7 | 7 | 未更改生产边界；冻结 trace 为诊断用途 |
| 感知能力 | 10% | 5 | 5 | 无独立新 GT 感知精度 |
| 定位与时序 | 15% | 2 | 2 | Basalt/TUM VI 未运行 |
| 深度与三维几何 | 20% | 3 | 3 | 仅观测到失败分布，未验证公制误差改善 |
| 地图/导航可用性 | 15% | 3 | 3 | CLEAR 49/481 不变，没有可用性增益 |
| 安全证据 | 20% | 2 | 2 | proxy 危险 17/295 CLEAR 未改善，不是安全验收 |
| 可复现性 | 10% | 6 | **7** | 本轮新增冻结输入清单、全程 trace OFF/ON、派生数据与独立检查，但二进制全量审计及几何叠加未完成 |

**严格加权：** 上轮同维度基线 `7×.10 + 5×.10 + 2×.15 + 3×.20 + 3×.15 + 2×.20 + 6×.10 = 3.55 → 3.6/10`；本轮 `3.65 → 3.7/10`（四舍五入）。先前项目总览写的是**主观 4.0/10**，它与固定权重下的旧分项**无法数学对齐**；这里作口径纠正，不能将 4.0→3.7 解释为技术退步。可比的加权进步是 **3.6→3.7（+0.1，且仅复现性）**。之后一律沿用本卡口径。

技术选型成熟度：**定位已选型/未复现/未集成；地图参考已做桌面回放，但真实三维几何和用户安全未验收。** 证据等级：**DATASET + DESKTOP_REPLAY（含独立聚合与对照核查）；非 DEVICE_VERIFIED / USER_STUDY**。

P0–P6：
- **P0 部分**：SANPO 指定 session IMU 缺口有审计；独立 VIO 环境、磁盘/下载校验未达总门禁。
- **P1 HOLD**：Basalt + TUM VI 未运行。
- **P2 部分证据 / 门禁未过**：既有 DAV2 57 帧不能替代独立米制真值对照。
- **P3 HOLD**：Mobilio 参考桌面回放和 E3 诊断完成，但几何/地图改进未验收。
- **P4 NOT_STARTED**：独立跨场景验证未通过。
- **P5 独立 HOLD**：Last10m 目前只有少量 replay demo，没有目标商店—入口关系充分真值。
- **P6 HOLD**：Android/手机最后。

完整 PC 阶段门禁通过：**0/7**；请勿把中间研究记录数量当阶段完成率。

## 7. 及时提醒与执行边界

- **新进展：** E3 从“未完整回放”进入“481 帧离线回放完成+部分独立核验”。原总计划此前的“E3 未运行”属于过时状态，需更新状态链接。
- **高优先级技术纠错：** 不要按 `125 no_candidate` 批量降低安全阈值；危险代理占 108/125。下一步看 77 个 confirmed-clear `surface:non_walkable` 的失效链，并将 danger CLEAR 作为必检负对照。
- **仍存在的风险：** 17/295 危险代理最终 CLEAR；没有方向正确性和真机证据，禁止作为现实行走安全依据。
- **执行资源：** 本轮审查没有启动 smoke、安装模型、下载数据或运行 Android。只读取仓库内容并独立复算公开文本分组，耗时仅用于审查；没有新增实验计算开销。
- 用户若仅需执行：复制下一阶段任务书给执行者，回传 12 帧诊断与原始派生文件。无需替项目指导者决定技术方向。

## 8. 复用技术速览（状态与用途）

| 资源 | BlindNav 目的 | 当前状态 |
|---|---|---|
| Basalt | PC VO/VIO 轨迹 | SELECTED / NOT_RUN |
| TUM VI room1 | 手持 VIO GT 与 ATE/RPE 基准 | SELECTED / CHECKSUM_PENDING |
| EuRoC V1_01_easy | 第二 VIO 基准 | SELECTED / SOURCE_TERMS_PENDING |
| ADVIO-09 | 后续真实手机数据域差 | DEFERRED，CC BY-NC 4.0 |
| DAV2 Metric VKITTI Small | 现有深度基线 | EXISTING 57-FRAME EVIDENCE，不重复推理 |
| Metric3D v2 Small | 公制深度+法线候选 | CANDIDATE，checkpoint 许可、内参与 GT 待核 |
| Harvard/Mobilio | Surface/Occupancy 地图思想 | REFERENCE ADAPTED / DESKTOP_REPLAY，不是完整 AR 系统 |
| SANPO | 目标视角数据与离线几何归因 | PARTIAL DATA，指定 session 无原始 IMU 观测 |
| NavAble | BLV 无障碍设施数据 | DISCOVERED / NOT_DOWNLOADED |
| OpenVINS / VINS-Fusion / RTAB-Map | 明确缺口后的备选 | DEFERRED / ON-DEMAND |

**最终阶段裁决仍为 ADJUST / G2 HOLD。下轮只执行因果叠加，不把更多仪表、smoke 或新依赖当作项目成果。**
