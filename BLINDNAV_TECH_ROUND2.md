# BlindNav Tech Round 2–3 — 2026-11-03

## 三个问题的直接回答

1. **Walking false-safe 能否压下来？**  
   **能压到 16.5%，再往下很难，且和 curb 相互拉扯。** road-as-SAFE 惩罚 + Cityscapes decoder 适配把 real_v2 的 road false-safe 从 **36.0% → 16.5%**，翻转型 TTA 再降到 **14.2%**；frozen45 从 10.0% → 1.9%，Cityscapes 从 42.7% → 25.9%。corridor 保持 **95.0%**。curb 专项模型把 curb recall 从 **0 → 31.6%**（与 v5 组成双模型 ensemble，false-safe 仍 16.5%）。但没有任何单一配置同时满足 **false-safe <10% 且 curb 明显起来**；最接近的是 TTA 的 14.2%，仍未过线。

2. **Obstacle 的 class-independent occupancy 是否真正提高？**  
   **是，第一次出现了可用信号，但离 75–80% recall / ≤10% False STOP 还很远。** 放弃“YOLO instance 必须与 walkable mask 重叠”后，改用 ground-plane depth residual（比地面更近即占用）：Blocking Recall **52.3%**、Precision 0.23、False-positive **37.4%**；加 2 帧时序后 **45.5% / 31.1%**。此前 instance 拼法只有 **2.3%**。2D dense depth 和 3D voxel 两种构造都更差（FP 55–100%）。

3. **Door proposal 是否达到可接 production 的水平？**  
   **域内达到，跨域没有。** 用 3150 张 Roboflow 门数据微调 YOLOv8n 后，held-out test 在 conf 0.05 时 Recall@IoU0.3 **95.7%**、@0.5 **82.1%**、平均候选 **2.72**（≤3）；扰动稳定性 IoU **0.847**、匹配率 99.6%。外部 OpenImages 173 个门框只有 Recall@0.3 **46.8%**；追加重增强后升到 **58.4%**，仍远低于 75% 跨域里程碑。不能直接接 production。

## Summary Table

| Capability | Before (Round 2) | Round 3 | Evidence | Decision |
|---|---|---|---|---|
| Walking / Traversability | real_v2 road FS 36.0%，corridor 100%，SAFE recall 90.1% | road FS **14.2%（TTA）/16.5%（v5）**，corridor **95.0%**，SAFE recall 78–79%；curb **31.6%（v5+v6 ensemble）**；Cityscapes FS 25.9% | DESKTOP_REAL_DATA | NEXT_EXPERIMENT |
| Obstacle semantic | COCO 41.8%，YOLO-World 20 词 19.8% | YOLO-World 40 词 **19.97%**，无提升 | DESKTOP_REAL_DATA | REJECT（扩词表） |
| Obstacle occupancy | instance 拼法 2.3%，depth ROI 0% | ground residual **52.3% recall / 37.4% FP**；时序 45.5% / 31.1% | DESKTOP_REAL_DATA | NEXT_EXPERIMENT |
| Last10m Door | YOLO-World Recall@0.3 21.4%，@0.5 13.9% | 微调 YOLOv8n 域内 **95.7% / 82.1%**，平均候选 2.72；外部 **46.8% / 38.7%**，重增强后 **58.4% / 41.6%** | DESKTOP_REAL_DATA | 域内 KEEP，跨域 NEXT_EXPERIMENT |
| Last10m Search | YELLOW | 未改 | DESKTOP_VERIFIED | KEEP |
| Scene / Text | RED | 未接 | NOT_CONNECTED | KEEP |

## 1. Walking：针对 road false-safe 的 safety segmentation

### 改了什么

不是继续调旧阈值，而是改训练目标：

1. 对 GT road 像素预测成 SAFE 的概率加显式惩罚：`loss += 2.0 * P(SAFE | road)`；
2. road/terrain 类权重从 1 提高到 8，boundary 权重提高到 15；
3. 用 426 张 Cityscapes 训练图像做 decoder 适配（encoder 冻结），教模型区分 sidewalk 与 road；Cityscapes 只用于训练/内部验证，官方 val 100 张完全留出。

数据仍是 SANPO：21 sessions 训练、6 sessions real_v2 验证、45 帧 frozen。所有对比都在同一 frozen set 上。

