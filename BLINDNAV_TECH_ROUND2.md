# BlindNav Tech Round 2 — 2026-11-03

## 四个问题的直接回答

1. **General Traversability 是否真正改善？**  
   是，corridor availability 从 **0/45 → 100%**（frozen SANPO 与新的 real_v2 都是 100%），SAFE recall **89–93%**，SAFE IoU **0.79–0.83**。但 road false-safe 在 real_v2 仍有 **36%**，curb recall 只有 **0.2–8%**，所以是**明显改善、但不能接 production**。

2. **YOLO-World 是否明显优于 YOLOv8 COCO？**  
   **否。** BLV 全量 1795 帧宏召回：COCO **41.8%**，YOLO-World **19.8%**。YOLO-World 把 person 提到 98.5%、bicycle 62.2%、bench 26.3%，但 pole/railing/barrier/stairs/curb 几乎为 0，拖垮宏召回。作为单一替换 **REJECT**；只值得做类别互补实验。

3. **class-independent occupancy 是否值得继续？**  
   **朴素 depth ROI 不值得。** UrbanEgo 真实 depth：1.5m 阈值 recall 21.6% / false-positive 8.1%；2.0m 46% / 29%；3.0m 100% / 86%。只有加入 walkable mask + instance mask + tracking 才值得继续，**NEXT_EXPERIMENT**，不能把 oracle 当生产结果。

4. **Automatic Door Proposal 是否达到可接 production？**  
   **没有。** DeepDoors2 的 Google Drive 下载尝试 0B 失败，未获得门 GT，因此没有 Recall@IoU 数字；**NOT_TESTED / BLOCKED_DATA**，不能接 production。

## Summary Table

| Capability | Round 1 | Round 2 | Evidence | Decision |
|---|---|---|---|---|
| Walking / General Traversability | BlindPath 0/45；Cityscapes strict 22.2% | SANPO fine-tune valid 100%，SAFE recall 89–93%，road false-safe 36%，curb recall 0.2% | DESKTOP_REAL_DATA | NEXT_EXPERIMENT |
| Obstacle | COCO real recall 36–38% | YOLO-World BLV macro 19.8%，低于 COCO 41.8% | DESKTOP_REAL_DATA | REJECT（单一替换） |
| Occupancy-first | 未测 | depth ROI 1.5m 22%/8% FP，3m 100%/86% FP | DESKTOP_REAL_DATA (ORACLE) | NEXT_EXPERIMENT |
| Last10m Search | YELLOW | 未重做，回归保持 | DESKTOP_VERIFIED | KEEP |
| Last10m Entrance | RED | 门 GT 未获取 | BLOCKED_DATA | NEXT_EXPERIMENT |
| Scene / Text | RED | 未接生产 | NOT_CONNECTED | KEEP |

## 1. General Traversability V2

### 标签重映射

SANPO 掩码统一映射为 5 类：

- `SAFE_WALKABLE`: sidewalk, crosswalk, paved trail, other walkable
- `BOUNDARY`: curb
- `CONDITIONAL`: road, terrain
- `NON_WALKABLE`: 其余已标注类别
- `UNKNOWN`: unlabeled / 不可靠区域

**Road 不进入 SAFE_WALKABLE。**

### 数据与训练

- 30 个 SANPO-Real sessions，约 600 帧；21 sessions 训练、6 sessions 新的 real_v2 验证、原 45 帧继续作为 frozen 验证。
- 同一 SegFormer-B0 架构；从 Cityscapes checkpoint 初始化，只替换 5 类分类头，训练 3 epochs，512×288，MPS。
- 没有把模型结构变化混入对比。

### 同集结果

| 模型 | 集 | Valid corridor | SAFE IoU | SAFE recall | Road false-safe | Curb recall | Center median | 延迟 |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| Cityscapes B0 | frozen45 | 4.4% | 0.064 | 0.066 | 1.2% | 0 | 0.599 | 192 ms |
| SANPO FT v1 | frozen45 | 100% | 0.792 | 0.895 | 18.5% | 0 | 0.030 | — |
| **SANPO FT v2** | frozen45 | **100%** | **0.828** | **0.895** | **10.0%** | **8.1%** | **0.024** | 116 ms |
| DeepLabV3+ MBV3 | frozen45 | 100% | 0.758 | 0.893 | 34.0% | 34.1% | 0.031 | 354 ms |
| Cityscapes B0 | real_v2 (120) | 15.0% | 0.163 | 0.170 | 16.9% | 0 | 0.399 | 192 ms |
| **SANPO FT v2** | real_v2 (120) | **100%** | **0.786** | **0.901** | **36.0%** | **0.2%** | **0.026** | 116 ms |
| DeepLabV3+ MBV3 | real_v2 (120) | 100% | 0.783 | 0.925 | 46.2% | 15.4% | 0.067 | 354 ms |

