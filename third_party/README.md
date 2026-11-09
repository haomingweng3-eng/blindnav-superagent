# BlindNav 外部技术源码与数据资源索引

> 本目录用于登记 BlindNav 计划复用的上游项目、源码入口、数据入口、许可证核验状态和集成位置。
>
> **这里先保存官方链接、版本/许可证核验记录和复用说明，不直接复制整份第三方仓库、数据集或模型权重。** 这样可以避免把上游版权、第三方依赖、非商业数据、模型权重和大型文件混进 BlindNav 主仓库。需要实际集成时，再按下方规则使用 Git submodule、独立下载缓存或经许可的最小适配代码。

## 1. 当前主选型

| 项目 | 官方源码/数据 | 在 BlindNav 的作用 | 当前状态 |
|---|---|---|---|
| **Basalt** | https://github.com/VladyslavUsenko/basalt | PC 首选 VO/VIO、相机-IMU 标定、轨迹基线 | 已选；尚未在 BlindNav 复现 |
| **TUM VI** | https://cvg.cit.tum.de/data/datasets/visual-inertial-dataset | 手持/头戴 VIO 基准；优先 room1 512×512 | 已选；文件校验/下载待完成 |
| **EuRoC MAV** | https://projects.asl.ethz.ch/datasets/euroc-mav/ | 标准双目+IMU VIO 回归 | 已选；单序列下载和许可待确认 |
| **ADVIO** | https://github.com/AaltoVision/ADVIO / https://zenodo.org/records/1476931 | 真实手机 RGB+IMU 数据格式和域差验证 | 后续候选；CC BY-NC 4.0，非商业限制 |
| **Depth Anything V2** | https://github.com/DepthAnything/Depth-Anything-V2 | 当前深度基线与候选部署实现 | 现有 DAV2 57 帧证据复用；Small 权重与其他变体许可不同 |
| **Metric3D v2** | https://github.com/YvanYin/Metric3D | 公制深度、置信度和表面法线候选 | 候选；具体 checkpoint 许可、焦距/尺度条件待核验 |
| **Harvard Mobilio** | https://github.com/Harvard-Slade-Lab/Mobilio | Surface/FillGrid/Occupancy 局部地图思想参考 | 已有参考移植；不是原始 ARFoundation 系统完整复现 |
| **SANPO** | https://github.com/google-research-datasets/sanpo_dataset | egocentric 可行走区域、障碍物和时序感知 | 已有部分数据；指定 session 原始 IMU 缺口未解决 |
| **NavAble** | https://huggingface.co/datasets/NavAble/NeurIPS_2026_BLV | 盲人/低视力无障碍设施识别数据 | 已发现；未下载/未训练 |
| **OpenVINS** | https://github.com/rpng/open_vins | Basalt 的备选 VIO 对照 | 暂缓，不与 Basalt 同时集成 |
| **VINS-Fusion** | https://github.com/HKUST-Aerial-Robotics/VINS-Fusion | 备选 VIO 对照 | 暂缓；需审查 GPL-3.0 及依赖 |
| **ORB-SLAM3** | https://github.com/UZ-SLAMLab/ORB_SLAM3 | VO/SLAM、重定位和跟踪恢复研究 | 仅在出现明确定位缺口时考虑 |
| **RTAB-Map** | https://github.com/introlab/rtabmap | RGB-D/视觉地图融合对照 | 可选；不是无 ARCore Android 即插即用组件 |
| **Open3D** | https://github.com/isl-org/Open3D | 点云、ICP、几何分析和可视化 | 按需使用 |
| **evo** | https://github.com/MichaelGrupp/evo | ATE/RPE 轨迹评估 | Basalt 实验优先采用/核对 |

## 2. 上游源码阅读入口

以下链接用于阅读和定位原始实现；具体路径会随上游版本变化，使用前必须记录实际 commit。

### Basalt
- Repository: https://github.com/VladyslavUsenko/basalt
- VIO 使用说明: https://github.com/VladyslavUsenko/basalt/blob/master/doc/VioMapping.md
- VO 使用说明: https://github.com/VladyslavUsenko/basalt/blob/master/doc/Vo.md
- 许可入口: https://github.com/VladyslavUsenko/basalt/blob/master/LICENSE
- BlindNav 复用边界：先 PC 运行；不直接复制 Basalt 算法进生产模块；先以轨迹文件和指标作为外部基线。

### Harvard Mobilio
- Repository: https://github.com/Harvard-Slade-Lab/Mobilio
- BlindNav 对照重点：上游 Vision/DepthImage、FillGrid、相机/世界坐标契约、地面高度和可见自由空间更新。
- 复用边界：保留上游版权和 MIT LICENSE；逐项核对本地参考移植与上游 commit 的差异；不能声称复现 ARFoundation/ARKit/ARCore 原始输入链。

### Depth Anything V2
- Repository: https://github.com/DepthAnything/Depth-Anything-V2
- Metric-depth 文档: https://github.com/DepthAnything/Depth-Anything-V2/tree/main/metric_depth
- Android 社区实现（仅供适配研究，需独立审查代码/依赖许可）:
  - https://github.com/shubham0204/Depth-Anything-Android
  - https://github.com/FeiGeChuanShu/ncnn-android-depth_anything
- 官方 README 区分 Small 与 Base/Large/Giant 权重许可；不得把代码许可证自动套用到所有 checkpoint。当前先复用既有 DAV2 57 帧结果，不重复跑同一实验。

