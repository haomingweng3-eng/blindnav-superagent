# 唯一下一阶段执行提示词：校正后的 2×2 几何输入消融

> 项目指导决定：本任务替代当前 12 帧 E3 causal overlay，优先裁决已有 A/B/C/D 四路消融中的变量混杂。只执行一次有意义的完整对照，不做重复 smoke，不下载新数据或模型。

## 目标

使用现有 481 帧冻结数据，对 segmentation（GT/v5）与 depth（GT/DAV2）做严格受控的 2×2 消融，确保四个条件除这两个输入因素外，其他几何和地图变量完全一致。

本任务要验证的不是“哪种模型看起来更好”，而是回答：
1. 分割输入本身对 Surface/Occupancy/最终决策的影响；
2. 深度输入本身对上述结果的影响；
3. 两者是否存在交互效应；
4. 现有 A/B/C/D 结果是否因 calibration mask、up vector、camera height 不一致而混杂。

## 一、先核对并冻结输入

基线代码：
- 分支：`agent/corridor-obstacle-split-ad0888814215`
- 数据：现有 SANPO-Real 481 帧；
- 原输入清单：`docs/phase_reports/e3_input_audit_20261107/input_inventory.json`
- manifest SHA256：`2b4997b6c3e561e62a046fb812edbd31de2ad231596da0ab9219755f2a20e37c`
- 现有 E3 回放与射线证据：`docs/phase_reports/e3_surface_6813160/`
- 四路旧矩阵：`docs/phase_reports/mobilio_feedback_20261107/layer_matrix.json`

先核对本地现有输入和 checkpoint 哈希与 manifest 一致。不要仅凭路径相同认定一致。输入缺失或哈希不匹配则一次性报告缺项并 HOLD；不重复扫描、不下载新数据。

## 二、修正当前已发现的混杂

原代码 `pc/tools/mobilio_sanpo_ab.py`：
- 当前 466–468 行：DAV2 的 `GroundCalibrator.update` 使用 `v5_cls` 生成的 walkable mask；
- 当前 471–478 行：GT depth 分支使用 `gt_ref.up` 和 `gt_ref.h`；
- 当前 479–486 行：DAV2 分支使用 `fit.up` / `up_pose` 和 session camera height；C 条件虽用 GT map segmentation，DAV2 标定 mask 仍是 v5。

因此原 A/B/C/D 不是严格正交的 segmentation×depth 消融。旧结果保留为“旧代码路径行为记录”，不得再用于单因素因果归因。

## 三、四个校正条件

采用同一：
- 481 帧及完全相同的 frame/session 顺序；
- recorded pose（明确标记 offline proxy，不是 VIO）；
- 相机内参 K；
- gravity/up vector；
- camera height；
- DAV2 calibration mask source；
- Surface/Occupancy 初始化、参数、衰减和更新顺序；
- SafetyCoordinator、fuse、STOP/UNKNOWN/CLEAR、authorized_motion 语义。

推荐固定使用生产 v5 mask 作为 DAV2 scale/shift calibration mask，四个条件完全相同。固定同一个 recorded gravity/up 和同一个 camera height；不得在 GT-depth 分支切换成 GT plane height/up。DAV2 的标定只计算一次/按原时间序列更新，并以相同结果供 C、D 使用。

| 条件 | 用于地图更新的语义 mask | 用于几何投影的 depth | DAV2 calibration mask |
|---|---|---|---|
| A | GT segmentation | SANPO GT metric depth | 固定 v5 mask |
| B | v5 segmentation | SANPO GT metric depth | 固定 v5 mask |
| C | GT segmentation | DAV2 calibrated depth | 固定 v5 mask |
| D | v5 segmentation | DAV2 calibrated depth | 固定 v5 mask |

注意：这是固定生产标定约定下的受控输入消融，不是“全知 oracle”，也不是部署性能。若 SANPO GT depth 的单位、有效范围、对齐方式或相机坐标定义不清楚，停止并报告，不得硬套。

## 四、执行方式：一次完整基准，不要重复冒烟