### Before / After

| 模型 | 集 | Valid corridor | SAFE IoU | SAFE recall | Road false-safe | Curb recall | Center err |
|---|---|---:|---:|---:|---:|---:|---:|
| SANPO FT v2 | frozen45 | 100% | 0.828 | 89.5% | 10.0% | 8.1% | 0.024 |
| **v5 false-safe + City adapt** | frozen45 | **100%** | **0.827** | 86.0% | **1.9%** | 0 | **0.008** |
| SANPO FT v2 | real_v2 (120) | 100% | 0.786 | 90.1% | 36.0% | 0.2% | 0.026 |
| **v5 false-safe + City adapt** | real_v2 (120) | **95.0%** | 0.743 | 79.2% | **16.5%** | 0 | 0.031 |
| v6 boundary 专项 | real_v2 (120) | 98.3% | 0.767 | 84.1% | 26.5% | 30.3% | 0.034 |
| **v5 + v6 curb ensemble** | real_v2 (120) | **95.0%** | 0.743 | 79.2% | **16.5%** | **31.6%** | 0.031 |
| v5 + flip TTA | real_v2 (120) | 95.0% | 0.740 | 78.1% | **14.2%** | 0 | 0.031 |
| SANPO FT v2 | Cityscapes val (100) | 100% | 0.0875 | 99.99% | 42.7% | — | — |
| **v5 false-safe + City adapt** | Cityscapes val (100) | **100%** | **0.1105** | 99.85% | 33.3% | — | — |
| v5 + flip TTA | Cityscapes val (100) | 100% | 0.140 | 98.2% | **25.9%** | — | — |

v6 boundary 的 frozen45 表现：curb recall **37.3%**、road false-safe 3.9%；与 v5 做 curb ensemble 后 frozen45 curb **48.1%**、false-safe 1.9%。

结论：

- **False-safe 确实降了**：v5 real_v2 相对下降 54%，frozen45 相对下降 81%；TTA 再降到 14.2%，Cityscapes 从 42.7% 降到 25.9%。
- **代价明确**：SAFE recall 从 90.1% 掉到 79.2%（TTA 78.1%），IoU 从 0.786 掉到 0.743。这是“宁可少报可走”的保守化，不是免费提升。
- **Curb 专项有效但会反噬**：v6 把 curb recall 拉到 30.3%，但 road false-safe 回到 26.5%；与 v5 做 ensemble 可以保留 v5 的 16.5% false-safe，同时拿到 **31.6% curb recall**。
- **没有单一配置同时达标**：false-safe <10% 仍没做到（最好 14.2%），curb 最好 31.6% 也远低于可用边界感知；Cityscapes 25.9% 仍不能上线。

### 外部研究对照

2026 年的 safety-oriented sidewalk/road segmentation 工作（arXiv:2607.21137）把 **Road-as-Sidewalk Error Rate** 单独作为核心指标；其 DeepLabV3Plus-MobileNetV3 的 road-as-sidewalk error 为 0.079，Android 约 7.38 FPS（512×384）。本项目当前 16.5% 仍差一倍以上，且 curb recall 为 0。Mobilio（Nature Biomedical Engineering 2026）把语义分割用于 2D walkable grid，而不是直接给移动许可；这与本项目“分割只提供走廊、不单独授权移动”的边界一致。

## 2. Obstacle：语义召回与 class-independent occupancy

### 2.1 YOLO-World 扩词表

把 BLV 词表从 20 个扩到 40 个（加入 scooter、stroller、traffic cone、guardrail、tactile paving、curb ramp、gate、fence 等），在同样 1795 帧 BLV 上重跑：

| 指标 | COCO YOLOv8n | YOLO-World 20 词 | YOLO-World 40 词 |
|---|---:|---:|---:|
| BLV macro recall | **41.8%** | 19.8% | **19.97%** |
| Person | 96.4% | 98.5% | 98.5% |
| Bicycle | 31.1% | 62.2% | 64.4% |
| Bench | 13.2% | 26.3% | 26.3% |
| Pole / railing / barrier / stairs | 0 | 0 | 0 |
| Curb | 0 | 0.3% | 0.3% |