### Metric3D v2
- Repository: https://github.com/YvanYin/Metric3D
- 复用边界：先核对具体 checkpoint 的模型卡/权重许可；输入焦距、相机内参、预处理和米制反变换必须记录；没有独立深度真值时只报告模型差异，不宣称准确率胜出。

### 定位备选
- OpenVINS: https://github.com/rpng/open_vins
- VINS-Fusion: https://github.com/HKUST-Aerial-Robotics/VINS-Fusion
- ORB-SLAM3: https://github.com/UZ-SLAMLab/ORB_SLAM3
- 只有 Basalt 出现已证实的能力/兼容性缺口，才选择一个项目做对照；不同时引入多个定位框架。

## 3. 数据集官方入口（只登记，不把原始数据放进 Git）

### 定位 / VIO
- TUM VI: https://cvg.cit.tum.de/data/datasets/visual-inertial-dataset
- EuRoC MAV: https://projects.asl.ethz.ch/datasets/euroc-mav/
- ADVIO: https://zenodo.org/records/1476931
- KITTI Odometry: https://www.cvlibs.net/datasets/kitti/eval_odometry.php
- TartanAir: https://tartanair.org/
- SANPO: https://google-research-datasets.github.io/sanpo_dataset/

### 深度 / 三维几何
- NYU Depth V2: https://cs.nyu.edu/~fergus/datasets/nyu_depth_v2.html
- DIODE: https://diode-dataset.org/
- ETH3D: https://www.eth3d.net/
- iBims-1: https://www.albert-janesch.de/ibims/
- Hypersim: https://github.com/apple/ml-hypersim
- Virtual KITTI 2: https://europe.naverlabs.com/proxy-virtual-worlds-vkitti-2/
- TUM RGB-D: https://cvg.cit.tum.de/data/datasets/rgbd-dataset
- ScanNet: https://www.scan-net.org/ScanNet/

### 语义 / 无障碍感知
- NavAble: https://huggingface.co/datasets/NavAble/NeurIPS_2026_BLV
- Mapillary Vistas: https://www.mapillary.com/dataset/vistas/
- BDD100K: https://bdd-data.berkeley.edu/
- Cityscapes: https://www.cityscapes-dataset.com/license/
- Open Images: https://storage.googleapis.com/openimages/web/index.html

## 4. 许可证与公开仓库规则

1. **源码、数据集、模型权重、第三方依赖分别核验。** 一个 GitHub repo 的 LICENSE 不自动覆盖其下载权重、训练数据、子模块和外部资产。
2. **不把 ADVIO 等 CC BY-NC 数据用于商业产品默认训练/分发。** 需遵守署名、非商业及其他具体条款。
3. **Depth Anything V2 权重按具体变体核验。** 官方说明 Small 权重 Apache-2.0，Base/Large/Giant 权重 CC BY-NC-4.0；每个具体 metric checkpoint 仍需检查对应模型卡/文件许可。
4. **GPL 项目谨慎集成。** OpenVINS、VINS-Fusion、ORB-SLAM3 的 GPL 条款可能影响分发方式；先作为独立研究工具使用，商业集成前进行法律审查。
5. **不把数据集、checkpoint、视频、原始用户数据或大型二进制直接提交 Git。** 使用官方链接、下载脚本、manifest、校验和和忽略规则。
6. **不复制上游整仓库到本项目目录。** 若确需固定源码版本，优先用 Git submodule 指向官方仓库的明确 commit；同时保存 LICENSE、NOTICE、上游 URL、commit SHA、变更说明和依赖清单。若 submodule 不适用，使用经许可的最小代码摘录并保留版权声明。
7. **公开前检查** token、个人路径、用户名、私有数据、未授权模型权重和不可再分发媒体。

参考：
- Depth Anything V2 官方 README/许可说明: https://github.com/DepthAnything/Depth-Anything-V2
- Depth Anything V2 LICENSE: https://github.com/DepthAnything/Depth-Anything-V2/blob/main/LICENSE
- Basalt 上游 LICENSE: https://github.com/VladyslavUsenko/basalt/blob/master/LICENSE
- Hugging Face license 标识说明: https://huggingface.co/docs/hub/en/repositories-licenses

## 5. Git Submodule / 源码导入的准入门槛

目前不批量添加 submodule。每个项目必须满足以下条件后才能导入：

- 已经在 BlindNav 的一个明确实验中产生可复现收益，或是当前必需的核心基准工具；
- 上游 commit 已固定，不跟踪浮动 main/master；
- 主仓库、submodule、第三方依赖和模型权重的许可证都已核对；
- 写明上游源码与 BlindNav 适配代码的边界；
- 能在独立实验环境中构建，不污染生产工作树；
- 记录维护者、上游状态、版本和移除方案。

**当前唯一优先验证的上游源码是 Basalt；其余保持索引状态，不因为“研究过”就全部拉进仓库。**

## 6. 当前状态标签

- `DISCOVERED`：已找到官方入口；
- `LICENSE_REVIEW_PENDING`：具体版本/权重/依赖许可尚待确认；
- `SELECTED_NOT_RUN`：已选型但未运行；
- `RUNNING_BASELINE`：已开始真实基准运行；
- `REPRODUCED`：结果可在记录的环境和数据版本中重复；
- `INTEGRATED`：已接入 BlindNav，并通过有意义的对照实验；
- `DEFERRED`：暂缓，需明确触发条件。

状态必须由证据更新，不能把 DISCOVERED/SELECTED_NOT_RUN 升级为 REPRODUCED 或 INTEGRATED。
