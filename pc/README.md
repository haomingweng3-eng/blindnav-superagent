# BlindNav PC 闭环 Demo（pc-v0.1 分支，含 P0 修订）

在 PC 上把"视频/摄像头 → 可行走分割 → 单目相对深度 → **地面标定成米制** → 鸟瞰栅格(BEV) → VFH+ 规划 → 去抖策略 → 中文指引"整条链路跑通，
并带一个 2D 运动学仿真用来在没有真机数据时验证闭环（绕障、回正、死路停止）。**这是骨架，不是产品**：见文末"已知限制"。

```
视频帧 ──┬─ Segmenter(默认 SegFormer-B0 ADE20K) ──► 可行走 mask ─┐
         └─ Depth-Anything-V2-Small ──► 相对逆深度 q ──────────────┤
IMU 重力 / 姿态(可选) + 相机高度 ──► 地面标定 a,b ◄───────────────┘
     D = 1/(a·q+b) ──► 反投影 ──► 重力对齐水平系 ──► 单帧证据 (free_cnt, occ_cnt)
     姿态(陀螺 Δyaw) + 步伐事件×L_step ──► EgoTracker ──► BEVMemory 世界系栅格（旋转+平移 warp 后衰减）
     ──► 三态 BEV(UNKNOWN/FREE/OCCUPIED, 0–4 m × ±1.5 m) ──► VFH+ (±35°，不通则 ±50/±60°，再不通 STOP)
     ──► WalkingSafetyPolicy(去抖/STOP) ──► BehaviorMachine(CRUISE→AVOID_*→PASSING→RECENTER→CRUISE)
     ──► FORWARD/LEFT/RIGHT/STOP/RECENTER(+OBSERVE)
```

## 安装

```bash
cd pc
python -m venv .venv && source .venv/bin/activate        # Python >= 3.10
pip install torch --index-url https://download.pytorch.org/whl/cpu
pip install -r requirements.txt
pytest -q                                                # 137 个测试，不联网、不下载模型、不需要数据集
```

首次 `run` 会从 Hugging Face 下载两个模型（约 100 MB，缓存在 `~/.cache/huggingface`，**不入库**）：
`depth-anything/Depth-Anything-V2-Small-hf`、`nvidia/segformer-b0-finetuned-ade-512-512`。

## 运行

```bash
# 1) 在视频上跑（无界面，保存叠加视频 + 日志 + 指标）
python -m blindnav_pc run --video my_walk.mp4 --no-gui --save-video --out out/run1
# 带 IMU / 相机内参 / 相机高度 / 每 3 帧处理 1 帧 / 标注
python -m blindnav_pc run --video my_walk.mp4 --imu imu.csv --camera camera.json --height 1.35 \
       --stride 3 --labels labels.csv --out out/run2

# 1b) 带自运动输入（姿态 + 步伐；BEV 记忆补偿与回正都靠它）
python -m blindnav_pc run --video my_walk.mp4 --orientation orientation.csv --steps steps.csv --cam-axes opengl --no-gui

# 2) 摄像头（窗口里按 q / Esc 退出）
python -m blindnav_pc run --webcam 0

# 3) 2D 仿真（7 个场景），可存逐帧日志
python -m blindnav_pc sim all --log out/sim
python -m blindnav_pc sim right_free --memory full      # oracle | full | rot_only(默认) | none
python -m blindnav_pc sim right_free --memory none --blind 1.5   # 大盲区+无自运动：已知会撞（见限制）

# 4) 从日志重新算指标
python -m blindnav_pc metrics out/run1/log.jsonl --labels labels.csv
```

常用选项：`--seg hf | hf:<model_id> | onnx:<path> | torchscript:<path> | const`，`--depth-size 252`（深度输入短边，14 的倍数，越小越快），
`--config cfg.json`（覆盖 `config.py` 里任意参数，如 `{"bev": {"cell_m": 0.05}}`），`--max-frames`、`--stride`。
输出目录：`log.jsonl`（逐帧）、`metrics.json`、`overlay.mp4`（左：原图+mask+深度小图+指令；右：BEV、候选航向、选中航向、碰撞走廊）。
指引只**打印**（"继续 / 稍向左 / 稍向右 / 停下 / 向左回正 / 向右回正 / 请稍停，正在观察"），没有 TTS。

