# 消融解释纠正与研发提速决定（2026-11-07）

> 决策：**ADJUST；停止文档型循环，下一步必须执行校正 2×2 代码实验。**
>
> 当前最新可见分支包含 E3 视觉证据补充，但没有校正 2×2 实验的代码运行结果。本文件纠正此前审查中的一处过度概括，并明确真正阻塞。

## 1. 先回答“为什么感觉更差”

当前生产 D / Mobilio 481 帧：
- 最终 CLEAR/授权方向：49/481（10.2%）；
- UNKNOWN：275/481（57.2%）；
- danger proxy 上 any_miss：17/295（5.8%）；
- confirmed-clear 上授权方向：29/150（19.3%）。

对照 baseline_split_c：
- 授权方向：220/481；
- UNKNOWN：214/481；
- danger proxy any_miss：123/295；
- confirmed-clear 上授权方向：76/150。

因此当前系统表现不是简单“全面更差”，而是**明显偏保守、可用性大幅下降，同时 proxy danger 放行指标降低**。Mobilio 的授权方向比 baseline 少 171/481（约 77.7%），UNKNOWN 多 61/481；但 any_miss 少 106/295。这个 trade-off 是当前核心问题。没有方向轨迹真值，不能据此判断 LEFT/RIGHT 是否安全或正确。

按 session：
- Mobilio session 1：10/121 授权、74 UNKNOWN；
- session 2：37/184 授权、53 UNKNOWN；
- session 3：2/176 授权、148 UNKNOWN，其中 danger proxy 162/176。
这表明最差 session 的场景/标签构成非常不同，不能只看总平均值；新实验必须报告 session 分层效应。

## 2. 对旧四路消融审查的精确纠正

此前审查说旧 A/B/C/D “不能干净地归因单因素贡献”，这个表述过宽，需要更精确：

- **A vs B（GT depth 下的 segmentation 简单效应）仍可比较**：两条件均进入 GT-depth 分支，使用相同 gt_ref.up/h；差异是地图角色 mask GT/v5。
- **C vs D（DAV2 depth 下的 segmentation 简单效应）仍可比较**：两条件共用由 v5 mask 得出的 DAV2 calibration fit、同一 fit.up 与 session camera height；差异是地图角色 mask GT/v5。
- **A vs C、B vs D 的 depth 简单效应存在混杂**：GT depth 分支用 gt_ref.up/h；DAV2 分支用 fit.up/attitude 与 session camera height。不能将差异单独归因深度图质量。
- 因为 depth 主效应被几何约定混杂，完整 factorial 的 interaction/difference-in-differences 也不能作纯粹因果解释。

因此旧结果并非全部作废：保留 A-vs-B、C-vs-D 作为对应 depth 条件下的描述性/受控 segmentation 比较，但必须核对其他变量与同一帧数据；depth effect 与 interaction 标记 NOT_IDENTIFIED。新 2×2 的目的，是统一几何约定后重新估计两个主效应和交互效应。

## 3. 最新 E3 视觉证据说明了什么

最新提交 `da6951f3ce4a75d0f3984a7791fe521d7a46e3dd` 增加：
- 4 个脱敏真实回放案例；
- 每会话指标和计算脚本；
- 独立门禁复核；
- 射线/网格可视化。

这是真实的证据整理进展，但仍然是诊断性证据，不是算法修复。四例不能估计总体根因比例；一个单会话补充案例使用非冻结入口，模型字节未锁定。隐私像素化不能保证匿名，后续公开图片仍需审慎。

E3 视觉门禁：几何/射线案例 PASS（四例）；根因解释 ADJUST；G2 仍 HOLD。不得把 E3 视觉证据描述成导航性能改善。

## 4. 真正阻塞在哪里

阻塞不再是“缺少更多冒烟测试”或“缺少更多图表”，而是**四路输入消融中 depth 与几何标定约定不一致，导致我们不能可靠决定应该修深度还是修分割/地面标定。**

此外，当前 branch 最新可见提交主要是报告、视觉证据和文档；校正 2×2 实验尚未运行。必须停止继续新增纯审查文档，转向实现和运行一次实验。

## 5. 唯一下一步

执行：
`docs/phase_prompts/CORRECTED_2X2_GEOMETRY_ABLATION_20261107.md`

实施要求：
1. 复用现有 481 帧、v5 checkpoint、DAV2 输出/模型缓存；先用 manifest 核对哈希。
2. 修正 evaluator，让 calibration mask、K、up source/vector、camera height、pose、map config 在四条件中完全固定。
3. GT/DAV2 depth 只作为地图投影的 depth factor；GT/v5 segmentation 只作为地图角色 mask factor。
4. DAV2 calibration 每帧只计算一次，并在 C/D 共用同一 fit；禁止按条件重复更新有状态 calibrator。
5. 每帧导出来源和哈希，做四条件同帧配对，报告 A/B、C/D、A/C、B/D 和 interaction；按 session 分层。
6. 只跑本次改动相关的契约测试，然后一次性跑完整 481 帧四条件。禁止重复 OFF/ON trace、重复 smoke、阈值扫描。
7. 不修改 SafetyCoordinator、CLEAR/UNKNOWN/STOP、authorized_motion，不引入新模型，不启动 Android。

若现有 DAV2 缓存不存在，或 GT depth 的尺度/注册关系不明确，停止一次并报告具体缺项；不下载大型数据、不反复检查同一环境。

## 6. 当前项目指导决策与评分

- 技术方向：保留 Harvard/Mobilio 为待验证地图候选；先校正输入几何实验，不立即替换地图算法。
- VIO：Basalt + TUM VI 仍是后续 PC 基线，但当前先不并行，避免分散资源。
- 深度：保留 DAV2 既有结果；Metric3D 暂缓。
- 分割：保留 v5；若受控实验显示语义简单效应明显，再考虑 NavAble/其他训练数据适配。
- 不再扩大技术清单。

固定评分：**3.6/10**，本轮不加分。最新可视化提升可审查性，但没有改变感知、几何、定位、地图可用性或安全证据。不得因为新增 PNG、脚本或报告加分。

**当前状态：E3 诊断回放与四例可视化已完成；G2 根因未通过；校正 2×2 是唯一下一项技术任务。**
