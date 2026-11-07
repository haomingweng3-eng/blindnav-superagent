# BlindNav 四路输入消融设计复核与提速决策（2026-11-07）

> 决策：**ADJUST；暂停 12 帧叠加任务，先做一次校正后的 2×2 输入消融。**
>
> 本审查只读检查分支代码和既有报告，没有运行模型或修改生产代码。检查基线：`bd5cea73b96109b6b367d8695b6e112fae98ff08`。

## 1. 为什么目前“复用技术后效果仍差”

当前生产 D：481 帧中 CLEAR/授权方向 49，UNKNOWN 275；危险代理帧 17/295 最终 CLEAR。E2 的 37 个 observation_hint 没有增加任何最终授权。E3 的 481 帧射线诊断让失败可见，但没有改变导航结果。

这说明目前新增工作主要增加了诊断和证据，而不是导航能力。继续增加 smoke、可视化或开源项目清单不会解决问题。

## 2. 发现一个关键实验设计混杂：四路 A/B/C/D 不是严格正交的 2×2 消融

四个条件原意：
- A: GT segmentation + GT depth
- B: v5 segmentation + GT depth
- C: GT segmentation + DAV2 depth
- D: v5 segmentation + DAV2 depth

代码核查 `pc/tools/mobilio_sanpo_ab.py`：

- 第 466–468 行：只要 `need_model` 为真，`calib_cls = v5_cls`，再用该 mask 调用 `GroundCalibrator.update(q_dav2, calib_walk, session.K, up_pose, ...)`。
- 第 479–486 行：C 条件虽然地图角色 mask 使用 `gt_cls`，但 DAV2 的 scale/shift fit 已经由 v5 mask 计算；D 也使用 v5 mask。也就是说 C 的“GT segmentation”并没有控制 DAV2 地面尺度标定的 mask。
- A/B 的 GT depth 分支使用 `gt_ref.up` 和 `gt_ref.h`（第 471–478 行）；C/D 的 DAV2 分支使用 `fit.up` / `up_pose` 和 `session.camera_height_m`（第 479–486 行）。因此 depth 因子同时改变了 up-vector 来源和 camera-height 来源。

结果：现有 A/B/C/D 结果不是只改变 segmentation 与 depth 两个因素的严格正交对照。它们仍然是有价值的系统条件结果，但不能据此干净地归因“GT depth 比 DAV2 好/差”或“GT segmentation 的独立贡献”。

这也解释了为什么“GT+GT”授权 29/481 反而低于某些 DAV2 条件：不能立刻得出 DAV2 比 GT depth 更准确的结论。坐标/标定契约也在变化。

## 3. 技术纠正与对项目的影响

**必须纠正：此前把四路 A/B/C/D 当作干净的 segmentation×depth factorial ablation 来解释，是过度解读。**

- A/B 的 up 和 camera height 与 C/D 不同；
- C 的地图 segmentation 是 GT，但 DAV2 标定 mask 仍是 v5；
- 这会混淆 depth、segmentation 和 ground calibration 的效果。

旧数据不作废：它仍然描述四种当前代码路径的行为；但“单因素归因”结论降级为 NOT_IDENTIFIED。不能把它作为更换深度模型或分割模型的依据。

## 4. 项目指导决定：用一次校正实验替代重复诊断

暂停原计划中的 12 帧 Surface 因果叠加（`E3_SURFACE_CAUSAL_OVERLAY_20261107.md`），除非校正消融仍无法定位问题。当前更高价值的任务是一次**严格匹配几何约定的 2×2 消融**，直接回答 segmentation 与 depth 各自造成多少变化。

### 必须固定的共同变量

- 同一 481 帧、同一 session/frame 顺序和输入 manifest；
- 同一 recorded pose（明确标记为离线代理，不是 VIO）；
- 同一相机内参、同一 gravity/up vector、同一 camera height；
- 同一地图初始状态、Surface/Occupancy 参数、时间顺序和决策逻辑；
- 同一 depth calibration mask 来源和同一 DAV2 scale/shift 拟合方法；
- 只改变用于地图角色更新的 segmentation mask（GT/v5）与最终输入 depth map（GT/DAV2）。

