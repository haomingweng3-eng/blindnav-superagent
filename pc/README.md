# BlindNav PC 闭环 Demo V0.1

在 PC 上把"视频/摄像头 → 可行走分割 → 单目相对深度 → **地面标定成米制** → 鸟瞰栅格(BEV) → VFH+ 规划 → 去抖策略 → 中文指引"整条链路跑通，
并带一个 2D 运动学仿真用来在没有真机数据时验证闭环（绕障、回正、死路停止）。**这是骨架，不是产品**：见文末"已知限制"。

```
视频帧 ──┬─ Segmenter(默认 SegFormer-B0 ADE20K) ──► 可行走 mask ─┐
         └─ Depth-Anything-V2-Small ──► 相对逆深度 q ──────────────┤
IMU 重力(可选) / 俯仰角 + 相机高度 1.20 m ──► 地面标定 a,b ◄───────┘
     D = 1/(a·q+b) ──► 反投影 ──► 重力对齐水平系 ──► BEV(0–4 m × ±1.5 m, 衰减 0.9)
     ──► VFH+ 规划(±35°) ──► WalkingSafetyPolicy 去抖 ──► FORWARD/LEFT/RIGHT/STOP/RECENTER(+OBSERVE)
```

## 安装

```bash
cd pc
python -m venv .venv && source .venv/bin/activate        # Python >= 3.10
pip install torch --index-url https://download.pytorch.org/whl/cpu
pip install -r requirements.txt
pytest -q                                                # 47 个测试，不联网、不下载模型
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

# 2) 摄像头（窗口里按 q / Esc 退出）
python -m blindnav_pc run --webcam 0

# 3) 2D 仿真（4 个场景），可存逐帧日志
python -m blindnav_pc sim all --log out/sim
python -m blindnav_pc sim right_free

# 4) 从日志重新算指标
python -m blindnav_pc metrics out/run1/log.jsonl --labels labels.csv
```

常用选项：`--seg hf | hf:<model_id> | onnx:<path> | torchscript:<path> | const`，`--depth-size 252`（深度输入短边，14 的倍数，越小越快），
`--config cfg.json`（覆盖 `config.py` 里任意参数，如 `{"bev": {"cell_m": 0.05}}`），`--max-frames`、`--stride`。
输出目录：`log.jsonl`（逐帧）、`metrics.json`、`overlay.mp4`（左：原图+mask+深度小图+指令；右：BEV、候选航向、选中航向、碰撞走廊）。
指引只**打印**（"继续 / 稍向左 / 稍向右 / 停下 / 向左回正 / 向右回正 / 请稍停，正在观察"），没有 TTS。

### 输入文件格式
- `camera.json`：`{"fx":..,"fy":..,"cx":..,"cy":..,"width":1920,"height":1080}`（`width/height` 可选，与视频尺寸不同时自动缩放）。缺省按水平 FOV 70° 估计。
- `imu.csv`：表头 `t_ms,gx,gy,gz`，**重力方向（指向下）在相机坐标系**（OpenCV：x 右、y 下、z 前）下的向量，按最近时间戳取值，额外列忽略。
  没有 IMU 时用 `config.camera.pitch_deg`（默认相机向下俯 15°）和 roll=0 构造重力——**俯仰角错了，米制尺度就会错**。
- `labels.csv`（可选）：`t_ms,should_stop`（1=此刻应停），用于误停率/漏停率。

