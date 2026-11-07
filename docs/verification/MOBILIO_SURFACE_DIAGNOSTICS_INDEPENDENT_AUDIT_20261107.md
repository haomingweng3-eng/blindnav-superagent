# Mobilio E3 surface diagnostics 独立核查（2026-11-07）

## 结论

**PASS（代码/单元级 instrumentation）；INCONCLUSIVE（完整 E3 replay）。**

## 核查结果

- `SurfaceRoute` 新字段均为带默认值的尾部 dataclass 字段：`termination_distance_m`、`last_walkable_distance_m`、`termination_cell`、`last_walkable_cell`。旧的 9 参数构造仍可工作，兼容性保持。
- `_ray_candidate` 只在扫描过程中记录最后 walkable 与终止 cell/距离；`raycast_route` 只复制所选 route 的诊断字段。没有写入 grid、Surface state、decision 或授权字段的副作用。
- 无候选路径仍返回 `SurfaceRoute("UNKNOWN", ..., candidate_count=0, ..., "no_candidate_ray")`，诊断字段为空/0；测试覆盖该情形。
- non-walkable 与 hazard 终止分别保留 `reason="non_walkable"`/`"hazard"`，不会被重编码为 CLEAR；既有 `test_mobilio_surface.py` 覆盖终止距离、hazard/非可行走和候选选择，新增 diagnostics 测试覆盖 termination 与 no-candidate。
- CSV 导出在 `pc/tools/mobilio_sanpo_ab.py` 中仅增加 `surface_termination_*` 和 `surface_last_walkable_*` 字段，并从 route 读取；`effective_command`、`safety_state`、`authorized_motion` 路径未改写。旧无 geometry 构造仍使用默认字段。
- 计划/报告明确 E3 当前为 **NOT_EVALUABLE**：冻结 CSV 缺少射线截断位置/候选角度，当前只完成诊断输出和单元级射线测试，未完成 481 帧 E3 replay。

## 反证尝试

1. **旧构造反证：** 检查 9 参数 `SurfaceRoute(...)` 调用与新增字段默认值；未发现因字段追加而破坏的调用。
2. **状态泄漏反证：** 搜索 diagnostics 字段的写入点；只见 route 构造/CSV 读取，未见对 `state`、decision 或 authorization 的赋值。
3. **终止语义反证：** 测试同时覆盖 non-walkable、hazard、no-candidate；返回 reason 与 state 保持 OBSERVE/UNKNOWN 语义，未出现 CLEAR/授权转换。
4. **完整性反证：** 检查计划标记；完整 481 帧 E3 replay 仍 NOT_EVALUABLE，因此不能把单测 PASS 当成端到端 E3 结果。

## 门禁

instrumentation 与单元测试可判 PASS；E3 完整实验保持 INCONCLUSIVE/NOT_EVALUABLE，不能据此宣称 surface diagnostics 已改善导航或安全指标。