推荐将 DAV2 的 scale/shift calibration mask 固定为生产 v5 mask，且在四个条件中完全相同；使用同一 recorded gravity/up 和同一 camera height。GT depth 条件直接使用 GT metric depth，但不切换 up/height。必须把这个设计称为“固定生产标定约定下的受控输入消融”，不是全知 oracle，也不是部署性能。

### 2×2 四个条件

| 条件 | 地图语义 mask | 地图深度 | DAV2 标定 mask |
|---|---|---|---|
| A | GT | GT metric depth | 固定 v5 |
| B | v5 | GT metric depth | 固定 v5 |
| C | GT | DAV2 calibrated depth | 固定 v5 |
| D | v5 | DAV2 calibrated depth | 固定 v5 |

A/B、C/D 分别只改变地图 segmentation；A/C、B/D 分别只改变 depth map。所有条件使用相同 up、height、K、pose 和地图配置。

若无法从现有数据实现共同几何约定，必须停止并报告精确原因，不得暗中混用 GT plane 与 predicted up。

## 5. 只运行一次有技术价值的实验

- 复用现有 481 帧 manifest、v5 checkpoint 和 DAV2 缓存/权重；不重复下载、不重复做 smoke。
- 先做一次静态代码审查确认四个分支确实只改变两个指定输入；只运行与本次改动直接相关的单元/契约测试。
- 然后一次性运行四个条件完整 481 帧，保留每帧 mask/depth/up/height/K/pose source 标识和 hash。
- 不需要先做 12 帧漂亮可视化；优先产出可复算的逐帧 CSV、因子效应和每 session 统计。
- 只有结果显示特定失败类别仍无法解释时，才针对少量帧制作图像叠加。

## 6. 必须报告的指标

每个条件都报告：
- 481 总帧；danger 295、confirmed_clear 150、other 36；
- Surface CLEAR/OBSERVE/UNKNOWN、Occupancy 各状态、Fusion 状态；
- CLEAR/授权方向、OBSERVE、UNKNOWN、STOP；
- danger 上最终 CLEAR/forward guidance miss；
- confirmed_clear 上 CLEAR、false STOP、UNKNOWN；
- 按 session 的同类指标；
- 每帧实际 segmentation source、depth source、calibration mask source、up source、camera height source、pose source；
- 深度有效像素率、地面拟合有效率、投影点数和无效/截断比例。

输出 2×2 因子效应：
- segmentation effect under GT depth: A vs B；
- segmentation effect under DAV2 depth: C vs D；
- depth effect under GT segmentation: A vs C；
- depth effect under v5 segmentation: B vs D；
- interaction effect（差中差），不要只看总 CLEAR 数。

没有独立方向轨迹真值时，不得声称 LEFT/RIGHT 正确率。danger proxy 上 CLEAR 仍是风险指标，不是现实事故率。

## 7. 停止条件

- 输入缺失、哈希不匹配、frame keys 不一致；
- up/height/K/pose 任一变量在四条件间不一致；
- calibration mask source 不一致；
- GT depth 与 DAV2 depth 的有效范围/单位不清楚；
- 决策授权字段因诊断代码改变；
- 只能得到聚合数字、无法复算逐帧因子效应。

发生以上情况立即 HOLD，不再做重复 smoke，不下载新模型，不改安全阈值。

## 8. 评分和当前状态

固定权重评分维持 **3.6/10**（相较此前 3.7/10 下调 0.1，原因是发现原四路消融存在影响因果解释的设计混杂，实验可复现性维度由 7 调整为 6；这不是产品能力倒退，而是对证据可信度更严格）。

- 架构 7/10
- 感知 5/10
- 定位/时序 2/10
- 深度/几何 3/10
- 地图/导航可用性 3/10
- 安全证据 2/10
- 实验可复现性 6/10

**下一步唯一任务：执行校正后的 2×2 消融。完成前不运行 Basalt、不引入 Metric3D、不改 Android。** Basalt + TUM VI 仍是之后定位基准，不是当前这项混杂归因的替代品。