## 核心算法：不用 ARCore/LiDAR 的地面标定
Depth-Anything 输出的是**仿射不变逆深度** q（只差一个未知的 a、b：`1/Z = a·q + b`）。已知相机离地高度 h（默认 1.20 m）和重力方向就能解出 a、b：
1. `up = −normalize(g_cam)`。
2. 取"腐蚀后的可行走 mask ∩ 图像下部 ROI"里的像素，射线 `r = K⁻¹[u,v,1]`；仅当 `upᵀr < −eps` 时地面深度 `Z = −h/(upᵀr)`（z 深度，非射线长度）。
3. 对 `a·q + b = 1/Z` 做 RANSAC（2 点采样）+ 内点最小二乘；输出内点比例、相对残差、置信度 `= 内点比 × (1 − 残差rms/rel_tol)`。
4. 全图米制深度 `D = 1/(a·q+b)`，分母 ≤ 0 → NaN（**未知，绝不当作自由空间**）；玻璃/无深度同理。
5. 反投影 → 用重力旋到水平系（forward = 相机 z 轴投影到水平面，right = forward × up）→ 栅格化：地面高度≈0 且 mask 可走 = FREE；
   高度 0.15–2.0 m = OCCUPIED；低于 −0.25 m（台阶下沉）= OCCUPIED；其余 UNKNOWN。证据逐帧 ×0.9 衰减，占据优先于自由。
6. 置信度低 → 复用上一帧 a,b（最多 10 帧）→ 仍不行则"降级"：策略进入 OBSERVE（≥0.5 s），≥2 s 变 STOP。
7. 规划：±35°（步长 5°）的射线，按身体半宽 0.2 m + 余量 0.1 m 扫掠；
   `score = α·clear + β·free_width − γ·航向误差 − δ·unknown占比`；所有航向通行距离 < 1.5 m = 死路。相机盲区（按底部中心像素的平地交点算出前向距离 +0.1 m，下限 0.6 m）里的 unknown 不算障碍，其余 unknown 连续 0.5 m 即截断通行距离。
8. 策略（移植自 Android `WalkingSafetyPolicy.kt`）：停止证据需连续 ≥2 帧且持续 300 ms 才 STOP，STOP 保持 800 ms；
   左右转需连续 3 帧且停留 400 ms；回到直行需 2 帧。新增 RECENTER：绕障后直行 1200 ms（避免往视野外的障碍物回拐）再按累计横向偏移（>0.25 m）回正，<0.10 m 结束。
   RECENTER 期间策略把 ±10° 目标航向反馈给规划器；规划器不同意（被挡/另一侧）就取消回正。

每帧日志（`log.jsonl`）含：`a, b, inlier_ratio, residual, n_ground_px, confidence, reused/degraded, left/center/right_clear_m, corridor_blocked, dead_end, command, text, stop_reason, latency_ms(及分阶段)`。

思路来源：Tian et al., ICRA 2021（arXiv:2101.05995）；DRISHTI 仅作参考，**本实现为独立重写，未复制其代码**。

## 仿真 `pc/sim/`
2D 运动学（步行 1 m/s，转向 20°，回正 10°，最大偏航角速度 90°/s），走廊里放长方体障碍，每个仿真步渲染一张"传感器视角"的 BEV
（±35° 视锥、0.6 m 盲区、遮挡后为 unknown），喂给**同一个** `VFHPlanner` 和 `WalkingSafetyPolicy`，输出再驱动步行者，形成闭环。

| 场景 | 期望 | 实测（`python -m blindnav_pc sim all`） |
|---|---|---|
| clear | 全程 FORWARD | FORWARD×116，到终点 |
| right_free | 中间偏左障碍、右侧更空 → RIGHT → 绕过 → RECENTER → FORWARD | FORWARD→RIGHT×13→FORWARD→RECENTER→FORWARD，最小净空 0.18 m，终点偏离 +0.08 m |
| left_free | 镜像 → LEFT | 镜像，同上 |
| dead_end | 全宽挡墙 → STOP | FORWARD×40 后 STOP，停在墙前约 1.0 m，无碰撞 |

仿真里把走廊偏航角（陀螺仪式的"相对行走轴的航向偏移"）传给规划器，用来把"目标航向"换算到当前视角；真实视频流水线暂时没有这个量（V0.1 视为 0）。

