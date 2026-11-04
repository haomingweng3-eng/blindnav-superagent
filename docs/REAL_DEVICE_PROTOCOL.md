# BlindNav 真机 Demo 协议（受控场景 / ShadowWalk）

日期：2026-11-04（草案，基线 `pc-v0.1`）
适用：PC 链路 + ShadowWalk 虚拟走廊 stub 的受控真机演示

> **一句话**：这是**有安全员在旁的受控 demo**，不是独立安全导航。系统随时可能因为看不清而要求停下；
> “没报障碍”不等于“安全”，“报了直行”也不等于“可以放心走”。

## 1. 适用范围与硬性边界

- 仅在**封闭或受控路线**演示：人行道、校园步道、无障碍通道；路线事先勘察，安全员在一臂距离内，盲杖照常使用。
- 不进入机动车道、不单独过街、不在雨雪/夜间弱光/强逆光的首次演示中使用。
- 系统的输出是**提示**，不是控制指令；不驱动机器、不替代人的判断。
- 演示前必须确认：相机高度与标定参数一致、IMU/重力可用、电池与录音正常、安全员知晓急停手势。
- 出现任何不确定，默认动作是**停下并用盲杖探查**，不是继续走。

## 2. 拒答与降级条件

任一条件触发，最终指令不得是 `FORWARD`。状态语义与实现见 `pc/blindnav_pc/safety.py`。

| 条件 | 判定来源 | 输出 |
|---|---|---|
| 地面标定失败：`fit.ok=False` | `GroundFit` | `UNKNOWN` → `OBSERVE`/`STOP` |
| 标定置信度低：`confidence < 0.45` | `SafetyConfig.min_confidence` | `UNKNOWN` |
| 地面残差大：`residual > 0.08` | `SafetyConfig.max_residual` | `UNKNOWN` |
| 姿态与视觉地面法线冲突 > 15°，或地面拟合退化为视觉回退 | `attitude_visual_conflict` | `UNKNOWN` |
| 输入过期：帧间隔 > 500 ms，或时间戳倒退 | `stale_frame` / `timestamp_regression` | `UNKNOWN` |
| 无可信重力/IMU：`gravity_source` 不是 `TYPE_GRAVITY`，或只有固定重力/纯姿态且与视觉不一致 | 调用方标记 + `fit` | 降级为 `OBSERVE`，日志标注 `gravity_untrusted`；不得宣称已标定 |
| 走廊覆盖不足：前方走廊已知单元比例 < 0.30 | `SafetyConfig.min_corridor_coverage` | `UNKNOWN`（`corridor_unseen`） |
| 下落风险未确认：`drop_unconfirmed` / `drop_edge_unobserved` / `low_obstacle_unknown` | `layers.UNKNOWN_REASONS` | `UNKNOWN` |
| 已确认障碍 / 死路 / 走廊阻塞：`drop_risk` / `head_obstacle` / `body_obstacle` / `low_obstacle` / `dead_end` / `corridor_blocked` | `layers.BLOCK_REASONS` + planner | `BLOCKED`，允许转向离开，不允许前进 |
| ShadowWalk：无外部位姿、可见可行走点不足、走廊覆盖 < 0.5、偏离走廊 > 80% 半宽、航向偏差 > 30° | `shadowwalk.ShadowWalkTracker` | `UNKNOWN` / `STOP`；见第 6 节 |
| 分割/深度模型未加载、输入尺寸异常、推理异常 | 调用方 | `UNKNOWN`，禁止用上一帧结果继续前进 |

补充规则：

- `UNKNOWN` 离开需要连续 3 帧可靠观测（`unknown_recovery_frames`），单帧好转不能直接放行。
- 标定失败时**不做**“用旧参数硬撑”的静默降级；必须显式进入 `OBSERVE/STOP` 并记日志。
- `BLOCKED` 下只允许发出“稍向左/稍向右”或“停下”，且必须先确认该方向在安全走廊内可走。
- 演示中若连续 3 次出现 `UNKNOWN` 或任意一次 `STOP` 无法由安全员在 10 秒内复核，终止演示。