### ShadowWalk 虚拟走廊 stub
预定义走廊（`straight60` / `left_arc80` / `right_arc100`，宽 2.5 m）接到现有分割 mask，输出
`STRAIGHT/LEFT/RIGHT/STOP/UNKNOWN`；需要外部位姿（PDR/里程计），看不清或偏离走廊就停，不发“可走”。

```bash
.venv/bin/python tools/shadowwalk_demo.py --corridor straight60 --frames 12 --drift 0.9
.venv/bin/python -m pytest tests/test_shadowwalk.py -q
```

真实分割 mask（Blind-road GT label）离线冒烟：无深度网络时用平地面几何生成米制深度，外部位姿默认合成；
逐帧打印 `STRAIGHT/LEFT/RIGHT/STOP/UNKNOWN`，可接 `--pose-csv`：

```bash
.venv/bin/python tools/shadowwalk_real_eval.py --limit 6
.venv/bin/python -m pytest tests/test_shadowwalk_real.py -q
```

### 红绿灯 3 态 stub（`traffic_light.py`）
输出域只有 `green / red / uncertain`；连续 `min_confirm_frames` 帧一致才确认，单帧/低置信/倒计时空白一律
`uncertain`。**`auto_go` 恒为 false**：绿灯也只提示“由您决定”，不构成过街许可。

```bash
.venv/bin/python tools/traffic_light_demo.py --limit 12
.venv/bin/python -m pytest tests/test_traffic_light.py -q
```

ImVisible 标签映射：`mode` 0=red、1/2=green、3/4=uncertain。

### Last10m 门候选 stub（`last10m.py`）
解析 Entrances YOLO 标签（`class cx cy w h`），按框底与平地面估计距离，只保留 10 m 内候选并按距离排序，
**最多选 3 个**；无候选/低置信/超距/截断处难分先后时直接拒答。不做接近控制。

```bash
.venv/bin/python tools/last10m_demo.py --limit 15
.venv/bin/python -m pytest tests/test_last10m.py -q
```

边界与真机门槛见 `../docs/REAL_DEVICE_PROTOCOL.md`：受控 demo，不是独立安全导航。

### 输入文件格式
- `camera.json`：`{"fx":..,"fy":..,"cx":..,"cy":..,"width":1920,"height":1080}`（`width/height` 可选，与视频尺寸不同时自动缩放）。缺省按水平 FOV 70° 估计。
- `imu.csv`：表头 `t_ms,gx,gy,gz`，**重力方向（指向下）在相机坐标系**（OpenCV：x 右、y 下、z 前）下的向量，按最近时间戳取值，额外列忽略。
  也可直接给 Android logger 的原始格式 `t_ns,sensor_type,x,y,z,w`：只取 **TYPE_GRAVITY** 行，ns→ms，并把设备系 (x 右、y 上、z 朝屏幕外) 映射到相机系 `(gx,-gy,-gz)`。
  重力来源优先级固定为 **TYPE_GRAVITY → orientation 四元数 → `config.camera.pitch_deg`**（默认向下俯 15°）——**俯仰角错了，米制尺度就会错**。
- `orientation.csv`：`t_ms,qx,qy,qz,qw`（相机→世界，世界 Z 向上，TYPE_GAME_ROTATION_VECTOR 风格）或 `t_ms,heading_deg`（仅航向，右为正）。`--cam-axes opencv|opengl` 指定相机轴约定。
  提供四元数时同时给出重力方向，不必再给 imu.csv。
- `steps.csv`：`t_ms`，每个步伐事件一行（TYPE_STEP_DETECTOR）。没有则只做旋转 warp。
- `labels.csv`（可选）：`t_ms,should_stop`（旧格式），或 `t_ms,action[,preferred_side]`，`action ∈ FORWARD|LEFT|RIGHT|STOP`，`preferred_side ∈ left|right|center|空`。

