# BlindNav Tech Round 2–3 — 2026-11-03

## 三个问题的直接回答

1. **Walking false-safe 能否压下来？**  
   **能压下来一半，但还不能上线。** 在 SANPO 上加入 road-as-SAFE 惩罚并用 Cityscapes 做 decoder 适配后，real_v2 的 road false-safe 从 **36.0% → 16.5%**，frozen45 从 **10.0% → 1.9%**，Cityscapes 第二域从 **42.7% → 33.3%**；无盲道 corridor 仍保持 **95.0%**（real_v2）。代价是 SAFE recall 从 90.1% 降到 **79.2%**，curb recall 仍为 **0**。方向有效，但没达到“可接生产”。

2. **Obstacle 的 class-independent occupancy 是否真正提高？**  
   **是，第一次出现了可用信号，但离 75–80% recall / ≤10% False STOP 还很远。** 放弃“YOLO instance 必须与 walkable mask 重叠”后，改用 ground-plane depth residual（比地面更近即占用）：Blocking Recall **52.3%**、Precision 0.23、False-positive **37.4%**；加 2 帧时序后 **45.5% / 31.1%**。此前 instance 拼法只有 **2.3%**。2D dense depth 和 3D voxel 两种构造都更差（FP 55–100%）。

3. **Door proposal 是否达到可接 production 的水平？**  
   **域内达到，跨域没有。** 用 3150 张 Roboflow 门数据微调 YOLOv8n 后，held-out test 在 conf 0.05 时 Recall@IoU0.3 **95.7%**、@0.5 **82.1%**、平均候选 **2.72**（≤3），达到“正确门进入 1–3 个候选”的目标；扰动稳定性 IoU **0.847**、匹配率 99.6%。但同一模型在外部 OpenImages 173 个门框上只有 Recall@0.3 **46.8%**、@0.5 **38.7%**，说明跨场景泛化不够，不能直接接 production。

## Summary Table

| Capability | Before (Round 2) | Round 3 | Evidence | Decision |
|---|---|---|---|---|
| Walking / Traversability | real_v2 road FS 36.0%，corridor 100%，SAFE recall 90.1% | road FS **16.5%**，corridor **95.0%**，SAFE recall 79.2%，curb 0；Cityscapes FS 33.3% | DESKTOP_REAL_DATA | NEXT_EXPERIMENT |
| Obstacle semantic | COCO 41.8%，YOLO-World 20 词 19.8% | YOLO-World 40 词 **19.97%**，无提升 | DESKTOP_REAL_DATA | REJECT（扩词表） |
| Obstacle occupancy | instance 拼法 2.3%，depth ROI 0% | ground residual **52.3% recall / 37.4% FP**；时序 45.5% / 31.1% | DESKTOP_REAL_DATA | NEXT_EXPERIMENT |
| Last10m Door | YOLO-World Recall@0.3 21.4%，@0.5 13.9% | 微调 YOLOv8n 域内 **95.7% / 82.1%**，平均候选 2.72；外部 **46.8% / 38.7%** | DESKTOP_REAL_DATA | 域内 KEEP，跨域 NEXT_EXPERIMENT |
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
| SANPO FT v2 | Cityscapes val (100) | 100% | 0.0875 | 99.99% | 42.7% | — | — |
| **v5 false-safe + City adapt** | Cityscapes val (100) | **100%** | **0.1105** | 99.85% | **33.3%** | — | — |

结论：

- **False-safe 确实降了**：real_v2 相对下降 54%，frozen45 相对下降 81%，Cityscapes 下降 9.4 个百分点。
- **代价明确**：SAFE recall 从 90.1% 掉到 79.2%，IoU 从 0.786 掉到 0.743。这是“宁可少报可走”的保守化，不是免费提升。
- **Curb recall 仍为 0**：SANPO 的 curb 像素极少，当前 loss 权重不足以让模型输出 boundary 类。Boundary 是下一个 RED。
- **Cityscapes 仍不达标**：33.3% road false-safe 意味着车载视角下每 3 个 road 像素仍有 1 个被判 SAFE，不能上线。

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

Ground residual 的做法：用 pose 把深度点变到世界坐标，拟合地面平面，像素深度比该像素对应的地面深度近 0.25m 以上即判为“占据”；不依赖任何类别，也不要求 YOLO instance 先和 walkable mask 重叠。它第一次把 recall 从 2.3% 拉到 52.3%，说明“深度 + 几何 + 时序”方向成立；但 37.4% 的 False-positive 意味着每 3 个非阻断帧就有 1 次误报，不能接 WalkingSafetyPolicy。

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

- **Walking**：false-safe 已从 36.0% 压到 16.5%，但 curb recall 0、Cityscapes 33.3%，仍是 RED。下一步不是再调阈值，而是补 curb/boundary 监督和步行视角数据；Cityscapes 只作第二域参考。
- **Obstacle**：ground-plane occupancy 是第一条有效路线（52.3% / 37.4% FP），但 False STOP 太高。下一步做深度补全 + 相机高度/平面在线估计 + 多帧占据累积，目标 recall ≥75%、FP ≤10%。
- **Door**：域内已过线，跨域 46.8% 不过线。下一步用 DeepDoors2 或更多真实入口数据做域泛化，再谈接 EntranceProposal。
- **上手机门槛**：Walking corridor ≥75% 且 false-safe 受控（当前 corridor 95%，false-safe 16.5%）；Obstacle recall ≥75–80% 且 False STOP ≤10%（当前 52.3% / 37.4%）；Door 域内 Recall ≥90%（已 95.7%，跨域未达）。三项未全部满足，不请求真机测试。