## 3. 代码依据（当前实现）

- `pc/blindnav_pc/safety.py:113` `SafetyStateMachine.update()`：先收集 `unknown_reasons`，只要非空就置
  `UNKNOWN`；`hazard_blocked` 或规划器死路/阻塞置 `BLOCKED`；其余才可能是 `CLEAR`。
- `pc/blindnav_pc/safety.py:125`：从 `UNKNOWN` 恢复需要 `unknown_recovery_frames` 个连续可靠帧。
- `pc/blindnav_pc/pipeline.py:146` `_safety_policy_override()`：`UNKNOWN` 下把 `FORWARD/LEFT/RIGHT/RECENTER`
  全部改成 `OBSERVE`；`BLOCKED` 下把 `FORWARD/RECENTER` 改成 `STOP`。
- `pc/blindnav_pc/pipeline.py:154` `_safety_command()`：最终输出守卫，`UNKNOWN` 永远不能变成前进或侧向指令，
  `BLOCKED` 永远不能变成前进。
- `pc/blindnav_pc/shadowwalk.py`：走廊 stub 只在“位姿可信 + 走廊看得足够清楚 + 人仍在走廊内”时给
  `STRAIGHT/LEFT/RIGHT`；否则 `UNKNOWN/STOP`。

结论：**UNKNOWN 与 BLOCKED 都不会产生 FORWARD**，这是输出级的硬约束，不依赖模型置信度解释。

## 4. 目标门槛与统计口径

所有指标按 session 单独报告，再给总体；同时给出样本量 n。禁止只挑成功片段。

| 指标 | 定义 | 门槛 |
|---|---|---|
| 路缘召回（curb recall） | 评测片段内每一处高度差 ≥ 8 cm 的路缘/台阶/站台边缘；系统在其前 1.5 m 内输出非 `FORWARD`（`STOP/OBSERVE/LEFT/RIGHT`）计为召回 | **≥ 70%** |
| 近场危险障碍召回 | 障碍进入以人中心为轴、宽 `body_width + 2*margin`、前方 2 m 的扫掠走廊；该帧输出非 `FORWARD` 计为召回 | **≥ 90%** |
| 端到端延迟 | 相机帧时间戳 → 最终中文指令文本生成（不含 TTS 播放） | 报告 P50 / P95 / P99；demo 目标 **P95 ≤ 300 ms**（参考机型） |
| FalseClear | 存在近场危险或路缘事件时输出 `FORWARD` 的比例 | 目标 **0**；任何一例都必须复盘 |
| UNKNOWN 率 / SAFE coverage | `UNKNOWN` 帧占比 / 可安全通行帧占比 | 必须与召回一起报告，防止用“全停”刷召回 |
| 标定失败降级正确率 | 标定失败帧中输出 `OBSERVE/STOP` 的比例 | 目标 **100%** |

延迟需拆开报告：分割、深度、地面标定、BEV/规划、安全状态机、文本生成。只报总延迟不报分项，视为不合格。

## 5. 红绿灯策略

- 红绿灯模块的输出域**只有** `green` / `red` / `uncertain` 三种，不输出概率性建议。
- **永远不做 auto-go**，永远不说“可以过街”“现在走”。`green` 也只表示“灯色被识别为绿”，
  不构成过街许可；过街决定权始终在人。
- `red` 与 `uncertain` 都按“等待/停下”处理；模型没看清、遮挡、逆光、距离过远一律 `uncertain`。
- 红绿灯结论**不得**覆盖 `safety.py` 的状态：即使灯是 `green`，只要 `UNKNOWN/BLOCKED`，仍然 `STOP/OBSERVE`。
- 当前 `pc` 链路**还没有**红绿灯模块；未接入前，设备端必须输出 `uncertain` 或保持静默，
  不得用任何启发式“看起来像绿灯”的提示代替。