## 核心算法：不用 ARCore/LiDAR 的地面标定
Depth-Anything 输出的是**仿射不变逆深度** q（只差一个未知的 a、b：`1/Z = a·q + b`）。已知相机离地高度 h（默认 1.20 m）和重力方向就能解出 a、b：
1. `up = −normalize(g_cam)`。
2. 取"腐蚀后的可行走 mask ∩ 图像下部 ROI"里的像素，射线 `r = K⁻¹[u,v,1]`；仅当 `upᵀr < −eps` 时地面深度 `Z = −h/(upᵀr)`（z 深度，非射线长度）。
3. 对 `a·q + b = 1/Z` 做 RANSAC（2 点采样）+ 内点最小二乘；输出内点比例、相对残差、置信度
   `= 内点比 × 1/(1 + 残差rms/rel_tol) × min(n/min_pixels,1)`。残差是 `1/Z` 的相对误差，与逐像素 AbsRel 同量纲；
   单元测试（`tests/test_ground.py`）验证置信度与 AbsRel 的 Spearman 相关为负（噪声越大、异常值越多，置信度越低）。
   **TYPE_GRAVITY 输入的 up 不被视觉平面替换**；只有 attitude（姿态四元数 / 固定俯仰）与视觉地面法线夹角
   `> prefer_visual_up_deg` 时才回退到"视觉平面 + 已知相机高度"，夹角很小时两者融合。视觉平面用 RANSAC 拟合，
   少量深度异常值不会伪造出大的姿态偏差。
4. 全图米制深度 `D = 1/(a·q+b)`，分母 ≤ 0 → NaN（**未知，绝不当作自由空间**）；玻璃/无深度同理。
5. 反投影 → 用重力旋到水平系（forward = 相机 z 轴投影到水平面，right = forward × up）→ 栅格化单帧证据：地面高度≈0 且 mask 可走 = FREE 证据；
   高度 0.15–2.0 m = OCCUPIED；低于 −0.25 m（台阶下沉）= OCCUPIED；其余 UNKNOWN。
6. 置信度低 → 复用上一帧 a,b（最多 10 帧）→ 仍不行则"降级"：策略进入 OBSERVE（≥0.5 s），≥2 s 变 STOP。

### 近场盲区（blind_m）：三类，不再把看不见的近场当自由
- **当前人体足迹**（脚下 0.5 m 宽 × 0.35 m）= 已知 FREE（人正站在上面）。
- **相机看不到的近场** = UNKNOWN（以前 V0.1 把它当 FREE，是错的）。
- **历史 FREE** 只有经自运动补偿 warp 过来的才保留。
规划器对 UNKNOWN 的处理：盲区内的 unknown 不算障碍、但按 `delta × unknown 占比` 扣分；允许从足迹"走出去"，所以不会因为近场全 unknown 而误判死路。

### BEV 记忆 + 自运动 warp（`bev.py` / `ego.py`）
`BEVMemory` 是**世界系**栅格（相对 episode 参考系 psi_ref），每帧：
1. 旋转：按姿态相对 psi_ref 的 Δyaw；2. 平移：步伐事件数 × `L_step`（`ego.step_length_m`，默认 0.70 m），沿当前航向；3. 再衰减（有自运动 0.9/帧）；
4. 没检测到步伐 → 只旋转（rot_only）；运动不可靠（帧间隔 > 1.5 s 或单帧偏航 > 60°）→ **丢弃旧历史**；完全没有姿态输入 → 衰减到 0.3/帧，等于只看当前帧。
5. **防幽灵障碍**：warp 后旧占据证据遇到当前帧明确的 FREE 观察时按 `free_clears_occ_ego` 削减；当前帧权重始终最大。测试 `test_bev.py`：走路者右移后旧障碍不会在新位置重复出现。

### 行为状态机（`behavior.py`）
`CRUISE → AVOID_LEFT/RIGHT → PASSING → RECENTER → CRUISE`，STOP 随时可抢占（安全覆盖）。
- 记录 episode 参考线 x=0 与 psi_ref；用 PDR（陀螺航向 + 步伐×L_step）估计横向偏移 `x_hat`、前向进度 `z_hat`。
- 通过障碍（PASSING，前向进度 ≥ `pass_len_m`=1.3 m，无步伐时超时 4.5 s）后，目标 = `(0, z_hat + lookahead)`（lookahead 1.5 m），目标方位角限幅 20°；
  VFH 只负责在该目标附近挑安全航向，**单帧不同意不能取消状态**。
- 退出：`|x_hat| < 0.10 m` 且航向误差 < 6° 且中线连续 3 帧畅通 → CRUISE；8 s 超时则重新锚定参考线。
- 没有姿态输入（`--orientation` 缺省）→ 不回正（只绕障）。

