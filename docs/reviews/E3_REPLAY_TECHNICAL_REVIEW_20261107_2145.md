# E3 Surface 完整回放技术审查（2026-11-07 21:45 +08:00）

> 决策：**ADJUST；E3 回放阶段完成，G2 因果根因门禁仍 HOLD。**
>
> 本次审查基于分支 HEAD `bd5cea73b96109b6b367d8695b6e112fae98ff08`。实验代码快照 `6813160c555e297b03a599eb689b8083fe4ce36a`。没有重新运行实验；审查读取了报告、机器可读 JSON、分组 CSV、运行日志、命令记录和因果叠加提示词，并对可读分组统计做独立复算。

## 1. 本轮真正进展

E3 已从“仅有诊断代码/未完成回放”推进到 **481 帧 PC 离线回放完成，OFF/ON trace 对照和部分独立核查完成**。

证据：
- OFF/ON 两个运行均 exit code 0，各含 481 帧 × 2 arms = 962 行。
- trace ON 共覆盖 481 个帧键：472 帧各记录 181 条射线（85,432 条），9 帧没有可用公制深度。
- trace 状态：RECORDED 398、NO_OBSERVATION 74、NO_METRIC_DEPTH 9。
- 独立结果文件报告 OFF/ON 的 17 个状态、命令、授权、时间和来源字段逐帧无差异；帧键、顺序相同。
- 32 项定向测试通过；这是 targeted tests，不是完整测试套件。
- 历史候选版本复跑与历史 CSV 的 11 个受检字段逐帧一致；当前 OFF 与旧历史只有 4 帧 guidance 字段不同，安全状态、effective command、授权和评分字段无差异。由于旧版模型快照未锁、原历史 CSV 未记录生成 commit，不能证明历史生成时所有字节完全一致。

因此，本轮证明的是**诊断旁路在受检决策字段上没有改变离线结果，并且射线失败分布可被观察**；没有证明导航性能改善、TTS/Android 行为或现实安全。

## 2. 关键数据复核

从 `per_group.csv` 与 `no_candidate_buckets.csv` 复核：

| 指标 | 数值 | 解释 |
|---|---:|---|
| 帧总数 | 481 | 121 + 184 + 176 个 session 帧 |
| GT 代理标签 | danger 295；confirmed_clear 150；other 36 | 三类合计 481 |
| Mobilio 最终状态 | CLEAR 49；OBSERVE 157；UNKNOWN 275 | 合计 481 |
| no_candidate | 125/481 = 26.0% | 不能全部视为错误拒绝 |
| no_candidate 中 danger | 108/125 = 86.4% | 高危代理帧占多数 |
| no_candidate 中 confirmed_clear | 7/125 = 5.6% | 可用性候选，但样本较少 |
| no_candidate 中 other | 10/125 = 8.0% | 不应强行归因 |
| no_candidate 的 trace | NO_OBSERVATION 74；RECORDED 51 | 74/125 = 59.2% 没有射线观测证据 |
| 最佳被拒射线 | NON_WALKABLE 16；UNKNOWN_GAP 7；无正长度被拒射线 102 | 16+7+102=125；这是描述性分组，不是已证实根因 |
| confirmed_clear 最终 CLEAR | 29/150 | 其余 OBSERVE 80、UNKNOWN 41 |
| confirmed_clear 原因 surface:non_walkable | 77/150；占未 CLEAR 121 帧中的 63.6% | 现象值得追查，不足以直接判定语义模型错 |
| danger 最终 CLEAR | 17/295 = 5.76% | 必须作为负向安全案例追查 |

**重要口径纠正：** `trace_status=RECORDED` 只表示射线上存在某种观测证据，不代表有足够长、连续、可信的 WALKABLE 通路。`termination_distance_m` 也不等于安全可走距离。最终状态 CLEAR、effective_command、forward guidance 和 authorized_motion 是不同字段，后续报告必须分别统计，不能互相替代。

另一个关键事实：历史结果的 baseline `any_miss=123/295`，Mobilio `any_miss=17/295`，但这只是当前 GT danger proxy 下的决策放行统计，不是独立的 detector recall，也没有 LEFT/RIGHT 轨迹真值。

## 3. 我对本轮结论的技术纠正

1. **不能把 125 个 no_candidate 当成 125 个错误拒绝。** 其中 108 个是 danger 代理帧；直接降低 `min_candidate_m` 或放宽 UNKNOWN gap 可能制造危险 CLEAR。
2. **下一步不应只追 no_candidate。** confirmed_clear 未 CLEAR 的 121 帧中，77 帧主因为 `surface:non_walkable`；同时 17 个 danger 帧进入 CLEAR。应同时检查“误拒绝”和“危险误放”两个相反方向。
3. **`surface:non_walkable` 不是根因。** 它只是最终状态原因，可能来自 v5 类别映射、DAV2 深度/尺度、相机内参/姿态、地面投影、网格更新/历史污染、DDA 穿格或 GT proxy 歧义。没有原图、预测 mask、深度、投影 BEV、网格快照和射线叠加，不能单凭计数决定改哪个模块。
4. **SegFormer 19 类与 5 类头的初始化警告不能单独证明 v5 头随机。** 日志显示后续加载 v5 checkpoint；必须结合 checkpoint 哈希和严格加载结果判断，不能只截取初始化警告做结论。
5. **OFF/ON 决策等价不是完整安全证明。** 它仅覆盖报告列出的离线字段；不覆盖 TTS、Android、设备时间同步和真实行走轨迹。

## 4. 阶段门禁

