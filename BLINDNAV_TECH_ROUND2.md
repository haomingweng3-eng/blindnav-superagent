# BlindNav Tech Round 2 — 2026-11-03

## 四个问题的直接回答

1. **General Traversability 是否真正改善？**  
   是，corridor availability 从 **0/45 → 100%**（frozen SANPO 与 real_v2 都 100%），SAFE recall **89–93%**，SAFE IoU **0.79–0.83**。但 real_v2 的 road false-safe 仍 **36%**，curb recall **0.2%**；DeepLabV3+ MobileNetV3 更慢且 road false-safe 46%，MobilioNet-B 无公开代码/权重。只能 `NEXT_EXPERIMENT`，不能接 production。

2. **YOLO-World 是否明显优于 YOLOv8 COCO？**  
   **否。** BLV 1795 帧宏召回：COCO **41.8%**，YOLO-World **19.8%**。person 98.5%、bicycle 62.2%、bench 26.3% 有提升，但 pole/railing/barrier/stairs/curb 几乎为 0，整体更差。作为单一替换 **REJECT**；只值得做类别互补。

3. **class-independent occupancy 是否值得继续？**  
   **方向值得，当前的输入不合格。** UrbanEgo 真实 depth：1.5m recall 21.6% / false-positive 8.1%；2.0m 46% / 29%；3.0m 100% / 86%。本轮 375–400s 片段（250 帧、44 个 GT blocking）补上了真实实例 mask 与跟踪：walkable + YOLOv8n-seg + depth<2m + 2 帧持续，recall 仅 **2.3%**、precision 1.0、FP 0；GT track instance 的 ORACLE 变体为 recall **36.4%**、FP 0。逐层诊断显示 1204 个 blocking 实例只剩 12 个与 walkable mask 重叠，depth 有效实例只有 4 个——主因是分割在 UrbanEgo 上指不准人行道、depth 大面积空洞。Unknown Blocking Recall 在 BLV 帧级可测定义下（COCO 没有的 12 个高价值类别，1021 个支持帧）为 **0.1%**；没有框 GT，不能报 IoU。`NEXT_EXPERIMENT`。

4. **Automatic Door Proposal 是否达到可接 production？**  
   **没有。** DeepDoors2 的 Drive 镜像仍阻塞，但改用 OpenImages 的真实 Door bbox GT（120 图 / 173 框）：YOLO-World 最佳 Recall@IoU0.3 **21.4%**、@0.5 **13.9%**，平均候选 0.47 个；远低于 90% 目标。候选稳定性用受控扰动测得匹配 IoU **0.8235**、候选数标准差 **0.879**，但这是图像扰动不是时序视频；BLV 视频探针零候选。`NEXT_EXPERIMENT`，不能接 production。

## Summary Table

| Capability | Round 1 | Round 2 | Evidence | Decision |
|---|---|---|---|---|
| Walking / General Traversability | BlindPath 0/45；Cityscapes strict 22.2% | SANPO FT valid 100%，SAFE recall 89–93%，road false-safe 36%，curb recall 0.2%；Cityscapes 第二域 SAFE recall 99.99% 但 IoU 0.0875、road false-safe 42.7% | DESKTOP_REAL_DATA | NEXT_EXPERIMENT |
| Obstacle | COCO real recall 36–38% | YOLO-World BLV macro 19.8%，低于 COCO 41.8% | DESKTOP_REAL_DATA | REJECT（单一替换） |
| Occupancy-first | 未测 | walkable+实例+depth+2帧 recall 2.3% / FP 0；GT instance ORACLE 36.4%；unknown recall 不可测 | DESKTOP_REAL_DATA (ORACLE) | NEXT_EXPERIMENT |
| Last10m Search | YELLOW | 未重做，回归保持 | DESKTOP_VERIFIED | KEEP |
| Last10m Entrance | RED | OpenImages Door Recall@0.3 21.4% / @0.5 13.9%，平均候选 0.47；扰动稳定性 IoU 0.8235 | DESKTOP_REAL_DATA | NEXT_EXPERIMENT |
| Scene / Text | RED | 未接生产 | NOT_CONNECTED | KEEP |