### 导航安全状态（`safety.py`，P0）
每帧输出 `CLEAR / BLOCKED / UNKNOWN`。**`UNKNOWN` 表示当前无法确认前方是否可通行，绝不会映射成 FORWARD**
（输出降级为 OBSERVE/STOP）。触发条件：
- 输入超时/丢帧：帧间隔 > `safety.input_timeout_ms`（默认 500 ms）或时间戳倒退；
- 地面标定不可信：`fit.ok=False`、置信度 < `min_confidence`、残差 > `max_residual`，
  或姿态与视觉地面法线冲突（`visual_fallback` 或夹角 > `max_attitude_visual_deg`）；
- 前方走廊（`corridor_len_m` × ±`corridor_half_width_m`）已知栅格（FREE/OCCUPIED）占比 < `min_corridor_coverage`。

`UNKNOWN → CLEAR` 需要连续 `unknown_recovery_frames` 个可靠帧（默认 3，迟滞），单帧恢复不会放行。
`BLOCKED` 是**已确认**的前方障碍，仍允许向空的一侧转。提示语区分障碍与无法确认：
`前方有障碍，请停下`（/稍向左、稍向右）与 `前方情况无法确认，请暂停并使用盲杖探查`。
状态切换通过 `logging`（logger `blindnav_pc.safety`）带 `t_ms` 记录；逐帧日志新增
`safety_state, safety_reasons, corridor_coverage`。

### 高度分层 + 跌落脚点（`layers.py`，P0）
每帧把水平系点云按**相对局部地面**的高度分层：ground（|h|≤0.05 m）、low（0.05–0.15 m）、
body（0.15–0.8 m）、head（0.8–2.0 m）；地面以下 `h < -drop_depth_m`（默认 −0.20 m）是跌落证据。
各层占用保留在 BEV 栅格上（`BEVMemory` 的三态栅格不变）。安全状态机只看前方可行走走廊
（默认 2.0 m × ±0.5 m）：
- **head / body 层有点** → `BLOCKED`；**low 层"大"**（≥3 格或横向 ≥0.30 m）→ `BLOCKED`；小的 low 障碍
  只有在其前方地面确实被看到时才忽略，否则 `UNKNOWN`（不静默当作安全）。
- **DROP_RISK**（台阶下沉/坑洞）需要同时满足：可行走地面边界 + 局部平面残差（低于局部地面 ≥0.20 m）
  + 深度不连续（或台阶下方地面在遮挡缺口之后仍被看到），且连续 `drop_confirm_frames`（默认 3）帧。
  **确认前一律 `UNKNOWN`**；确认后在证据短暂丢失时保持 `drop_hold_frames` 帧，避免闪烁。
- **地面边界之后没有可靠深度** → `UNKNOWN`（`drop_edge_unobserved`）：绝不断言成坑洞，也绝不放行。
`UNKNOWN`/`BLOCKED` 仍然永远不会映射成 `FORWARD`。逐帧日志新增
`layer_low_cells, layer_body_cells, layer_head_cells, drop_risk_cells, drop_unconfirmed_cells,
drop_unobserved_cells, hazard_reasons`。

### 规划器（`planner.py`）
- 默认 ±35°（5° 步长）；全部不通时依次放宽到 ±50°、±60° 再 STOP。放宽后的航向必须在视野内**整条射线都看得到是 FREE** 且前向进展 ≥ 1.0 m
  （默认 ±1.5 m 的 BEV 里 ±60° 基本不可行，需要加大 `bev.lateral_half_m`，例如 3.0；这是诚实的限制）。
- 膨胀 = 人体宽度 + 余量：`body_width_m=0.5`、`margin_m=0.15` → 扫掠半宽 `half_width_m = 0.25+0.15 = 0.40 m`。
- **膨胀后净空（clearance after inflation）** = 直线路径中心线到任一 OCCUPIED 栅格中心的最小距离 − `half_width_m`；≥0 表示膨胀后的足迹没碰到障碍，<0 表示碰到；无障碍为 +∞。
  每帧日志/评测里的 `clearance_after_inflation_m` 用这个定义。注意：仿真表里的"最小净空"是**真实几何**下步行者圆盘边缘到障碍的最近距离（只减身体半径 0.25 m，不含 0.15 m 余量），与它**不是同一个数**。
- `score = α·clear + β·free_width − γ·航向误差 − δ·unknown占比`；所有航向通行距离 < 1.5 m = 死路。
- 策略（移植自 Android `WalkingSafetyPolicy.kt`）：停止证据需连续 ≥2 帧且持续 300 ms 才 STOP，STOP 保持 800 ms；左右转需连续 3 帧且停留 400 ms；回到直行需 2 帧。

