# BlindNav Real-Data Round 1 — 唯一报告

## 一句话

真实视频上最大的瓶颈仍是“没有盲道时的通用走廊”；障碍真实召回只有 36–38%；Last10m 的目标/到达安全逻辑稳，但自动正确门候选仍未接生产。

## 1. 数据与证据等级

| 数据集 | 本地数据 | GT | 用途 | 局限 |
|---|---|---|---|---|
| SANPO-Real | 3 sessions × 15 帧 + 语义掩码 | sidewalk/road/curb/terrain 像素标签 | 通用可走区域 | 无 obstacle box、无 target/door |
| UrbanEgo run7 | 958 个 1s 帧 + 30s 片段 | YOLO11x+BoT-SORT、depth、heading | 真实视频避障 | GT 是模型生成，不是人工框 |
| BLV-Road-Nav-Accessibility | 1795 帧 + 90 类逐帧存在标签 | 帧级对象存在 | BLV 重要对象覆盖 | 无框、无 corridor overlap |
| GuideDog | 仅代码，HF gated | 2106 gold | 场景播报有用性 | 未下载，无指标 |
| Signboard / DeepDoors2 | 仅代码，数据在 Google Drive | 品牌/门 | Target Search / 门候选 | 未下载，无指标 |

证据等级：`DESKTOP_REAL_DATA`（SANPO/UrbanEgo/BLV）、`DESKTOP_VERIFIED`（Last10m JVM）、`NOT_CONNECTED`（Scene/Text/Automatic Entrance）。没有把模型自身预测当人工 GT，也没有为缺失数据编造 accuracy。

## 2. Walking

- SANPO 45 帧全部是 **无盲道人行道**；34 帧宽人行道，30 帧有行人。
- 当前 `BlindPathDetector` 有效 corridor：**0/45 = 0%**。
- GT 可走区域可用：45/45。
- SegFormer-B0 Cityscapes 严格人行道：**22.2%**；inclusive（road+sidewalk+terrain）100%，但 **90.1% 人行道像素被判成马路**。把 road 当可走在路缘处不安全，未上线。
- SegFormer-B2 严格人行道：11.1%；人行道→马路 94.3%。
- Centerline error median/P90：0.614 / 0.700；temporal jitter 0.33 / 0.16。
- Failure buckets：NO_WALKABLE_MASK 45、SEGMENTATION_FAILURE 35、SIDEWALK_AS_ROAD 45、CENTERLINE_FAILURE 28、PEDESTRIAN_OCCLUSION 20。MOTION_BLUR / LIGHTING 0。

结论：`RED`。根因是标签/域缺口，不是阈值；当前检测器只认盲道，Cityscapes 模型分不清砖铺人行道和马路。本轮**没有改 production**，标记 `NEED_DECISION`。

## 3. Obstacle

UrbanEgo run7（958 个 1s 帧）：

| 配置 | Object recall | Precision | Frame false-positive |
|---|---:|---:|---:|
| 当前 0.30/0.25 | 36.4% | 32.1% | 6.8% |
| 提高召回 0.25/0.20 | 38.4% | 27.7% | 8.7% |
| 提高精度 0.35/0.30 | 32.3% | 34.4% | 5.4% |

没有任何配置接近 90% 召回；阈值调参被双指标规则拒绝，production 不变。

BLV 1795 帧：COCO 覆盖的 9 类宏召回 **41.8%**；person 96.4%、car 78.1%、bus 76.3%、bicycle 31.1%、bench 13.2%、chair/table 0%。23 类没有 COCO 类别：curb、stairs、doorway、pillar、pole、railing、foldout sign、raised entryway、overhanging branches、sidewalk pits 等。

真实 375–400s 片段：GT 挡路 44 帧，模型 TP 43、FP 30、FN 1；Time-to-STOP 0ms；恢复 100–4300ms，其中一段超过 2s 目标；STOP 翻转 6 次。

结论：`RED`。最大缺口是 class-independent corridor occupancy；不是换阈值能解决的。

## 4. Last10m

- SEARCH / LOCK：现有 166 个 Last10m JVM 测试通过，`DESKTOP_VERIFIED`；没有真实 Signboard 子集，不报真实品牌召回。
- ENTRANCE：`NOT_CONNECTED`。`EntranceProposal` 只有接口，生产候选仍靠人工/VLM；DeepDoors2 数据未下载，不报 Recall@IoU。
- ARRIVAL：六条件、邻门冲突、跨 session、stale evidence 回归通过，`DESKTOP_VERIFIED`。
- Target→Door relation：`MISSING_GT`，未伪造 `relation_seed_v1`。

结论：Search `YELLOW`，Entrance `RED`。

## 5. Scene / Text

- `SceneAwarenessEngine`/`SceneMemory`/`SpeechPolicy` 15 个测试通过，但 production 无调用点：`NOT_CONNECTED`。
- `TextSaliencePolicy` 的 TARGET/WAYFINDING/GENERAL/NOISE 与 10s 去重测试通过，但常驻 OCR 未接生产：`PROTOTYPE`。
- GuideDog gold 未下载，不报 caption/guidance accuracy。

结论：Scene `RED`，Text `RED`。

## 6. Android / 构建

- Experience Mode 未实现：P0/P1 仍是 RED，先做产品壳会掩盖真实失败；没有 UI 代码变更。
- Clean build + 全量测试：**173 tests / 0 failures / 0 errors**。
- APK：`app/build/outputs/apk/debug/app-debug.apk`
- SHA-256：`2d19236e352b1729ba650e5f161349431068962a4af187b47ac9e66a76a48e21`
- Size：586,069,616 bytes

## 7. Regressions

`REG-001`–`REG-006` 全部通过：静态桌面不 STOP、KFC 两帧锁定、目标不是 KFC 时不主动报 KFC、近墙不 STOP、未知物体挡走廊可 STOP、邻门不 ARRIVAL。安全策略未改。

## 8. 明天设备测试（8 个动作）

1. 静止面对电脑 10s：不持续 STOP。
2. 直走清晰走廊 30s：LEFT/RIGHT 翻转 ≤3 次/分钟。
3. 原地 ±30° 转手机：不误报左右。
4. 真实偏 0.5–1m：一次明确修正，回正后 KEEP。
5. 椅子挡走廊：STOP ≤1s；移开后 ≤2s 释放。
6. 侧边墙/门很近：不 STOP。
7. KFC/瑞幸目标：尽早 Target Found，不因招牌直接 ARRIVAL。
8. 邻门：UNKNOWN / REOBSERVE，绝不 ARRIVAL。

## 9. 最终三个回答

1. **当前真实数据上最强的能力**：Last10m 的 TargetLock/Arrival 安全约束，以及 person/car 检测；系统保守，不会猜门。
2. **最大真实失败**：无盲道通用可走走廊，0/45。
3. **下一轮最值得改的一个问题**：用 Mapillary/SANPO 做真正的 walkable mask，并把 curb/road 误判率作为必报指标。

## 10. Verdict

- Walking：**RED**
- Obstacle：**RED**
- Last10m Search：**YELLOW**
- Last10m Entrance：**RED**