### 结论

- corridor availability 已实质解决（0/45 → 100%）。
- 但 **road false-safe 36%** 意味着在路口/路缘把马路当 SAFE，不能上线；curb recall 0.2% 也不够。
- DeepLabV3+ MobileNetV3 更慢、road false-safe 更差，仅 curb recall 略好；不作为首选。
- ONNX export 需要 `onnxscript`，本轮未完成；Android 部署仍 `UNVERIFIED`。
- 没有改 production；`NEXT_EXPERIMENT`：继续做 boundary-aware / curb-aware 训练，并把 road false-safe 和 curb recall 作为门槛。

## 2. Obstacle V2

### YOLO-World BLV 词表

词表 20 类：person, bicycle, wheelchair, dog, bench, chair, table, bollard, pole, pillar, railing, barrier, foldout sign, stairs, curb, raised entryway, trash bin, construction object, overhanging branch, doorway。

| 指标 | COCO YOLOv8n | YOLO-World s |
|---|---:|---:|
| BLV macro recall | **41.8%** | **19.8%** |
| Person | 96.4% | 98.5% |
| Bicycle | 31.1% | **62.2%** |
| Bench | 13.2% | **26.3%** |
| Pole | 0 | 0 |
| Railing | 0 | 0 |
| Barrier Post | 0 | 0 |
| Stairs | 0 | 0 |
| Curb | 0 | 0.3% |

- YOLO-World 模型 25.9MB，Mac CPU 75.7ms/帧。
- 对 person/bicycle/bench 有帮助，但对 pole/railing/stairs/curb 这类细长/地面结构几乎无效，整体宏召回反而更低。
- **REJECT 作为单一替换**；可做 class-specific 互补，但不是本轮 production 变更。

### Occupancy-first oracle

使用 UrbanEgo 真实 depth（320×288，mm），下中走廊 ROI，占空比阈值：

| 近距阈值 | Blocking recall | False-positive rate |
|---|---:|---:|
| <1.5m | 21.6% | 8.1% |
| <2.0m | 46.0% | 29.0% |
| <3.0m | 100% | 86.0% |

- 结论：raw depth 不能单独作为 occupancy；必须和 walkable mask、instance/foreground mask、track 结合。
- 这是 `ORACLE_DEPTH`，不是生产模型分数；不把它当 unknown-obstacle 已解决。
- `NEXT_EXPERIMENT`。

## 3. Last10m

- 状态机、TargetLock、ArrivalAuthority 未改；现有回归保持。
- DeepDoors2 的 gdown 尝试 0B，Google Drive 未返回文件；没有门 GT，因此没有 Recall@IoU0.3/0.5、平均候选数。
- 结论：`BLOCKED_DATA`，自动门候选仍不能接 production。

## 4. Scene / Text

未推进 production；GuideDog gold 仍需 HF 认证，本轮没有真实 guidance 指标。保持 `NOT_CONNECTED` / `PROTOTYPE`。

## 5. 修改与回归

- 本轮没有修改 production 代码。
- 173 个 JVM tests / 0 failures 保持。
- 新增离线模型与证据：`eval/real_v2/*.json`；训练脚本与权重在 `~/blindnav-real-eval/models`。
- 没有因为 corridor 100% 就合并；road false-safe/curb recall 未达门槛。

## 6. 下一步最值得做的一件事

在 SANPO + Mapillary 上做 **boundary-aware walkable segmentation**：保持 SegFormer-B0，加入 curb/boundary 强监督和 road-safe 抑制，用 real_v2 的 road false-safe（目标 <5%）和 curb recall（目标 >70%）作为硬门槛；达到后再谈 Android ONNX 集成。
