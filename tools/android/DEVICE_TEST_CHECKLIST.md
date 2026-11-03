# BlindNav Android 真机测试 Checklist（第一轮站立 / 第二轮睁眼步行）

适用版本：`app/build/outputs/apk/debug/app-debug.apk`
SHA-256 `117610b41e0b4ab79bf22ae6089e6ac8dfd950ae14ab61ee7f40c3e38a85f3fa`（2026-11-01 22:47 构建，含 ARCore 影子链路 + 收口审计修正）
每次改代码后请重新 `shasum -a 256 app/build/outputs/apk/debug/app-debug.apk` 并更新这里——报告里必须写清是哪一次构建测的。

影子开关会改变相机所有权，而 debug 包的调试屏是 exported=true，所以 adb 起它必须带口令：
`--es shadow_token blindnav-shadow-2026`（`run_ar_probe.sh` 已自动带上）。

本清单只做一件事：回答"电脑上值得继续的能力，放到真实 Android 手机上能不能稳定拿到同步的相机/Pose/Depth/视觉结果"。
**不验证盲人独立行走，不做道路测试。**

## 0. 硬性前提

- 一台 ARCore 支持列表内的 Android 真机（minSdk 26，arm64-v8a）。
- 已安装 *Google Play Services for AR*（包名 `com.google.ar.core`）。没有它影子屏只会显示失败原因，不会崩。
  国行设备不预装 Google Play 商店，但**这个服务可以单独装**：先在**小米应用商店**搜 "Google Play Services for AR / 面向 AR 的 Google Play 服务"。
  装不到再判设备侧阻塞，**不要装来源不明的 ARCore APK**——那会让后面所有深度与位姿数据失去可比性。
  App 内 `ArCoreApk.requestInstall()` 在无 Play 商店的机器上通常会失败，所以这一步手动装更可靠。
- 手机用数据线连 Mac，`adb devices` 能看到设备且已授权调试。
- 全程**睁眼、有人陪同、不蒙眼、不上车流道路**。盲杖照常带、照常使用。

模拟器不能替代：本项目 `abiFilters` 只保留 `arm64-v8a`，且 ARCore 在 Apple Silicon 的 arm64 AVD 上不提供可用 session。

关于本机（Xiaomi 18 Pro）的预期，写清楚免得事后争判据：Google 官方支持列表里 **Xiaomi 17 / 17 Pro / 17 Pro Max 已在列并标注支持 Depth API**，这是很强的正面信号；但 **18 Pro 目前尚未出现在官方列表里**。所以本清单第一步的能力探针结果**优先于任何推测**：真机返回什么就记什么，支持就继续，不支持就记设备侧阻塞并换机，不靠同系列型号外推。

## 1. 一次性环境准备

```bash
cd /Users/mima0000/blindnav-app
export JAVA_HOME=/opt/homebrew/opt/openjdk@17      # 不设就是 "Unable to locate a Java Runtime"
adb devices
adb shell pm list packages com.google.ar.core      # 空 = 没装 ARCore
```

## 2. 第一轮：站立，不走路（目标 15 分钟）

```bash
tools/android/run_ar_probe.sh --full --seconds 600
```

跑起来后调试屏每 500ms 刷新一次状态。**先别做动作**：屏幕上（以及日志 `type=SESSION event=capability`）已经给出 Session 能否创建、`arcore_depth_automatic` / `arcore_depth_raw_only` / `arcore_semantic_enabled` / `camera_timestamp_source` / `arcore_camera_configs`。这五项就是第一步的设备能力探针，先抄下来再继续。

然后按顺序做下面 5 个动作，**每个动作保持 60 秒**，并在笔记本上记下开始/结束时间（后面用 `frame` 序号对齐）：