每帧日志（`log.jsonl`）含：`a, b, inlier_ratio, residual, n_ground_px, confidence, reused/degraded, safety_state, safety_reasons, corridor_coverage, layer_low_cells, layer_body_cells, layer_head_cells, drop_risk_cells, drop_unconfirmed_cells, drop_unobserved_cells, hazard_reasons, left/center/right_clear_m, corridor_blocked, dead_end, command, text, stop_reason, latency_ms(及分阶段)`。

思路来源：Tian et al., ICRA 2021（arXiv:2101.05995）；DRISHTI 仅作参考，**本实现为独立重写，未复制其代码**。

## 仿真 `pc/sim/`
2D 运动学（步行 1 m/s，转向 20°，回正 10°，最大偏航角速度 90°/s），走廊里放长方体障碍，每个仿真步渲染一张"传感器视角"的 BEV
（±35° 视锥、近场盲区 `--blind`、遮挡后为 unknown），喂给**同一个** `VFHPlanner`/`BEVMemory`/`BehaviorMachine`/`WalkingSafetyPolicy`，输出再驱动步行者。
自运动输入来自仿真的"陀螺航向 + 步伐事件"。仿真的步伐事件是合成的离散事件，所以默认 `--memory rot_only`（只信任陀螺）；
显式 `--memory full` 才用步伐平移，`none` 无自运动，`oracle` 只看当前帧。

| 场景 | 期望 | 实测（`python -m blindnav_pc sim all`，memory=full，blind=0.6 m） |
|---|---|---|
| clear | 全程 FORWARD | FORWARD×116，最小净空 1.00 m |
| right_free / left_free | 绕向空的一侧 → PASSING → RECENTER → CRUISE | FORWARD×17→RIGHT(LEFT)×16→FORWARD×25→RECENTER×20→FORWARD×39，最小净空 0.30 m，终点偏离 ±0.14 m，无碰撞 |
| dead_end | 全宽挡墙 → STOP | FORWARD×39 后 STOP，停在墙前，无碰撞 |
| slalom | 两个障碍分居两侧：绕→回正→再绕 | 无碰撞，最小净空 0.18 m，终点 x=−0.09 m |
| gap_ok / gap_too_narrow | 缝隙够宽过、不够宽停 | gap_ok 直行通过（净空 0.25 m）；gap_too_narrow → STOP |

附加测试（`tests/test_sim.py`/`test_behavior.py`/`test_ego.py`）：PDR 步长误差 ±15% 仍无碰撞；陀螺漂移 1°/s 无碰撞（终点横向偏离 0.67–0.8 m，说明参考线会漂）；
大盲区 1.5 m 下 full 记忆通过，**rot_only / none 会撞——这是已记录的限制，不是已解决**。

## 指标
`python -m blindnav_pc metrics log.jsonl --labels labels.csv` 输出：
- **误停率** = 标注"不该停"的帧里输出 STOP 的占比；**漏停率** = 标注"该停"的帧里没输出 STOP 的占比；
- **unsafe-forward 危险前进率** = 标注要求 STOP/LEFT/RIGHT 时仍输出 FORWARD（RECENTER 计作 FORWARD）的占比（另给仅 STOP 标注的口径）；
- **wrong-side 选错侧率** = 输出 LEFT/RIGHT 与标注 `preferred_side`（或 action 的侧）相反；
- **oscillation** = 2 s 内左右反向切换次数（次/分钟）；另有指令翻转次数、任意 2 s 窗口内最多翻转数；
- **延迟** p50 / p95 / 均值（ms）。
无对应标注时该项为 N/A。

## 评测骨架 `pc/eval/`（SANPO）
被测系统只看 **RGB + 姿态派生的重力/航向（IMU 代理）**；GT 深度/分割只用于打分。