| 项目 | 决策 | 说明 |
|---|---|---|
| E3 冻结 481 帧回放 | <b>完成</b> | OFF/ON 均成功退出 |
| trace OFF/ON 受检字段等价 | <b>通过（限定范围）</b> | 17 个受检字段逐帧一致 |
| 射线诊断统计 | <b>部分通过</b> | 可复算分组已提供；完整 gzip 二进制由既有独立脚本核验，本审查未解压全量二进制 |
| 原图+mask+depth+BEV/grid+ray 因果叠加 | <b>未完成</b> | 当前仍是 G2 根因门禁的主要缺项 |
| 导航可用性/安全改善 | <b>未证明</b> | CLEAR 49/481、UNKNOWN 275/481 等基线没有改善 |
| Basalt/TUM VI | <b>HOLD</b> | 仍未运行 |
| 手机/Android | <b>HOLD</b> | 继续放在最后 |

## 5. 下一步唯一任务：12 帧 Surface 因果叠加

采用现有提示词：
`docs/phase_prompts/E3_SURFACE_CAUSAL_OVERLAY_20261107.md`

目标是对 12 个预先分层样本展示同一证据链：
- confirmed_clear + surface:non_walkable：6 帧；
- danger + final CLEAR：4 帧；
- confirmed_clear + surface:no_candidate_ray：2 帧。

对每帧叠加 RGB/GT（GT 仅审查）、v5 mask、DAV2 深度和尺度、内参/位姿、投影 BEV、Surface/Occupancy 当前及历史网格、被选中/拒绝射线和最终决策。若历史网格无法恢复，只允许一次最小顺序诊断回放；不得重跑 481 帧 OFF/ON，不做阈值扫描，不改安全逻辑。

这 12 帧是按失败类别挑选的案例集，不能用来估计总体根因比例。目标是区分至少一个真实失败层，并提出一个可证伪的下一项实验。

## 6. 技术选型与辅导

| 方向 | 决定 | 状态 |
|---|---|---|
| 现有 v5 + DAV2 + Surface/Occupancy 因果诊断 | 当前首选 | 现有缓存与回放证据可用 |
| Basalt + TUM VI room1 | 下一条定位主线 | 已选型，未运行，继续 HOLD |
| Metric3D v2 Small | 深度备选对照 | 权重许可、焦距和 GT 条件未齐，不启动 |
| OpenVINS/VINS-Fusion/RTAB-Map | 暂缓 | 当前无证据证明需要增加框架 |
| Android/小米手机 | 最后 | 不启动 |

技术辅导：先把“感知 mask → 深度反投影 → 地面坐标 → 网格单元 → DDA 射线 → 最终状态”按同一帧、同一坐标系逐层可视化。只有发现哪一层与 GT/几何证据不一致，才改那一层。不要先改阈值。

## 7. 项目评分

采用固定权重：架构 10%、感知 10%、定位/时序 15%、深度/几何 20%、地图/导航可用性 15%、安全证据 20%、可复现性 10%。

| 维度 | 分数 | 本轮变化 |
|---|---:|---|
| 架构与模块边界 | 7/10 | 不变 |
| 感知能力 | 5/10 | 不变 |
| 定位与时序融合 | 2/10 | 不变；Basalt 未运行 |
| 深度与三维几何 | 3/10 | 不变；只定位失败分布，未改善几何 |
| 地图与导航可用性 | 3/10 | 不变；无新增授权覆盖 |
| 安全证据 | 2/10 | 不变；离线 proxy 仍非现实安全验证 |
| 实验可复现性 | 7/10 | 从 6 升至 7；新增冻结 manifest、OFF/ON、射线日志和独立复算证据 |

加权总分：**3.7/10**（上一轮同一评分卡 3.6/10，+0.1，增量仅来自可复现性）。此前主观 4.0/10 与固定权重不一致，今后统一采用此加权评分口径。

技术选型成熟度：定位已选型未运行；深度有 DAV2 旧基线、Metric3D 待验证；地图参考回放已完成但根因未定。证据等级：DATASET + DESKTOP_REPLAY，不是 DEVICE_VERIFIED 或 USER_STUDY。

## 8. 当前复用技术速览

- Basalt：PC VIO 首选，SELECTED / NOT_RUN。
- TUM VI room1：VIO 基准，CHECKSUM_PENDING。
- EuRoC V1_01_easy：第二 VIO 基准，来源/许可待核。
- ADVIO-09：后续手机域差，CC BY-NC 4.0，暂缓。
- DAV2 Metric VKITTI Small：复用既有 57 帧证据，不重复推理。
- Metric3D v2 Small：候选，权重许可/焦距/尺度/GT 待核。
- Harvard/Mobilio：地图参考移植和桌面回放，不是原始 ARFoundation 系统完整复现。
- SANPO：部分数据可用，指定 session 原始 IMU 未确认。
- NavAble：无障碍设施数据候选，未下载。
- OpenVINS/VINS-Fusion/RTAB-Map：按需备选，不并行集成。

## 9. 提醒与决策

- 当前真正的进展是 E3 完成 481 帧离线诊断回放，不是导航能力改善。
- 当前最大技术问题从“没有射线数据”推进为“有射线诊断，但缺原图/几何/网格因果叠加”。
- 不要再做重复 481 帧 OFF/ON smoke；下一轮只做 12 帧因果叠加。
- 继续保持安全授权逻辑冻结。
- 只有因果叠加指向明确根因后，才设计一个单变量修正实验。

**最终决定：ADJUST；E3 回放完成，G2 因果根因验收 HOLD。**
