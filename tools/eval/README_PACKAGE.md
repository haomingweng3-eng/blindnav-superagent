# BlindNav 当前项目包 — 2026-11-03

来源仓库：`https://github.com/haomingweng3-eng/blindnav-superagent`
打包 commit：`776a3fb`

## 包含内容

- `blindnav-app/`：Android 工程源码、Gradle 配置、测试、治理文档、`BLINDNAV_TECH_ROUND2.md`、`BLINDNAV_AUTO_PROGRESS.md`、`eval/real_v2/` 证据 JSON
- `real-eval/eval/`：离线评测脚本（SANPO、Cityscapes、UrbanEgo、BLV、门检测、occupancy、multi-head）
- `real-eval/results/`：所有评测结果 JSON/CSV
- `blindnav-text-agent/`：Text Agent 源码、报告和日志

## 未包含（体积原因）

- `blindnav-real-eval/data/`：约 6.7GB（SANPO、UrbanEgo、BLV、Cityscapes、OpenImages、Mapillary）
- `blindnav-real-eval/models/`：约 145MB 训练权重
- `blindnav-app/app/libs/`、`app/build/`、根目录 `asr/`、`tts/`、`device-test-*`
- `blindnav-app/.git/` 历史

## 重要：API key 已脱敏

原 `blindnav-app/app/src/main/assets/index.html` 里有明文 DeepSeek key。包内已替换为
`sk-REDACTED_ROTATE_BEFORE_USE`。使用前请先轮换原 key，不要在云电脑里复用旧 key。

## 快速开始

```bash
cd real-eval
python -m venv .venv
# 依赖：torch transformers onnxruntime ultralytics pyarrow pillow numpy scikit-learn
# 数据/模型需从原开发机补回 data/ 和 models/ 后再跑评测
```

报告优先读：

- `blindnav-app/BLINDNAV_TECH_ROUND2.md`
- `blindnav-app/BLINDNAV_AUTO_PROGRESS.md`

## 当前结论摘要

- Walking：real_v2 road false-safe 36.0% → 16.5%（TTA 14.2%），corridor 95%，curb 0 → 31.6%（v5+v6 ensemble）；multi-head 原型因算力只跑 80 帧/2 epochs，欠拟合。
- Obstacle：世界坐标 3D 体素 63.6% recall / 18.0% FP；≥4 cells 45.5% / 11.7%；尚无 recall ≥50% 且 FP <15% 的工作点。
- Door：域内 95.7% Recall@0.3；外部多域 69.4%（最终）/75.1%（中间），候选 6.3–11.4；几何排序 top3 外域 56.1%。