## 1. General Traversability V2

标签统一为 5 类：`SAFE_WALKABLE`（sidewalk/crosswalk/paved trail/other walkable）、`BOUNDARY`（curb）、`CONDITIONAL`（road/terrain）、`NON_WALKABLE`、`UNKNOWN`。**Road 不进入 SAFE_WALKABLE。**

数据：30 个 SANPO-Real sessions，约 600 帧；21 sessions 训练、6 sessions real_v2 验证、原 45 帧 frozen。同一 SegFormer-B0 架构，Cityscapes 初始化，替换 5 类头，3 epochs，512×288，MPS。

| 模型 | 集 | Valid corridor | SAFE IoU | SAFE recall | Road false-safe | Curb recall | Center median | Latency |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| Cityscapes B0 | frozen45 | 4.4% | 0.064 | 0.066 | 1.2% | 0 | 0.599 | 192 ms |
| SANPO FT v1 | frozen45 | 100% | 0.792 | 0.895 | 18.5% | 0 | 0.030 | — |
| **SANPO FT v2** | frozen45 | **100%** | **0.828** | **0.895** | **10.0%** | **8.1%** | **0.024** | 116 ms |
| DeepLabV3+ MBV3 | frozen45 | 100% | 0.758 | 0.893 | 34.0% | 34.1% | 0.031 | 354 ms |
| Cityscapes B0 | real_v2 (120) | 15.0% | 0.163 | 0.170 | 16.9% | 0 | 0.399 | 192 ms |
| **SANPO FT v2** | real_v2 (120) | **100%** | **0.786** | **0.901** | **36.0%** | **0.2%** | **0.026** | 116 ms |
| DeepLabV3+ MBV3 | real_v2 (120) | 100% | 0.783 | 0.925 | 46.2% | 15.4% | 0.067 | 354 ms |

### 第二城市分割域：Cityscapes 验证集

Mapillary 官方数据需登录、SideGuide/sidewalk-semantic 是 gated，无法在本轮拿到合法可用的 pedestrian-view 分割 GT。改用 Cityscapes 验证集 100 张（`test0.parquet`）做第二个 urban 分割域检查，并明确：**Cityscapes 是车载视角，不是盲人步行视角**，只能验证“换城市后模型还认不认路”，不能替代人行道数据。

| 模型 | 图像 | Valid corridor | SAFE IoU | SAFE recall | Road false-safe |
|---|---|---:|---:|---:|---:|
| Cityscapes B0 | 100 | **0.0%** | 0.0001 | 0.02% | 3.5% |
| SANPO FT v2 | 100 | **100%** | 0.0875 | **99.99%** | **42.7%** |

Cityscapes 原模型在盲道语义上几乎不输出 SAFE（IoU 0.0001），所以它 3.5% 的 road false-safe 没有意义——它把 99.98% 的 sidewalk 都判成了 CONDITIONAL/UNKNOWN。SANPO 微调后 corridor 恢复，但把大量 road 当成 SAFE（42.7%），IoU 只有 0.0875。两个域结论一致：**当前分割能“找到路”，但分不清“人行道”和“车道”**，这正是不能上生产的原因。

### Mapillary 图片域检查（无标签）

通过 Hugging Face 镜像取到 Mapillary Vistas 测试图片 24 张（官方站点需登录，这里只用图像、不用标签），跑 SANPO FT v2 看输出是否还有可用走廊：

- 有效走廊率 **95.8%**（23/24 帧 ROI 中 SAFE 占比 ≥25%）
- ROI 平均 SAFE 占比 **62.7%**

这只能说明模型在不同城市、不同相机下仍能产出走廊，不能说明走廊是否正确；没有 GT，所以本轮不报 IoU、不报 road false-safe，也不把它算作精度证据。

### MobilioNet-B feasibility

Web/GitHub search found only news coverage of Harvard Mobilio; **no public MobilioNet-B code or weights**. It cannot be reproduced locally without the authors' release. The closest lightweight proxy tested is DeepLabV3+ MobileNetV3 above: 42MB checkpoint, 354ms/frame, road false-safe 34–46%, curb recall 15–34%. Verdict: `NOT_AVAILABLE` for MobilioNet-B; lightweight proxy `RESEARCH_CANDIDATE`, not better than SANPO SegFormer.

