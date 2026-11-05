# BlindNav W1 安全协议

日期：2026-11-05（锁定；与 2026-11-04 `docs/REAL_DEVICE_PROTOCOL.md` 对齐）

W1 在继续叠模型之前，先把**可度量的安全协议**定下来。纯单目只用于有安全员的低速受控演示。1.2 m 相机高度加相对深度**不是**可靠的米制 BEV，不能拿来声称“前方安全”。云端 VLM 不参与实时走 / 不走。产品演示路径优先 ShadowWalk 虚拟走廊。

本文件是门槛和拒答规则。现场步骤、代码行号和数据路径的细表仍以 `docs/REAL_DEVICE_PROTOCOL.md` 为准。设备端 `app/src/main/java/com/blindnav/app/shadow/` 与 `last10m/` 只作为已有上下文，本轮不改 Kotlin。不使用 ARCore，不使用 LiDAR。

## 1. 三种失败时的动作：STOP / OBSERVE / 拒答

系统输出是提示，不是控制。看不清时的默认动作是停下，并用盲杖探查。

| 动作 | 何时使用 | 代码里的对应 |
|---|---|---|
| **STOP** | 危险已经确认：走廊里有障碍、死路、人偏离走廊、航向偏差过大 | `safety.py` 的 `BLOCKED` 把前进改成 `STOP`；`shadowwalk.py` 在偏离走廊或航向过大时给 `STOP` |
| **OBSERVE** | 还不能为前方负责：标定失败、置信度低、残差大、姿态与视觉冲突、帧过期或时间倒退、重力不可信、走廊覆盖不足、下落风险未确认 | `safety.py` 的 `UNKNOWN`。`pipeline.py` 把 `FORWARD` / `LEFT` / `RIGHT` / `RECENTER` 改成 `OBSERVE`。`UNKNOWN` 不能产生前进 |
| **拒答** | 信息不够，就不能给一个具体答案 | Last10m：无候选、低置信、距离未知、超距、截断处难分先后时 `refused`，不得沿用旧候选。红绿灯：`uncertain` 不得说成绿灯。ShadowWalk：无可信位姿或可行走地面不够时 `UNKNOWN` / `STOP`，不说“可走” |

补充：

- 离开 `UNKNOWN` 需要连续 3 帧可靠观测（`unknown_recovery_frames`）。单帧好转不能放行。
- `OBSERVE` 持续超过 `observe_to_stop_ms`（2 s）由行走策略转成 `STOP`。
- `BLOCKED` 只允许“稍向左 / 稍向右”或停下，且该方向必须仍在安全走廊内。
- 红绿灯的灯色**不能**覆盖上述结果。灯是绿色，只要安全状态是 `UNKNOWN` 或 `BLOCKED`，最终仍是 `OBSERVE` / `STOP`。
- 演示中连续 3 次 `UNKNOWN`，或一次 `STOP` 安全员不能在 10 秒内复核，就结束这一次演示。

## 2. 受控演示约束

- 安全员在一臂距离内。使用者仍拿盲杖。路线事先勘察，限人行道、校园步道、无障碍通道这一类封闭或受控路线，低速。
- 不进入机动车道，不单独过街。雨雪、夜间弱光、强逆光不作为首次演示条件。
- 演示前核对相机高度、内参、安装角、重力来源、电池和急停手势。标定失败必须显式 `OBSERVE` / `STOP`，不许用旧参数静默继续前进。
- 提示可以让人停下或观察。提示不驱动轮椅、不替代人的判断。
- 几何假设停在现有单目链路：分割 mask、相对深度、相机高度和外部位姿（PDR / 里程计）。没有 ARCore 平面，也没有 LiDAR 点云。

## 3. 必须报告的指标和门槛

每个 session 单独报，再给总体，并给出样本量 n。不许只挑成功片段。延迟要拆开：分割、深度、地面标定、BEV / 规划、安全状态机、文本生成。只报一个总数视为未完成。

| 指标 | 口径 | W1 门槛 |
|---|---|---|
| 路缘召回 | 高度差 ≥ 8 cm 的路缘 / 台阶 / 站台边缘。系统在其前 1.5 m 内输出非 `FORWARD`（`STOP` / `OBSERVE` / `LEFT` / `RIGHT`）计为召回 | **≥ 70%** |
| 近场危险召回 | 障碍进入人体走廊（宽 `body_width + 2 * margin`，前方 2 m）。该帧输出非 `FORWARD` 计为召回 | **≥ 90%** |
| 端到端延迟 | 相机帧时间戳 → 最终中文指令文本，不含 TTS | 必须报 P50 / **P95** / P99。演示目标 **P95 ≤ 300 ms**（参考机型）。不报 P95 不能算通过 |
| FalseClear | 存在近场危险或路缘时仍输出 `FORWARD` | 目标 **0**。出现一例就要复盘 |
| STOP 率 | 最终指令为 `STOP` 的帧占比 | 与召回一起报告。全程 `STOP` 可以把召回刷满，因此单独的高召回不算通过 |
| 翻转率（flip rate） | 相邻帧最终指令发生变化的比例：`变化次数 / (N - 1)` | 与召回一起报告。指令来回跳说明去抖不够，不能靠振荡刷召回 |
| UNKNOWN 率 / SAFE coverage | `UNKNOWN` 帧占比，以及可安全通行帧占比 | 与召回一起报告，防止用“全停”换召回 |
| 标定失败降级 | 标定失败帧里输出 `OBSERVE` / `STOP` 的比例 | 目标 **100%** |