| 模块 | 内容 |
|---|---|
| `eval/sanpo.py` | `SanpoSession`：Synthetic 与 Real；Real 优先用 `fixed_camera_poses.csv`；读 float16.gz 深度（头部 H,W）、分割 id（PNG 第 3 通道）、位姿；`gravity_check` 对照 GT 地面法线核对位姿约定 |
| `eval/depth_metrics.py` | AbsRel、RMSE、δ1、0.3–5 m 近场分段、置信度-误差 Spearman |
| `eval/bev_eval.py` | Oracle BEV（GT 深度+GT 分割，**同一个**栅格化/阈值/足迹规则）vs Pred BEV：false-free 率、漏检率、误报率、最近障碍距离误差、走廊宽度误差；Oracle vs Pred 规划动作对比（一致率、unsafe-forward、false-stop、wrong-side、混淆矩阵） |
| `eval/ego_eval.py` | 自运动：gyro-only vs gyro+step 的 PDR 误差（可加陀螺偏置）；记忆预测测试 none/rot_only/full（障碍召回、自由召回、幽灵率） |
| `eval/fixture.py` | 不用下载数据的 SANPO 格式合成夹具（解析场景，用于测试与冒烟） |
| `eval/ub_visiogeoloc.py` | uB-VisioGeoloc 加载器**桩**（`NotImplementedError`，docstring 记了数据集事实与 TODO） |

```bash
cd pc
python -m eval.run --fixture --out out/eval_fixture                     # 无需数据，几秒
python -m eval.run --session data/sanpo_synth/sanpo-synthetic/<session_id> --camera camera_head --frames 0:30:1 --out out/eval_synth
python -m eval.run --session ./data/sanpo_real/sanpo-real/<session_id> --camera camera_chest --frames 200:220:1 --out out/eval_real
# 诊断开关：--gravity gtplane（用 GT 地面算重力，隔离姿态误差）、--seg gt / --depth gt（分量换成 Oracle）、--bev-forward 8（默认 8 m）
```
输出 `eval.json`（逐帧+汇总）和中文摘要。**`--gravity gtplane/--seg gt/--depth gt` 是消融，不是系统输入；报告里会标明。**

### 如何只下载一小块 SANPO（不要拉全量，全库约 6 TB）
数据在公共桶 `gs://gresearch/sanpo_dataset/v0`（CC BY 4.0），无需登录，可用 `gsutil -m cp` 或普通 HTTPS：
```bash
# 列目录（JSON API）
curl -s "https://storage.googleapis.com/storage/v1/b/gresearch/o?prefix=sanpo_dataset/v0/sanpo-synthetic/&delimiter=/"
# 取单个文件：https://storage.googleapis.com/gresearch/sanpo_dataset/v0/<路径>
# 一个 session 的目录：sanpo-synthetic/<id>/{description.json, camera_head/{camera_poses.csv, left/{video_frames,depth_maps,segmentation_masks}/<frame>.*}}
# Real：sanpo-real/<id>/camera_chest/{camera_poses.csv, fixed_camera_poses.csv, left/...}
# 另需根目录的 labelmap.json（分割 id→类名）。只下载前 20–30 帧即可冒烟。
```
本仓库没有提交任何数据（`pc/data/` 被 gitignore）。

### 真实数据上发现的约定与坑（务必读）
- SANPO README 写位姿为"Y-up"，**实测不符**：Synthetic 与 Real 的 camera_poses 都是 相机→世界、OpenCV 相机轴、**Z-up** 世界（用 GT 地面法线核对）。`SanpoSession(world_up, cam_axes)` 可配置。
- Synthetic 位姿与 GT 地面法线夹角约 1.2°（6 帧）；**Real 约 12–14°（7 帧，仅 1 个 session）**。该误差使地面标定在 Real 上全部失败（见下），所以真实数据上"姿态当重力"不够准。
- SANPO **没有原始 IMU**：姿态用位姿朝向代替，步伐事件由位姿轨迹合成（`step_times_ms`）。
- Real 的"GT"深度是 CREStereo 伪真值（720×1280，对 1242×2208 图像），不是传感器真值。
- 相机高度由 GT 地面平面拟合得到：Synthetic head≈1.85 m，Real chest≈1.35 m。Synthetic head 相机水平朝前，**近 3.5 m 内看不到地面**，所以 4 m 的 BEV 里几乎全是 unknown，评测默认用 8 m。