### Decision

`NEXT_EXPERIMENT`: corridor availability is solved in prototype, but boundary/road safety is not. Required before integration: road false-safe <5%, curb recall >70%. No production change.

## 2. Obstacle V2

YOLO-World BLV 20-class vocabulary on 1795 frames:

| 指标 | COCO YOLOv8n | YOLO-World s |
|---|---:|---:|
| BLV macro recall | **41.8%** | **19.8%** |
| Person | 96.4% | 98.5% |
| Bicycle | 31.1% | **62.2%** |
| Bench | 13.2% | **26.3%** |
| Pole / Railing / Barrier / Stairs | 0 | 0 |
| Curb | 0 | 0.3% |

YOLO-World 25.9MB, Mac CPU 75.7ms/frame. It helps person/bicycle/bench but not thin/ground structures; macro is worse. **REJECT as single replacement**; possible class-specific ensemble is `NEXT_EXPERIMENT`.

### Occupancy-first oracle

UrbanEgo depth (320×288, mm), lower-center ROI:

| 近距阈值 | Blocking recall | False-positive rate |
|---|---:|---:|
| <1.5m | 21.6% | 8.1% |
| <2.0m | 46.0% | 29.0% |
| <3.0m | 100% | 86.0% |

Raw depth is not viable. Unknown Blocking Recall at BLV frame level is **0.1%** (12 categories without a COCO class, 1021 support frames); box-level IoU is unmeasurable because BLV has presence labels only.

### Occupancy-first 原型（walkable mask + instance mask + depth + 时序）

在 UrbanEgo clip 375–400s（250 帧，10fps，44 个 GT blocking）上按“先判可走、再看占用”的顺序实现四层：

1. SANPO FT v2 分割出 walkable mask；
2. YOLOv8n-seg（COCO，conf 0.25）给出真实实例 mask，不用 GT 替身；
3. 实例 mask 必须落在走廊内且与 walkable mask 重叠；
4. 取实例内的 depth 中位数，要求 <2m，并做相邻帧 bbox IoU>0.3 的跟踪持续性。

| 变体 | Blocking recall | Precision | False-positive rate |
|---|---:|---:|---:|
| 仅实例 | 11.4% | 0.455 | 2.9% |
| 实例 + depth | 6.8% | 0.75 | 0.5% |
| 实例 + 时序 | 2.3% | 0.50 | 0.5% |
| 实例 + depth + 时序 | **2.3%** | **1.0** | **0.0%** |
| 仅 depth + 时序（无实例） | 0.0% | — | 0.0% |
| GT track instance（ORACLE） | 36.4% | 1.0 | 0.0% |

逐层诊断给出了失败位置，而不是一句“效果不好”：

- 250 帧里 YOLOv8n-seg 共检出 **1204** 个 blocking 类实例，落在走廊内的有 **288** 个；
- 再要求与 walkable mask 重叠后只剩 **12** 个，**walkable mask 是主瓶颈**；
- 12 个里 depth 有效（mask 内有效深度 ≥5%）的只有 **4** 个，**depth 空洞是第二瓶颈**；
- 全片段 walkable ROI 平均占比 0.275，达到“有效走廊”（≥25%）的帧只有 **48.8%**。

结论：occupancy-first 的方向没错，但当前两个输入都不合格——分割模型在 UrbanEgo 上指不准人行道，depth 又大面积缺失。改阈值救不回来；ORACLE 变体的 36.4% 说明“实例先验 + 时序”本身可行。Unknown Blocking Recall 在该原型中仍不可测（GT 只覆盖已知 track）。`NEXT_EXPERIMENT`，下一步是补 UrbanEgo/步行视角的分割监督与 depth 补全，而不是继续调阈值。



## 3. Last10m Automatic Door