1. 对当前代码做静态审查，确认四个分支只改变上表两项因素。
2. 只运行本次修改直接相关的单元/契约测试。
3. 用现有模型输出缓存、数据、checkpoint，避免重复推理；若缓存无法复用，先报告预计成本，不自行下载或大规模重跑。
4. 一次性对完整 481 帧运行四个条件，保存逐帧结果和每个输入来源。
5. 不做重复 OFF/ON trace 回放；E3 现有 trace 结果直接复用。
6. 不做阈值扫描，不改安全逻辑，不引入 Basalt/Metric3D/其他模型。

## 五、每帧必须记录的来源与变量

- session/frame；
- segmentation_source（GT/v5）；
- depth_source（GT/DAV2）；
- calibration_mask_source（固定 v5）；
- up_source 与 up vector；
- camera_height_source 与数值；
- intrinsics hash；
- pose_source（SANPO fixed pose proxy）；
- depth valid pixel ratio、有效距离范围；
- ground calibration a/b、confidence、validity；
- 投影点数、无效点数；
- Surface、Occupancy、Fusion state；
- effective_command、guidance、authorized_motion；
- danger / confirmed_clear / other 标签（GT 仅评分，不进入生产决策）。

## 六、必须报告的结果

每个条件报告：
- frames=481、danger=295、confirmed_clear=150、other=36；
- Surface CLEAR/OBSERVE/UNKNOWN；
- Occupancy CLEAR/OBSERVE/BLOCKED/UNKNOWN；
- Fusion CLEAR/OBSERVE/BLOCKED/UNKNOWN；
- 最终 CLEAR/授权方向、OBSERVE、UNKNOWN、STOP；
- danger 上最终 CLEAR 与 forward guidance miss；
- confirmed_clear 上 CLEAR、false STOP、UNKNOWN；
- 每 session 同类指标；
- 有效深度像素率、地面拟合失败率、投影有效率。

计算四个简单效应：
- segmentation effect under GT depth：A vs B；
- segmentation effect under DAV2 depth：C vs D；
- depth effect under GT segmentation：A vs C；
- depth effect under v5 segmentation：B vs D。

同时计算 difference-in-differences 交互效应。所有指标给出 numerator/denominator，保留逐帧 CSV，不能只给百分比。

没有方向/轨迹真值，不得声称 LEFT/RIGHT 正确率。danger proxy 上 CLEAR 只作为风险信号，不是现实事故率。

## 七、交付物

放入新目录：
`docs/phase_reports/corrected_2x2_ablation_20261107/`

至少包含：
- `README.md`：问题、实验设计、旧消融混杂说明、结论和限制；
- `per_frame.csv`；
- `factor_effects.json`；
- `per_session.csv`；
- `run_metadata.json`：代码 commit、输入/模型哈希、配置、环境、命令；
- `logs/`；
- `independent_recompute.py` 与复算结果；
- 关键指标对照图（不需要大规模视频）。

## 八、停止条件与禁止事项

以下任一发生即 HOLD：
- 输入、GT depth、模型或 checkpoint 缺失/哈希不符；
- 四条件 K/up/camera height/pose/map config 不一致；
- DAV2 calibration mask source 不一致；
- 逐帧 key 或 session 分母不一致；
- 需要改变安全决策或阈值才能运行；
- 结果不能独立复算。

禁止：
- 修改 SafetyCoordinator、融合和运动授权逻辑；
- 把 UNKNOWN 改 CLEAR；
- 下载大型数据或模型；
- 安装新依赖或清理/删除用户文件；
- 修改 Android/手机代码；
- 合并 PR #20；
- 把旧 A/B/C/D 结果继续描述为严格正交因果结论。

## 九、最终裁决

完成后给出 GO / ADJUST / HOLD / STOP，并回答：
1. segmentation 的独立效应是什么？
2. depth 的独立效应是什么？
3. 是否存在显著交互或 calibration failure？
4. 哪个输入链是下一步最值得修复的唯一瓶颈？
5. 是否值得继续 Harvard/Mobilio，还是应替换某个输入模块？

本实验是当前唯一任务。完成前不启动原 12 帧 overlay、不运行 Basalt、不引入 Metric3D、不启动手机验证。