| # | 动作 | 要看什么 | 日志字段 |
|---|---|---|---|
| A | 手机静止放在桌上，镜头对着有纹理的墙面 1.5m 外 | 位姿抖动、深度抖动、深度覆盖率 | `tracking`, `yaw_deg` 逐帧差, `depth_jitter_mean_m`, `depth_coverage` |
| B | 手持在胸前，缓慢左右转 30°，来回 5 次 | 旋转连续性、有无 PAUSED | `yaw_step_deg`, `failure`(`INSUFFICIENT_*`/`EXCESSIVE_MOTION`) |
| C | 缓慢前后走 1m，来回 5 次 | 平移连续性、ARCore 尺度是否漂 | `tx`/`ty`/`tz` 序列连续性、`camera_ts_source`、`ar_cam_delta_spread_ns`（**只作观察量，不作判据**）、`tracking` 状态 |
| D | 故意制造失效：手掌完全挡住镜头 5s → 面对纯白墙 5s → 快速甩头 3 次 → 回到纹理墙面 | `TRACKING → PAUSED → TRACKING` 全过程，以及每种的失效原因 | `tracking`, `failure`, `depth_unavailable_reason` |
| E | 在 0.5m / 1m / 2m / 5m 处各放一个纸箱（或椅背），每个距离静止 30s | 深度是否给数、置信度多少、近场中位距离准不准 | `depth_nearband_median_m`, `raw_depth_confident_coverage`, `raw_depth_mean_confidence`, `raw_depth_conf_hist` |

**E 步必须拿卷尺量真实距离并记在笔记本上**，否则"深度有没有用"这句话没有判据。

第一轮结束：

```bash
python3 tools/android/analyze_shadow_log.py shadow-runs/<最新目录>/shadowlog --markdown shadow-runs/<最新目录>/round1.md
```

第一轮通过标准（全部满足才进第二轮）：

1. **`selftest.failed` 必须为空。** 日志里 `type=SELFTEST` 那条是坐标约定的合成自检（前方是不是 -Z、右侧像素是不是 +X、下方像素是不是 -Y、yaw 90°、逆变换闭环、像素重投影闭环）。任何一条 FAIL，说明几何链路符号错，**后面所有 WorldPath 数字都不要读**。
2. **overlay 肉眼核对通过。** 拉回 `shadowlog/session-*/overlay/*.jpg` 至少看 5 张：绿色 mask 边界必须压在画面里真实的路面/人行道边界上，青色十字必须落在绿块里，黄色折线必须顺着可通行区域走。**绿块整体偏移或转了 90°，本轮 WorldPath 结论全部作废**，先修对齐再看别的。
3. `frames_with_image / frames > 0.9`，`rgb_truncated` 恒为 false，且 `rgb_error` 里没有 `RESOURCE_EXHAUSTED`（说明 Image 都有正常关闭）。
4. `clock.camera_ts_source` 有明确值（`REALTIME` / `MONOTONIC_UNKNOWN_BASE` / `NOT_READ`），`ar_cam_delta_spread_ns` 一并记录。
   **这一条不设阈值**：spread 只说明 ARCore 与相机时间戳的差是否稳定，能不能跨域相减由 `camera_ts_source` 决定，见 `TIMESTAMP_AUDIT.md`。若为 `MONOTONIC_UNKNOWN_BASE`，`cross_domain_comparable=false`，证据龄只能走同域 `ar_ns` 差，这是预期行为不是失败。
5. D 步里 `PAUSED` 确实出现过，且恢复后回到 `TRACKING`，全程 App 没重启、没 ANR；`tracking.failure_reasons` 里应能看到 `INSUFFICIENT_FEATURES` / `INSUFFICIENT_LIGHT` / `EXCESSIVE_MOTION` 中至少一种（ARCore 自己给的失效归因）。
6. E 步至少一个距离段 `depth_coverage > 0.2` 且近场中位距离与卷尺读数差 < 0.3m。
   做不到就明确记 **ARCore Depth 在本机不可用**，不要靠调阈值凑。
7. 顺带记录 `depth_px_beyond_8191mm` / `raw_depth_px_beyond_8191mm` / `depth_max_mm_seen`：
   只要出现过 >8191mm 的像素，就实证了这台设备走**完整 16-bit**（0..65535mm）而不是旧文档的 13-bit。这个数本身也是"有没有按 13-bit 掩码解码"的反证——掩码过就永远读不到 >8191。

## 3. 第二轮：安全环境、睁眼步行（目标 20 分钟）

地点只允许：空走廊、空操场、封闭安全空间、宽阔无人区域。

```bash
tools/android/run_ar_probe.sh --full --seconds 1200
```