DeepDoors2 official/relabeled Google Drive downloads still time out; Dataset Ninja exposes metadata but not a public full archive. Instead, use **OpenImages V7 `Door` bbox annotations** as a reliable public door GT subset (CC BY 4.0):

- 120 validation images, **173 door boxes**, average 1.44 GT doors/image.
- Candidate generator: YOLO-World s with `door` / `doorway` / `glass door` / `entrance`.
- Confidence sweep 0.05–0.25.

| conf | Recall@IoU0.3 | Recall@IoU0.5 | Mean candidates | Median candidates | Candidate std |
|---:|---:|---:|---:|---:|---:|
| 0.05 | **21.4%** | **13.9%** | 0.47 | 0 | 0.77 |
| 0.10 | 9.3% | 5.2% | 0.17 | 0 | 0.46 |
| 0.15 | 4.6% | 2.9% | 0.07 | 0 | 0.26 |
| 0.25 | 1.7% | 0.6% | 0.03 | 0 | 0.18 |

The target is 90% Recall with ≤3 candidates. YOLO-World is far below target and produces almost no candidates.

### Candidate Stability

OpenImages 是独立帧，无法做真实时序稳定性，因此做受控扰动稳定性：对 42 张有候选的图分别做 x 平移 2%、y 平移 2%、0.98× 缩放，检查同一候选是否仍被匹配及数量是否漂移。

| 指标 | 结果 |
|---|---:|
| 匹配候选 IoU 均值 | **0.8235** |
| 匹配率 | 100% |
| 候选数标准差（跨扰动） | **0.879** |

位置稳定性尚可（IoU 0.82），但候选数在扰动下明显漂移（std 0.879），这对“门附近只报一个入口”的交互不够稳。该结果只能算图像级受控扰动，**不是视频时序稳定性**；BLV 视频探针零候选，无法补测。**Not production-level.** Next experiment: a door-specific detector or OWLv2/Grounding DINO proposal with a lower threshold, evaluated on the same 173 boxes, plus a real video clip for temporal stability.

## 4. Android deployment feasibility

| Model | Size | Mac CPU latency | ONNX status | Android verdict |
|---|---:|---:|---|---|
| SANPO SegFormer-B0 FT | 14.9MB | 116ms PyTorch / **46.8ms ORT** | Exported (external data 14MB) | `FEASIBLE_PROTOTYPE`; needs single-file ONNX + Android ORT test |
| DeepLabV3+ MBV3 | 42MB | 354ms | Not exported | `RESEARCH_CANDIDATE` |
| YOLO-World s | 25.9MB | 75.7ms | Not exported | `RESEARCH_CANDIDATE`; open-vocab text encoder adds deployment cost |

SegFormer ONNX export succeeded with `onnxscript`; output is 432 nodes, 14.9MB weights, ORT 46.8ms on Mac CPU. No Android device test was performed.

## 5. Threshold discipline

One UrbanEgo diagnostic sweep (0.30/0.25, 0.25/0.20, 0.35/0.30) was run only to test whether the old detector could reach 90% recall. It could not (max 38.4%); the sweep was stopped and **no threshold was changed in production**. This is not a tuning loop.

## 6. What changed

- No production algorithm or threshold change.
- Added offline SANPO safety-segmentation training/eval and real_v2 evidence.
- Added second urban segmentation domain (Cityscapes validation, 100 images) and an unlabeled Mapillary image-domain check (24 images).
- Added occupancy-first prototype with real YOLOv8n-seg instances, depth gating, temporal tracking, plus a clearly separated GT-instance ORACLE variant.
- Added YOLO-World BLV baseline, depth occupancy oracle, door candidate probe, and controlled-perturbation door stability.
- 173 JVM tests / 0 failures remain.

## 7. Next most valuable experiment

Boundary-aware walkable segmentation on SANPO plus pedestrian-view video, with explicit curb/boundary supervision and road-safe suppression; gate on **road false-safe <5%** and **curb recall >70%** on real_v2 before any Android integration. In parallel, fix the occupancy inputs: collect or label UrbanEgo-style walking frames for segmentation, and add depth-hole filling. Mapillary labels remain login-gated, so they cannot serve as the next benchmark without an access path.
