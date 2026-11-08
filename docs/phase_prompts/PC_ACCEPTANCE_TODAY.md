# 明日验收：今天交付 PC 演示，不再以消融或新 VIO 复现作为前置

这是用户明确提出验收期限后的交付优先级调整，替代今天执行队列中 `RUN_CONTROLLED_2X2_NOW.md` 的“唯一前置”地位；不推翻研究方案，消融留到演示冻结后。手机/Android 仍最后，生产安全逻辑不变。

## 唯一交付目标

交付一个能启动、能在已有 SANPO 输入上实际运行现有模型、能查看原始状态/原因和视频、能保留日志的 PC **离线辅助导航研究原型**。不是临时移植整个 Harvard Unity 应用，不承诺真实自主导航或实时 VIO。

固定技术：既有 v5/SegFormer + **相对** DAV2 Small + 原地面标定 + 已有 Mobilio-inspired Surface/Occupancy/fusion。记录位姿来自数据集；GT 只按原评估器的约定用于参考评分，不冒充当前预测。Basalt、OpenVINS、Metric3D、RTAB-Map 不在验收前新装；不要训练、扩大数据下载或换手机。

官方依据：
- Mobilio 上游发布 C# 算法及实验数据，应用依赖 Unity/ARFoundation/ARKit/ARCore，不是本项目普通手机输入的即插即用成品：https://github.com/Harvard-Slade-Lab/Mobilio
- DAV2 相对模型与另行微调的 Metric 模型不同：https://github.com/DepthAnything/Depth-Anything-V2/blob/main/metric_depth/README.md

## 新启动器：`pc/tools/acceptance_demo.py`

### 主交付：实际离线推理 → 输出视频/CSV → 可交互结果页面

使用之前成功跑过 E3 的 **同一台电脑、同一个 Python 环境、现有数据和权重**。执行者从之前命令/配置恢复 SANPO_REAL、V5_CHECKPOINT、PYTHON；不要再次让用户填写设备信息。

```bash
# 在已有开发分支的隔离工作区、仓库根执行；不清理用户未提交改动。
# 三个变量沿用 E3 的真实值。
"$PYTHON" pc/tools/acceptance_demo.py --infer \
  --data-root "$SANPO_REAL" --checkpoint "$V5_CHECKPOINT" \
  --device mps --open
```

原来是 CPU/CUDA 环境则对应使用 `--device cpu/cuda`，不因本例而切换环境。

启动器调用 **现有** `mobilio_sanpo_ab.py --conditions production`；只运行生产条件，不运行 2×2，不重新写算法。启用 HF/Transformers 离线模式，禁止自动下载。生产评估器已有的 split-C/Mobilio 两臂会按原逻辑计算，本启动器不重构它。

输出到新的 `pc/out/acceptance_<UTC>/`：
- `production_replay.mp4`：原评估器实际渲染的视频；
- `per_frame.csv`、`summary.json`、原评估器其他产物；
- `inference.log`：完整 stdout/stderr；
- `index.html`：可本地打开的结果查看器，支持场景、逐帧、播放/暂停、状态、原因和展示性朗读；
- `demo_manifest.json`：结果 CSV 哈希、代码版本和显示统计。

视频编码取决于既有 OpenCV；浏览器无法解码时用本机播放器打开同一 mp4，不能说是模型失败。播放速度不等于推理速度。页面滑块与视频不自动同步。

### 保底：历史证据回放（不能算新推理通过）

```bash
python pc/tools/acceptance_demo.py --open
```

只需 Python 标准库和已有完整仓库；读取已提交的 `on_per_frame.csv.gz` 与最多四个对应帧的像素化诊断图。不安装模型。页面明确标注“历史结果回放 · 不运行模型”，所有帧保留 UNKNOWN/失败；没有对应图的帧明确显示缺图，不用其他帧假装对应。

历史模式可用于网络/环境故障下展示既有工作，但不能冒充本次实际推理、实时识别或最终产品验收。`--infer` 失败时不自动切回历史模式掩盖失败。

## 实际执行要求

1. 一次检查现有资源与输出目录，然后直接运行主交付入口。没有实质代码变化不重跑整套测试。
2. 如有调用/输出接口错误，只修最小集成点；保留第一条完整错误与最终运行日志，不重新写研究计划。
3. 缺本地数据/模型时，一次性准确报告缺项；不能用合成模型、GT 输入或旧 CSV 伪造新推理。
4. 不为好看而改变 UNKNOWN/CLEAR/STOP、不改路径安全阈值，不只选择成功帧或去掉失败记录。
5. 初版视频和原评估器 JSON/日志可能含原图、人员、绝对路径。**只留本机用于受控演示，不自动公开上传**；公开仓库只交脱敏摘要/日志及允许公开的派生产物。像素化不等于完全匿名。
6. 固定该启动方式后，不再为了额外审计而改执行入口。消融、Basalt 等转到演示冻结后的研究队列。

## 交回与验收口径

执行后给：真实命令/退出码、结果目录、模型推理是否完成、视频是否可播放、页面是否可逐帧操作，以及一段连续演示录屏（隐私审核后再分享）。

- **模型推理验收**：实际生产条件跑完，并生成非空 CSV 与视频；记录处理帧数和实际耗时。不能只看窗口存在。
- **界面验收**：场景切换、播放/暂停、前后帧、UNKNOWN/OBSERVE/CLEAR 原样可见；观察提示不能显示成运动授权。
- **证据验收**：每条显示信息能追溯到 CSV，缺图不伪造；历史回放和本次离线推理明显区分。
- **不在本次范围**：实时手机、真实 VIO、方向轨迹正确率、用户行走安全认证。

若验收方要求真实手机自主导航，以上离线交付不能算满足该要求；必须坦诚展示当前能力和范围，不能因截止时间改口。

## 此次真实验证记录

作者环境运行 `python -m pytest -q pc/tests/test_acceptance_demo.py`：**6 passed**，使用合成 CSV 夹具，仅测试读取/统计/重复帧拒绝/脚本注入防护/启动参数/禁止失败伪装。
Chromium 使用 `set_content` 加载同一生成 HTML：逐帧、播放结束、场景选择和 UNKNOWN 无授权显示通过，无 JS 错误；此环境的 file/localhost URL 导航被策略禁止，因此**没有验证本机文件打开的完整传输路径**，也没有测试浏览器语音实际出声或视频解码。
**未在作者环境加载真实 v5/DAV2/SANPO，也未声称完成本次端到端推理。**原 E3 环境的最终运行由执行者提供日志。

能力评分沿用 **3.6/10**；提交启动器和界面不增加导航准确性分。演示交付可独立检查，不能用主观分数替代真实运行。