扩词表只提升 0.17 个百分点，薄/地面结构仍然全 0。**REJECT 作为替换方案**；YOLO-World 只能做 person/bicycle 的类别补充。

### 2.2 class-independent occupancy：四种构造对比

同一 UrbanEgo clip 375–400s（250 帧，44 个 GT blocking）：

| 构造 | Blocking recall | Precision | False-positive |
|---|---:|---:|---:|
| walkable + YOLOv8n-seg instance + depth + 时序（Round 2） | 2.3% | 1.00 | 0.0% |
| 2D dense depth + walkable mask + 时序 | 68.2% | 0.148 | 84.0% |
| 3D sparse depth voxel + ground removal + 时序 | 43.2% | 0.128 | 62.6% |
| **Ground-plane depth residual（无 instance）+ 时序** | **45.5%** | **0.238** | **31.1%** |
| 同上，不要求与 walkable mask 重叠 | **52.3%** | **0.230** | **37.4%** |
| 同上 + 像素级 2 帧 AND | 34.1% | 0.211 | 27.2% |
| 同上 + 像素级 3 帧多数 | 22.7% | 0.217 | **17.5%** |

Ground residual 的做法：用 pose 把深度点变到世界坐标，拟合地面平面，像素深度比该像素对应的地面深度近 0.25m 以上即判为“占据”；不依赖任何类别，也不要求 YOLO instance 先和 walkable mask 重叠。它第一次把 recall 从 2.3% 拉到 52.3%，说明“深度 + 几何 + 时序”方向成立；但 37.4% 的 False-positive 意味着每 3 个非阻断帧就有 1 次误报，不能接 WalkingSafetyPolicy。

进一步把“帧级面积持续”改成“像素级 2/3 帧 AND”：FP 从 31.1% 压到 **17.5%**，但 recall 同时掉到 22.7%。这说明当前 FP 不是随机单帧噪声——很多是持续存在的边界/地面误差，像素级过滤会连同真障碍一起杀掉。目标是 recall ≥50% 时 FP ≤15%，当前没有同时满足的点；下一步必须改地面/深度本身，而不是继续加时间窗口。

Unknown Blocking Recall 仍按 BLV 帧级定义（COCO 没有的 12 个高价值类别，1021 个支持帧）为 **0.1%**；BLV 只有 presence 标签，无法报 box IoU。class-independent 构造从原理上不依赖类别，但缺少 unknown 物体的框 GT，不能宣称已解决。

## 3. Last10m：门检测专项训练

### 数据与模型

DeepDoors2 官方 Google Drive 仍无法稳定下载。改用公开 CC BY 4.0 的 Roboflow door_detection 数据集：**3150 train / 225 val / 225 test**，单类 `Door`，已转换为 YOLO bbox。训练/测试按原始图像去重检查：train 与 test 的 base image 交集为 **0**，无增强泄漏。

模型：YOLOv8n，6.5MB，imgsz 384，CPU 训练 12 epochs；MPS 在当前机器上不稳定，未使用。Mobilio 的参考实现也是把门/入口作为独立感知候选，而不是让通用 VLM 直接给结论。

### Held-out test（225 图 / 235 框）

| conf | Recall@IoU0.3 | Recall@IoU0.5 | Mean candidates | Median |
|---:|---:|---:|---:|---:|
| 0.01 | 98.3% | 85.5% | 4.26 | 4 |
| 0.03 | 97.0% | 84.3% | 3.13 | 3 |
| **0.05** | **95.7%** | **82.1%** | **2.72** | **2** |
| 0.10 | 91.9% | 78.7% | 2.28 | 2 |
| 0.25 | 84.3% | 72.3% | 1.70 | 2 |

conf 0.05 同时满足 Recall@0.3 ≥90% 和平均候选 ≤3，达到本轮目标。

### 外部域 OpenImages（120 图 / 173 框）

| conf | Recall@IoU0.3 | Recall@IoU0.5 | Mean candidates |
|---:|---:|---:|---:|
| 0.01 | 57.2% | 48.0% | 4.99 |
| 0.05 | 46.8% | 38.7% | 2.78 |
| 0.10 | 39.3% | 33.5% | 1.96 |

域外掉到 46.8%，说明模型学到了 Roboflow 数据集的视角/外观偏置。**不能因为 held-out test 95.7% 就宣称门检测已可生产。**