路缘召回、近场危险召回、P95 延迟是 W1 的三条硬门槛。STOP 率和翻转率是必报项：它们没有单独的“越高越好”，但缺了它们，召回数字无效。

当前诚实状态沿用真机协议里的离线记录：SANPO-Real 上 FalseClear 可以为 0，同时 UNKNOWN 率仍然很高。那组数字**不能**写成路缘或近场门槛已经在 ImVisible / Entrances 上通过。

## 4. 红绿灯：只有 RED / GREEN / UNCERTAIN

实现在 `pc/blindnav_pc/traffic_light.py`。对外三态与代码常量的对应是：

| W1 名称 | 代码常量 | 含义 |
|---|---|---|
| RED | `red` | 灯色像红。按等待处理 |
| GREEN | `green` | 灯色像绿。**不是**过街许可，要人自己确认左右 |
| UNCERTAIN | `uncertain` | 没看清、遮挡、倒计时空白、无灯、标签冲突、低置信。按等待处理 |

标签映射（ImVisible / PTL）：

- 数字 `mode`：`0 → RED`，`1 → GREEN`，`2`（green countdown）`→ GREEN`，`3`（countdown blank）`→ UNCERTAIN`，`4`（无灯）`→ UNCERTAIN`。
- 文本：`red → RED`，`green → GREEN`，`countdown_green → GREEN`，`countdown_blank` / `none` / `unknown` / `occluded-heavy → UNCERTAIN`。
- `CROSS`、`go`、`go-ahead`、`可以过街`、`现在走` 都不是状态。解析时落到 `UNCERTAIN`。`TrafficLightDecision.auto_go` 不能为 true。

`TrafficLightThreeState.predict(frame_bgr)` 返回 `(state, confidence)`。这是 **HSV / OpenCV 颜色 ROI 基线**，只给离线脚手架用，不是最终检测器：

- OpenCV HSV（色相 0–179）。
- 红：色相 `[0, 12]` 或 `[168, 179]`，饱和度 ≥ 70，明度 ≥ 70。
- 绿：色相 `[40, 95]`，饱和度 ≥ 70，明度 ≥ 70。
- 有色像素少于 40，或优势颜色不到红+绿总量的 75%，输出 `uncertain`。
- 置信度是优势占比；没有有色团块时为 0。蓝、黄、灰、红绿混在一起都保持不确定。

基线不输出第四类，也不输出“可以过街”。连续帧确认仍由原来的 `TrafficLightFilter` 做：未确认、低置信、不确定观测都回到 `uncertain`。该模块**尚未接入主 pipeline**。接入之前，设备端保持 `uncertain` 或静默，不许用“看起来像绿灯”代替。

离线评测：

```bash
cd pc
python -m blindnav_pc.eval_traffic_light \
  --ann data/eval/imvisible/testing_file.csv \
  --images data/eval/imvisible/extracted/PTL_Dataset_876x657 \
  --out out/w1_traffic_light
```

`--images` 可以是已解压目录，也可以只是尚未解压的 tar 路径。图像不在时仍写 `metrics.json` 和中文 `summary.md`，内容是类别计数和混淆矩阵占位。计分规则：`UNCERTAIN` 不计成 `GREEN`；低于 `--min-green-confidence`（默认 0.6）的绿灯在计分前改成不确定；缺图样本不计成绿灯。数据在 `pc/data/`（gitignore），本协议不要求下载数据集。

## 5. 已有 stub，本协议直接沿用

- ShadowWalk：`pc/blindnav_pc/shadowwalk.py`。预定义走廊加现有分割 mask 和外部位姿。覆盖率低于 0.5、偏离超过半宽的 80%、航向偏差大于 30°、无位姿或可见点不足 → `UNKNOWN` / `STOP`。不定位、不规划、不控制。
- Last10m：`pc/blindnav_pc/last10m.py`。10 m 内最多 3 个门候选。距离未知默认拒答。
- 行走安全状态机和最终指令守卫：`pc/blindnav_pc/safety.py`、`pc/blindnav_pc/pipeline.py`。`UNKNOWN` 与 `BLOCKED` 都不会产出 `FORWARD`。

## 6. 非目标

- 不做开放道路自动过街，不做 auto-go，不说“可以过街”或“现在走”。
- 不把 1.2 m + 相对深度说成可靠的米制 BEV，也不用它作为安全结论。
- 不用云端 VLM 做实时走 / 不走。
- 不引入 ARCore 或 LiDAR，也不在协议里假设它们存在。
- 不把 HSV 颜色基线当作最终红绿灯模型，不把它接进主链路来放行。
- 不把 SANPO 上的 FalseClear 写成路缘召回或近场召回已经达标。
- 本轮不改 Android Kotlin，不改 `main`。