## 指标
`python -m blindnav_pc metrics log.jsonl --labels labels.csv` 输出：
- **误停率** = 标注"不该停"的帧里输出 STOP 的占比；**漏停率** = 标注"该停"的帧里没输出 STOP 的占比（两者无标注时为 N/A）；
- **指令翻转次数**（相邻帧 command 变化次数；另给 次/分钟、任意 2 s 窗口内最多翻转数）；
- **延迟** p50 / p95 / 均值（每帧总耗时，ms；`log.jsonl` 里另有 seg/depth/geometry 分阶段）。

## 模型与许可证
| 组件 | 许可证 | 备注 |
|---|---|---|
| Depth-Anything-V2-**Small** | Apache-2.0 | 代码只允许 Small；Base/Large/Giant 是 CC-BY-NC，`DepthEstimator` 默认拒绝（`BLINDNAV_ALLOW_NONSMALL_DEPTH=1` 可在非商用实验里覆盖） |
| `nvidia/segformer-b0-finetuned-ade-512-512` | NVIDIA 非商业研究许可（HF 上标记 "other"） | **仅用于 Demo；商用前必须换成团队自己的 v5 模型**，接口见 `segmentation.py`（`onnx:` / `torchscript:`） |
| PyTorch / transformers / OpenCV / Pillow | BSD / Apache-2.0 / Apache-2.0 / HPND | |
| YOLOv8（AGPL-3.0） | — | 本骨架**没有**使用 |
| 示例视频 | CC BY-SA 4.0 | Wikimedia Commons "Harlow Museum & Walled Gardens, walking tour video"（截取前几秒做冒烟测试，**未入库**） |

## 已知限制（诚实清单）
- **没有真机数据**：全部验证来自合成数据单元测试 + 2D 仿真 + 1 段网络步行视频的冒烟运行。误停率/漏停率需要带标注的真实路测视频才能算。
- 平地假设：台阶/坡道/路肩会让地面拟合偏掉（置信度会下降，但不保证）；相机高度/俯仰角错 10% 会直接带来约同量级的距离误差。
- 无 IMU 时俯仰角靠默认值 15°，手持晃动会引入误差；V0.1 没有自运动补偿（BEV 记忆只用来平滑几帧）。
- 摄像头近处盲区（俯仰 15° 时约 1.5 m 内看不见地面）被当作"未知但不拦路"，所以脚下突然出现的东西看不到。
- 通用 ADE20K 分割会把草地/泥地、地毯当成/不当成可走，室外与团队 v5 模型表现差异很大；玻璃与反光地面的深度不可靠（只做到"未知不当自由"）。
- 相对深度的仿射模型在远处会失真（逆深度接近 0 时 1/(a·q+b) 极不稳定），因此 BEV 最远只取 4 m ，深度截断在 15 m。
- CPU 速度（8 核，无 GPU）：整条流水线约 0.3 s/帧（SegFormer ≈ 0.12–0.27 s，DA-V2-Small ≈ 0.09–0.26 s，几何+规划+策略 ≈ 8 ms），整体约 3–3.7 FPS；离"实时"还差一截，需要缩输入/GPU/ONNX/量化。
- 仿真传感器是理想化的（无噪声），所以仿真通过 ≠ 真实视频可用；RECENTER 偶尔出现"启动 1 帧即被规划器取消"的抖动（见仿真时间线）。

## 下一步
1. 用真机采集 imu.csv + 视频，并标注 should_stop，量化误停/漏停；用 IMU 积分（或视觉里程计）给 BEV 做自运动补偿。
2. 接入团队 v5 模型（ONNX/TorchScript 钩子已留），统一"可走"类别；评估 DA-V2-Small 的 ONNX/INT8 加速。
3. 引入陀螺仪航向偏移，把仿真里用到的"相对行走轴的偏航"接入真实流水线，去掉 RECENTER 抖动。
4. 与 Kotlin 端对齐阈值，做一致性回放测试。
