# 可自助下载的链接 — BlindNav

这份清单给云电脑助理用。能公开下载的都在这里；本地录制/微调产物没有链接，需要从原开发机补。

## 1. 公开数据

### SANPO
- 官方说明与选择性下载脚本：https://github.com/google-research-datasets/sanpo_dataset
- 数据桶：`gs://gresearch/sanpo_dataset/v0/`
- 本项目用到的 session 列表：`real-eval/manifests/sanpo_sessions.txt`
- 示例：
  ```bash
  gcloud storage cp -r "gs://gresearch/sanpo_dataset/v0/<session_id>" .
  ```

### BLV-Road-Nav-Accessibility
- https://github.com/Shohan29531/BLV-Road-Nav-Accessibility
- 约 244MB，含 Video Frames、Ground Truth Annotation。

### Cityscapes 分割 parquet（本项目第二域）
- https://huggingface.co/datasets/Chris1/cityscapes_segmentation
- 使用 `data/test-*.parquet` 和 `data/train-*.parquet`。

### Mapillary Vistas 图片（无标签域检查）
- https://huggingface.co/datasets/valentinamihalescu/mapillary-vistas-dataset
- 只有 testing images，无标注；官方 Mapillary 标签需登录。

### OpenImages V6 Door
- 门类标注（约 2.2GB，流式过滤 `/m/02dgv`）：
  `https://storage.googleapis.com/openimages/v6/oidv6-train-annotations-bbox.csv`
- 图片模板：`https://open-images-dataset.s3.amazonaws.com/train/<ImageID>.jpg`
- 本项目的 train ID：`real-eval/manifests/openimages_door_train_ids.txt`
- 本项目的 val ID：`real-eval/manifests/openimages_door_val_ids.txt`
- 过滤命令：
  ```bash
  curl -sL 'https://storage.googleapis.com/openimages/v6/oidv6-train-annotations-bbox.csv' \
    | awk -F, '$3=="/m/02dgv"' > door_train_ann.csv
  ```

### Roboflow door_detection（域内训练）
- Kaggle 直链（CC BY 4.0）：
  `https://www.kaggle.com/api/v1/datasets/download/dohahemdan202200701/skfjsdfsdfs`
- 3150 train / 225 valid / 225 test，单类 Door，polygon 标注需转 bbox（脚本 `eval/convert_door_labels.py`）。

## 2. 公开模型

- SegFormer-B0 Cityscapes 初始化：https://huggingface.co/nvidia/segformer-b0-finetuned-cityscapes-1024-1024
- Depth Anything V2 Small：https://huggingface.co/depth-anything/Depth-Anything-V2-Small-hf
- YOLOv8n / YOLO-World：Ultralytics 首次运行自动下载 `yolov8n.pt`、`yolov8s-worldv2.pt`
- 门检测基座：`yolov8n.pt`，微调脚本见 `eval/` 与报告第 3 节

## 3. 没有公开链接、需从原开发机补

- `blindnav-real-eval/data/urbanego/`：本地 HoloLens 录制，约 2GB，无公开地址。
- `blindnav-real-eval/models/`：本项目微调权重（SegFormer v2/v3/v5/v6、DeepLab、multi-head、门检测），约 145MB。
  - 文件名与大小见 `real-eval/manifests/models_manifest.txt`
- `blindnav-app/app/src/main/assets/asr/`、`tts/`：sherpa-onnx SenseVoice/VITS 资源，约 546MB。
  - 文件清单见 `real-eval/manifests/app_assets_manifest.txt`
- `blindnav-app/app/libs/`、`app/build/`、根目录 `device-test-*`：构建/真机产物。
- 自定义 `yolo-seg-int8.onnx`（70MB）：本项目盲道分割模型，无公开链接。

## 4. 云电脑建议顺序

1. 解压项目包，先读两份报告。
2. 只跑评测：下载 SANPO 子集 + BLV + 公开模型；UrbanEgo 和微调权重从原机补。
3. 只改 App：Android Studio 打开 `blindnav-app/`，缺 `libs/asr/tts` 时从原机补，否则只能读代码不能完整构建。
4. 不要把旧 DeepSeek key 带到云电脑；包内的 key 已脱敏。