| # | 动作 | 判据 |
|---|---|---|
| A | 沿走廊正常直走 20–30m | `wp_points` 稳定 > 2，`wp_lateral_error_m` 中位接近 0，`would_have_said` 以 `STRAIGHT/KEEP` 为主 |
| B | **人保持直走，手机故意向左/右偏 30–45°** | WorldPath 在**世界系**，`wp_lateral_error_m` 不应随手机偏转而持续增大；若跟着翻转，说明背投影/朝向用错了，这是本轮最重要的否定性检查 |
| C | 缓慢向通道一侧偏，直到接近边缘 | `wp_lateral_error_m` 应单调增大 |
| D | 走回中央 | `wp_lateral_error_m` 回落 |
| E | 手掌遮镜头 3s | `tracking` 变 `PAUSED` → `would_have_said=REOBSERVE`，且**没有**产生 `STRAIGHT/LEFT/RIGHT` |
| F | 连续走 10 分钟不动，观察热降频 | `thermal_status` 是否从 0 升到 ≥1，`fps`/`seg_inference_ms` 是否同步恶化 |

第二轮结束同样跑 `analyze_shadow_log.py`，重点看：

- `worldpath.command_flip_rate`：左右/直行来回跳的比例，> 0.3 就说明不能播报。
- `worldpath.unknown_obstacle_candidates` 与 `would_stop`：影子阶段只统计，不播报。
- `segmentation.inference_ms`：手机上 SegFormer 到底多快（电脑阶段的 MPS 数字不能代替）。

## 4. 最后十米采集（顺路做，不影响上面两轮）

```bash
adb shell am start -n com.blindnav.nav/com.blindnav.app.shadow.ArShadowActivity \
  --es shadow_token blindnav-shadow-2026 \
  --ez ENABLE_ARCORE_OWNER true --ez ENABLE_SENSOR_LOG true --ez ENABLE_LAST10M_CAPTURE true
```

屏上操作：填 POI 名 → `开始组` → 依次点 `wide_scan / sign / candidate_door / neighboring_door / door_front_ground`（每张对着一拍）→ 点关系按钮标注（`correct_sign`、`correct_door`、`neighboring_wrong_door`、`ground_same_entrance`、`step`、`curb`、`obstruction`）→ `结束组`。

```bash
adb pull /sdcard/Android/data/com.blindnav.nav/files/last10m shadow-runs/<日期>/last10m
```

本轮目标只是**采到 POI→招牌→门→门前地面的真实关系数据**。不做入口 Recall，不加状态机测试。

## 5. 拉日志与留档

```bash
adb pull /sdcard/Android/data/com.blindnav.nav/files/shadowlog shadow-runs/<日期>/shadowlog
shasum -a 256 app/build/outputs/apk/debug/app-debug.apk     # 报告里必须写清是哪个 APK 测的
```

每次真机跑完必须留下：`logcat.txt`、`shadowlog/session-*/{events.jsonl,meta.json}`、`roundN.md`、笔记本上的卷尺读数与动作时间点。

## 6. 本轮禁止事项

蒙眼走路；真实车流道路测试；让视障用户独立按提示走；自动绕过动态车辆；给出"后退两步"；给出自由距离移动；让 VLM 决定左/右；把 ARCore Depth 当安全级测距；替换生产默认模型；把影子模式的 `would_have_said` 当成真实播报能力写进结论。

## 7. 已知会失败、但不算阻塞的点

- `MODEL_ASSET_MISSING`：SegFormer ONNX 还没 push 到手机。先跑
  `python3 tools/android/export_segformer_onnx.py --out /tmp/segformer_cityscapes.onnx`
  再 `adb push /tmp/segformer_cityscapes.onnx /sdcard/Android/data/com.blindnav.nav/files/models/segformer_cityscapes.onnx`，
  不用重装 APK。缺它时 WorldPath 影子会退回 ARCore Scene Semantics（若设备支持）。
- `depth_unavailable_reason=NOT_YET_AVAILABLE`：ARCore 深度依赖运动与纹理，前若干帧没有是正常状态，不是 bug。
- 相机被占用：`run_ar_probe.sh` 会先 `am force-stop` 兜底。**手动启动时如果看路相机在前台，影子屏只会提示"请先关闭看路"，不会替你关**——因为 `CameraActivity.onDestroy` 会 `TtsEngine.speak("相机已关")`，自动 finish 等于在真实用户正在用的导航里插播并中断视觉引导。
- 影子链路运行中再点"启动"会被拒（`shadow_already_running`），进程被杀或 force-stop 后占用标记自动清零，不会跨进程锁死。
- SegFormer 输入边长现在从模型 metadata 读（当前资产是 256），不再写死；预处理是**非等比压扁**，与 HuggingFace `SegformerImageProcessor` 的 `do_resize` 一致，不做 letterbox。