## 模型与许可证
| 组件 | 许可证 | 备注 |
|---|---|---|
| Depth-Anything-V2-**Small** | Apache-2.0 | 代码只允许 Small；Base/Large/Giant 是 CC-BY-NC，`DepthEstimator` 默认拒绝（`BLINDNAV_ALLOW_NONSMALL_DEPTH=1` 可在非商用实验里覆盖） |
| `nvidia/segformer-b0-finetuned-ade-512-512` | NVIDIA 非商业研究许可（HF 上标记 "other"） | **仅用于 Demo；商用前必须换成团队自己的 v5 模型**，接口见 `segmentation.py`（`onnx:` / `torchscript:`） |
| PyTorch / transformers / OpenCV / Pillow | BSD / Apache-2.0 / Apache-2.0 / HPND | |
| YOLOv8（AGPL-3.0） | — | 本骨架**没有**使用 |
| 示例视频 | CC BY-SA 4.0 | Wikimedia Commons "Harlow Museum & Walled Gardens, walking tour video"（截取前几秒做冒烟测试，**未入库**） |

## 包集成 / 离线评测脚本

历史 real-eval 已合并到仓库根 `tools/eval/`（见该目录 README）。`pc/eval/` 仍是闭环 PC harness。

公开缓存：本机 HF hub 已有 Depth-Anything-V2-Small 与 SegFormer-B0-Cityscapes 基座。仍缺 UrbanEgo、微调权重(~145MB)、ASR/TTS、自定义 yolo-seg、完整 SANPO/BLV 等（见 `tools/eval/DOWNLOAD_LINKS.md`）。

BEV 记忆：当步伐为仿真/未标定 `L_step` 时，评测与真机应优先 `--memory rot_only`（或 `EgoConfig.use_steps=False`）。`use_steps=True` 仅在真实 STEP_DETECTOR + 标定步长时安全；仿真 `sim` 命令因此默认 `rot_only`，`full` 需显式指定。

## 已知限制（诚实清单）
- **没有真机数据**：真实验证只有 SANPO 的一个小样本（Synthetic 30 帧 / Real 20 帧，且 Real 那段几乎静止）。误停率/漏停率需要带标注的真实路测。
- **Real 数据上姿态派生的重力偏差 12–14°，地面标定 20/20 帧失败（系统正确降级为 STOP，但说明依赖手机 IMU 的重力必须准）**；改用 GT 地面重力后 20/20 成功。仅 1 个 Real session，结论不可外推。
- 平地假设：台阶/坡道/路肩会让地面拟合偏掉；相机高度/俯仰角错 10% 直接带来同量级距离误差。
- 高度分层与跌落脚点检测仍是栅格级 P0：多帧跌落证据**没有做自运动补偿**（走路时格子会移动，可能延后确认）；
  相机近场盲区/地面可见边界附近会保守地报 `UNKNOWN`；单帧深度噪声不会确认 DROP_RISK，但会造成短暂 `UNKNOWN`。
  low 层与地面噪声的区分依赖 `ground_band_m` 和每格最少点数，真实深度噪声下需要标定。
- 记忆预测评测里 `full`（步伐离散）在 Synthetic 上的障碍召回低于 `rot_only`，且幽灵率更高：步伐是 0.7 m 一跳的离散事件，帧间平移和真实连续运动不一致。BEV 记忆的平移精度受 PDR 限制，不要高估。
- 陀螺漂移会让回正参考线漂移（仿真 1°/s → 终点偏离 0.67–0.8 m）；大盲区 + 无步伐/姿态输入会撞（仿真已记录）。
- 加宽到 ±60° 在默认 ±1.5 m 的 BEV 里基本不可行；需要更宽的 BEV。
- 通用 ADE20K 分割对草地/地毯/玻璃的判断与团队 v5 模型会差很多；玻璃与反光地面深度不可靠。
- 相对深度的仿射模型在远处失真，因此 BEV 默认 4 m（评测 8 m），深度截断在 15 m。
- CPU（无 GPU）整条流水线约 0.3 s/帧，约 3 FPS，离实时还差一截。
- 仿真传感器理想化（无噪声），仿真通过 ≠ 真实可用。
- uB-VisioGeoloc 加载器只是桩。

## 下一步
1. 真机采集 视频 + 姿态 + 步伐 + 标注（`action,preferred_side`），量化 unsafe-forward / 误停 / wrong-side / oscillation。
2. 用真实 Android 的 TYPE_GAME_ROTATION_VECTOR + TYPE_GRAVITY 替代姿态代理，并重做 Real 重力误差检查；考虑把地面标定对重力误差做鲁棒化（联合估计倾角）。
3. 接入团队 v5 分割模型（ONNX/TorchScript 钩子已留）；评估 DA-V2-Small 的 ONNX/INT8 加速。
4. 与 Kotlin 端对齐阈值，做一致性回放测试。
