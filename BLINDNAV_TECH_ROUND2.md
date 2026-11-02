# BlindNav Tech Round 2 — 2026-11-03

## 四个问题的直接回答

1. **General Traversability 是否真正改善？**  
   是，corridor availability 从 **0/45 → 100%**（frozen SANPO 与 real_v2 都 100%），SAFE recall **89–93%**，SAFE IoU **0.79–0.83**。但 real_v2 的 road false-safe 仍 **36%**，curb recall **0.2%**；DeepLabV3+ MobileNetV3 更慢且 road false-safe 46%，MobilioNet-B 无公开代码/权重。只能 `NEXT_EXPERIMENT`，不能接 production。

2. **YOLO-World 是否明显优于 YOLOv8 COCO？**  
   **否。** BLV 1795 帧宏召回：COCO **41.8%**，YOLO-World **19.8%**。person 98.5%、bicycle 62.2%、bench 26.3% 有提升，但 pole/railing/barrier/stairs/curb 几乎为 0，整体更差。作为单一替换 **REJECT**；只值得做类别互补。

3. **class-independent occupancy 是否值得继续？**  
   **朴素 depth ROI 不值得。** UrbanEgo 真实 depth：1.5m recall 21.6% / false-positive 8.1%；2.0m 46% / 29%；3.0m 100% / 86%。Unknown Blocking Recall 因 BLV/UrbanEgo 没有未知类别的框 GT，**不可测**，不伪造。只有 walkable mask + instance mask + tracking 才值得继续，`NEXT_EXPERIMENT`。

4. **Automatic Door Proposal 是否达到可接 production？**  
   **没有。** DeepDoors2 官方 Drive 文件夹、标注镜像 Drive 文件、Dataset Ninja 均被外部阻塞；BLV 没有门框 GT。YOLO-World 的 door 词表在 1795 帧上平均候选 **0.0** 个，无法形成候选集。`BLOCKED_EXTERNAL` + `NEXT_EXPERIMENT`，不能接 production。

## Summary Table

| Capability | Round 1 | Round 2 | Evidence | Decision |
|---|---|---|---|---|
| Walking / General Traversability | BlindPath 0/45；Cityscapes strict 22.2% | SANPO FT valid 100%，SAFE recall 89–93%，road false-safe 36%，curb recall 0.2% | DESKTOP_REAL_DATA | NEXT_EXPERIMENT |
| Obstacle | COCO real recall 36–38% | YOLO-World BLV macro 19.8%，低于 COCO 41.8% | DESKTOP_REAL_DATA | REJECT（单一替换） |
| Occupancy-first | 未测 | depth ROI 1.5m 22%/8% FP，3m 100%/86% FP；unknown recall 不可测 | DESKTOP_REAL_DATA (ORACLE) | NEXT_EXPERIMENT |
| Last10m Search | YELLOW | 未重做，回归保持 | DESKTOP_VERIFIED | KEEP |
| Last10m Entrance | RED | 门 GT 外部阻塞；YOLO-World 0 候选 | BLOCKED_EXTERNAL | NEXT_EXPERIMENT |
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

Raw depth is not viable. Unknown Blocking Recall is `UNMEASURABLE`: local data have no boxes for unknown categories, and model predictions cannot be used as GT. Next experiment must combine walkable mask + foreground/instance mask + temporal tracking; oracle numbers are not production.

## 3. Last10m Automatic Door

Attempted sources, all external blockers:

- Official DeepDoors2 Google Drive folder: `gdown --folder` remained 0 B.
- Relabeled DeepDoors2 Drive file `1wSmFUHF9aSJkomwFdOmepMevBvkRpf3D`: server timeout.
- Dataset Ninja mirror: Cloudflare/JS page, no direct download.
- No Kaggle credentials.

BLV has `Doorway` presence labels but **no door boxes**, so Recall@IoU cannot be computed. YOLO-World with `door` / `doorway` / `glass door` / `entrance` on 1795 BLV frames produced **mean 0.0 candidates**, median 0, p90 0; latency 82ms. Candidate Stability is therefore degenerate (std 0.06). This is a negative result, not a door proposal.

Status: `BLOCKED_EXTERNAL`, `NEXT_EXPERIMENT`. Do not integrate.

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
- Added YOLO-World BLV baseline, depth occupancy oracle, door candidate probe.
- 173 JVM tests / 0 failures remain.

## 7. Next most valuable experiment

Boundary-aware walkable segmentation on SANPO + Mapillary: keep SegFormer-B0, add curb/boundary supervision and road-safe suppression; gate on **road false-safe <5%** and **curb recall >70%** on real_v2 before any Android integration.