### 跨域泛化尝试：重增强

在原模型上追加 6 epochs 重增强（旋转 ±20°、平移 0.2、缩放 0.6、剪切、透视、HSV、mosaic、mixup、erasing、RandAugment），不加入任何 OpenImages 图像：

| 指标 | 原模型 | 重增强 |
|---|---:|---:|
| 域内 Recall@0.3 @conf0.05 | 95.7% | 95.7% |
| 域内平均候选 @conf0.05 | 2.72 | 3.84 |
| 域内 Recall@0.3 @conf0.10 | 91.9% | 91.9% |
| 域内平均候选 @conf0.10 | 2.28 | **2.87** |
| 外部 OpenImages Recall@0.3 @conf0.05 | 46.8% | **58.4%** |
| 外部 OpenImages Recall@0.5 @conf0.05 | 38.7% | **41.6%** |
| 外部 OpenImages Recall@0.3 @conf0.10 | 39.3% | **48.6%** |

重增强把外域召回从 46.8% 提到 **58.4%**，但离第一里程碑 **75%** 仍差 17 个百分点；同时域内低阈值候选数变多。结论：augmentation 只能补一部分域偏移，下一轮必须引入真实 storefront/entrance 多域数据，或 DeepDoors2 的玻璃门/户外门/遮挡子集，而不是继续在 Roboflow 上刷。

### Candidate Stability

对 222 张有候选的 test 图做受控扰动（x/y 平移 2%、0.98× 缩放）：

- 匹配候选 IoU 均值 **0.8471**
- 匹配率 **99.55%**
- 候选数标准差 **1.258**

位置稳定，但候选数在扰动下漂移比 Round 2 的通用 proposal（0.879）更大，因为低阈值下多出了弱候选。该结果仍是图像扰动，不是视频时序稳定性。

## 4. Android 部署可行性

| 模型 | 大小 | Mac CPU 延迟 | Android 判断 |
|---|---:|---:|---|
| SegFormer-B0 v5 | 14.9MB | 46.8ms ORT（Round 2 导出） | `FEASIBLE_PROTOTYPE`，未真机 |
| 门检测 YOLOv8n | 6.5MB | 56.7ms 推理（640 输入，CPU） | `FEASIBLE_PROTOTYPE`，未真机 |
| YOLO-World 40 词 | 25.9MB | 149.8ms（CPU） | `RESEARCH_CANDIDATE`，文本编码器成本高 |

没有上手机，没有 Android 设备实测。

## 5. 规则遵守情况

- 所有 Before/After 使用同一 frozen real_v2 / frozen45 / Cityscapes val / BLV / UrbanEgo clip。
- 没有修改生产阈值；WalkingSafetyPolicy、MotionAuthority、ArrivalAuthority 均未改。
- ORACLE 数字单独标注，未当生产结果。
- 单测 173 个仍通过；没有因为单测通过宣称功能完成。

## 6. 结论与下一步

- **Walking**：false-safe 已从 36.0% 压到 16.5%（TTA 14.2%），curb 通过 v5+v6 ensemble 从 0 拉到 31.6%，但两者不能在同一配置里同时达标；Cityscapes 25.9% 仍是 RED。下一步补真实 curb/boundary 数据和统一训练目标，而不是继续调阈值。
- **Obstacle**：ground-plane occupancy 是第一条有效路线（52.3% / 37.4% FP），但 False STOP 太高；把时序从“帧级面积持续”改成“像素级 3 帧多数”只能把 FP 压到 17.5%，同时 recall 掉到 22.7%。下一步做深度补全 + 相机高度/平面在线估计，目标是 recall ≥75% 时 FP ≤10%，而不是继续加时间窗。
- **Door**：域内已过线，跨域 46.8% 不过线。下一步用 DeepDoors2 或更多真实入口数据做域泛化，再谈接 EntranceProposal。
- **上手机门槛**：Walking corridor ≥75% 且 false-safe 受控（当前 corridor 95%，false-safe 14.2–16.5%，未到 <10%）；Obstacle recall ≥75–80% 且 False STOP ≤10%（当前 52.3% / 37.4%）；Door 域内 Recall ≥90%（已 95.7%，跨域 58.4%）。三项未全部满足，不请求真机测试。