## 6. ShadowWalk 虚拟走廊 stub 的定位

- 已有实现：`pc/blindnav_pc/shadowwalk.py`，演示入口 `pc/tools/shadowwalk_demo.py`。
- 三条预定义走廊（50–100 m，宽 2.5 m）：`straight60`、`left_arc80`、`right_arc100`；支持
  `--corridor-json` 自定义 polyline。
- 输入：现有分割 mask + 米制深度 + 相机内参 + 重力方向 + **外部位姿**（PDR/里程计）。
  输出：`STRAIGHT / LEFT / RIGHT / STOP / UNKNOWN` 与横向偏移、航向误差、覆盖率。
- 明确边界：stub 不定位、不规划、不控制；没有可信位姿或可见可行走地面不足时直接 `UNKNOWN/STOP`。
  它只用于验证“虚拟走廊 + 现有分割 mask”的接口与降级逻辑，不代表完整 ShadowWalk 系统。
- 演示时走廊必须与现场路线一致，且演示前用卷尺核对起点、宽度与拐点。

运行：

```bash
cd pc
.venv/bin/python tools/shadowwalk_demo.py --corridor straight60 --frames 12 --drift 0.9
.venv/bin/python -m pytest tests/test_shadowwalk.py -q
```

## 7. 当前诚实状态（2026-11-04）

基于 SANPO-Real 3 个 session、481 帧的离线评测（`pc/out/real_eval/summary.md`）：

- 输出级 FalseClear **0/28**；2 m 内走廊有障碍仍判 `CLEAR` **0/68**；11 次标定硬失败全部 `STOP/OBSERVE`。
- 但 `UNKNOWN` **48.0%**、SAFE coverage **11.0%**，离“可用”很远；主要瓶颈是 VIO 重力误差 6–11° 与
  单目深度 AbsRel 0.762。换 GT 重力后 AbsRel 0.208、UNKNOWN 5.0%，SAFE coverage 也只到 29.5%。
- 因此：目前**只能做受控 demo**，不能宣称独立导航；路缘召回与近场障碍召回尚未在 ImVisible/Entrances 上
  完成正式评测，不能引用 SANPO 的 FalseClear 当作真机指标。

## 8. 数据与复现

- 主验数据：`pc/data/eval/imvisible/`（人行横道框，5219 张）与 `pc/data/eval/entrances/`（入口/台阶，165 MB）。
- 仅作 smoke：`pc/data/eval/blind_road_crosswalk/`（400 张盲道/人行道，46 MB），不用于主结论。
- `sidewalk-semantic` 仍 **blocked**（HF gated，本机无 token；CC BY-NC 4.0，仅研究用途）：
  1. 登录 HF，打开 https://huggingface.co/datasets/segments/sidewalk-semantic 同意条款（auto-approve）；
  2. 在 https://huggingface.co/settings/tokens 建 read token；
  3. `pc/.venv/bin/hf auth login --token <TOKEN>`；
  4. `pc/.venv/bin/hf download segments/sidewalk-semantic --repo-type dataset --local-dir pc/data/eval/sidewalk_semantic`；
  5. parquet 需用 `datasets`/pyarrow 解成 png 后再进评测。
- 所有数据在 `pc/data/`（已 gitignore），**不提交**；评测输出在 `pc/out/`，同样不入库。

## 9. 演示检查清单

- [ ] 路线已勘察，走廊/拐点与配置一致，安全员到位
- [ ] 相机高度、内参、安装角度与配置一致；IMU 重力方向可用
- [ ] `pytest -q` 全绿；`shadowwalk_demo.py` 冒烟通过
- [ ] 记录每帧时间戳、指令、安全状态、覆盖率；失败片段留 overlay
- [ ] 任何 `UNKNOWN/STOP` 由安全员复核后才继续
- [ ] 演示结束如实报告 P95 延迟、召回、UNKNOWN 率与失败案例
