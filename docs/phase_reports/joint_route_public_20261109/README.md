# 2026-11-09：公开 SANPO 数据实际运行，导航可用性仍未通过

## 结论

**阶段裁决：PC 主线 ADJUST；手机、真人路测和路演能力验收 HOLD。**

本轮在一台新的 Linux CPU 环境下载并验证了公开数据与原 DAV2 Small 权重，实际完成 36 张 SANPO 图像的神经网络推理，以及相同输入上的旧查询 / 联合路径候选比较。组件计算已完成；**结果没有证明导航可用，新候选没有性能提升**。完整原 v5 生产链仍缺原微调权重，不能把本轮有人工语义辅助的实验写成生产复现。

执行基线：`b0a23bf7731c02ca557e29a235a729137e1f8a10`，分支 `agent/corridor-obstacle-split-ad0888814215`。运行时新增源码尚未提交，因此 `run_metadata.json` 同时记录基线 commit、工作区状态和实际执行源码 SHA256。具体文件哈希由 `root_verification.json` 再次核对。

## 1. 实际执行了什么

| 项目 | 本次实际记录 |
|---|---|
| 公开数据 | SANPO-Real，原冻结 inventory 中 3 个 session，各取前 12 个标注帧 |
| 图像帧键 | 每段 `000000…000055`，步长 5，共 36 帧；每段约 3.67 秒 |
| 数据完整性 | 114/114 个文件的大小和 SHA256 匹配；150,723,611 字节；0 失败 |
| 深度权重 | 原 `depth-anything/Depth-Anything-V2-Small-hf`，revision `5426e4f0f36572d16453bbda7a8389317b1bef99`；3 个模型文件哈希匹配原 inventory |
| 新神经网络推理 | 36 次真实 DAV2 推理，36 张有限值 `360×640` 相对深度图；非模拟输出 |
| 推理阶段耗时 | 28.639 秒，包含 4.307 秒模型加载；模型计算中位数 0.461 秒 / 帧，P95 0.609 秒 / 帧 |
| 有效位姿 | 33/36；每个 session 的第 0 帧为 NOT_READY |
| 旧新比较 | 67.417 秒，退出码 0；72 组配对，144 条 arm 记录，72 份证书 |
| 比较阶段推理 | 使用上一步刚生成并逐项校验的 36 份缓存，0 次重复模型推理 |

时间来自这台 CPU 的实际运行。模型计算时间不包含完整导航链；比较时间包含两个输入条件和两个独立地图分支，均不能当作手机实时性能。

数据和模型来自官方公开资源：[SANPO](https://github.com/google-research-datasets/sanpo_dataset)、[DAV2 Small Transformers checkpoint](https://huggingface.co/depth-anything/Depth-Anything-V2-Small-hf)。DAV2 原始输出是相对深度，需要现有地面标定后才进入米制地图。SANPO 官方发布了 corrected camera poses；本次下载的是冻结 inventory 所列 `fixed_camera_poses.csv`。没有以其他模型替换 v5 后沿用原名字。

## 2. 两个新实验条件及边界

| 条件 | 输入给决策链的辅助信息 |
|---|---|
| `public_gt_reference` | 数据集人工语义、参考深度、参考深度拟合的地面平面，以及记录位姿 |
| `public_gt_calibration_dav2` | 实际 DAV2 相对深度、人工语义和人工地面标定掩码，以及记录位姿；参考深度仅用于评分 |

两者都以 READY 记录轨迹计算目标方向，使用了未来位置，是明确的离线辅助目标。三段启动切片的目标也不同于完整 481 帧轨迹的目标。因此只在同一条件、本次相同切片内比较旧新查询，不能与旧 481 帧数字直接比较，不能宣称自动发现直路。

这两个条件不是历史 `C_gt_dav2`。历史 C 的标定掩码仍依赖 v5；本轮保留其原定义。参考深度条件和 DAV2 条件之间还存在地面平面 / 高度差异，不能把它们的差值全部归因于深度网络。

首次 harness 审查发现 NOT_READY 位姿仍被使用，已修正后重新比较。正式结果来自 `pc/out/joint_route_public_20261109_checked`：3 个 NOT_READY 帧保留在总分母中，全部 UNKNOWN，不参与标定、建图、查询或参考方向评分。初次结果保留在本地并标为 `PRE_REVIEW_SUPERSEDED`，不用于本报告结论。神经网络推理没有重复。

## 3. 正式结果：没有可用性收益

| 输入条件 | 指标 | 原查询 | 联合路径候选 |
|---|---|---:|---:|
| 参考深度 + 人工语义 | 总方向授权 | 3/36 | **0/36** |
| 同上 | 有效位姿帧授权 | 3/33 | 0/33 |
| 同上 | 可通行代理帧授权 | 1/9 | 0/9 |
| 同上 | 危险代理帧授权 | 0/16 | 0/16 |
| DAV2 + 人工语义 / 标定掩码 | 总方向授权 | **0/36** | **0/36** |
| 同上 | 有效位姿帧授权 | 0/33 | 0/33 |
| 同上 | 可通行代理帧授权 | 0/9 | 0/9 |
| 同上 | 危险代理帧授权 | 0/16 | 0/16 |

代理标签对应记录轨迹参考方向，不是各自选中方向的安全真值。9、16 的分母不涵盖全部帧，也不是独立行走次数；这些比率不是直行正确率、碰撞率或绕障成功率。取消参考条件中的 3 次授权，没有证明阻止了 3 次危险导航：其中 1 帧属于可通行代理，0 帧属于危险代理，2 帧不属于两类代理。

| 候选拒绝 / 阻断原因 | 参考条件 | DAV2 条件 |
|---|---:|---:|
| 通行带缺少地面 / 占据证据 | 23 | 20 |
| 未确认的即时人体占用 | 7 | 6 |
| 已确认的即时人体占用 | 3 | 0 |
| 地面标定失败 | 0 | 7 |
| 位姿 NOT_READY | 3 | 3 |
| 状态合计 | UNKNOWN 26 / OBSERVE 7 / BLOCKED 3 | UNKNOWN 30 / OBSERVE 6 / BLOCKED 0 |

### 已具体定位的两处失败

**标定链：** 33 个有效位姿帧中，26 个获得可用几何，其中 19 个为当次接受、7 个复用先前标定。另 7 个失败均为 session `-PqSDmiEe2pXjmYHgxh4YEBsj0T5LU10` 的帧 `5,10,15,20,25,30,35`；原因 `low_confidence+stale`，置信度约 0.281–0.347，虽然有 2385–2500 个地面采样像素。本轮未修改标定算法或阈值，原始 `GroundFit.to_log()` 已保存在证书中。

**地图证据链：** 参考条件 23/23 个完成路径搜索的帧、DAV2 条件 20/20 个完成路径搜索的帧，所有 181 个候选方向均含未知地面格和未知占据列。被选作拒绝诊断的路径均在起始人体足迹处就缺证据。当前默认近盲区豁免参数为 2.2 米，而候选要求检查共同的前 2 米通行带及当前人体足迹；旧查询会跳过部分近处未知区域，新候选没有将其填为 FREE。

这直接暴露了近处可观测性、地图证据和授权要求之间的不匹配。参考输入条件也出现这一问题，因此不能只说“深度模型太差”。但仅凭这些短启动切片，还不能确定成熟地图的覆盖率，或把所有拒绝归于单一代码缺陷。地面可见列也不证明从脚到头的全部体积空闲；证书明确没有物理安全或部署认证。

## 4. 新代码解决到哪里

新增 `pc/blindnav_pc/mobilio_joint_route.py`，只用于独立 PC 候选：在相同快照、起点和角度上检查人体扫掠带，寻找两层共同满足要求的候选；保留即时占用优先级、原距离要求和未知语义。地面检查至 5 米、障碍通行带检查至 2 米，证书明确共同区间为前 2 米，未宣称全 5 米均有障碍净空证据。

新增 `pc/tools/joint_route_candidate_eval.py`：每个条件的旧新分支共享只读感知产品，各自拥有独立地图；对地图全部证据、可见列、索引及时间窗口做一致性和查询不修改状态检查。实际深度、语义、角色图、内参、姿态和高度均参与共享输入哈希。

14 个人工构造地图用例和 3 个既有融合用例通过。人工用例覆盖异向拼接、同向可通行、前阻侧通、窄缝、未知近场及即时阻断。这证明对应代码行为；本次真实切片没有新候选授权正例，**真实场景下的正向可用性仍未验证**。

原生产查询、地图更新、标定、衰减及 Android 代码保持原样。项目能力评分维持 **3.6/10**，沿用原分项 `7/5/2/3/3/2/6`。本轮增加运行和失败定位证据，不增加导航能力分。

旧 481 帧 CSV 的单独核查也已实际执行：49 条方向授权中 45 条地面 / 障碍选中角度不同。见 `historical_csv_audit/`；这不是新模型推理，也不是 45 次碰撞。

## 5. 完整原链的确定缺项与下一目标

原微调文件未进入可获取的模型发布流程。仓库 [DOWNLOAD_LINKS.md](../../../tools/eval/DOWNLOAD_LINKS.md) 明确说明它没有公开下载链接，需从原开发机补齐：

- 文件：`segformer_sanpo_b0_v5.pt`
- 大小：`14,947,603` 字节
- SHA256：`952c2a156ab43090aa7aa4088a812b21f0639e75bb2eb8d8d7851696a17696bf`

**唯一近期交付目标仍是：在 PC 上，从冻结公开输入实际运行原感知链，定位同一路径的证据在哪一步丢失，并保留完整失败结果。** 先回收这一个已有权重，随后使用原输入完成完整 481 帧旧新对照；有可验证缓存则复用。只为排查近场和标定问题改一个因素，并分别报告可用性与证据一致性。不得通过放宽 UNKNOWN、延长过期上限或填充未观测区域制造授权。手机和最后 10 米扩展暂不推进。

工程问题是模型资产与复现入口没有闭合；算法问题是标定、地图可见性和通行授权尚未匹配；研究问题是原有帧级危险代理不能回答所选路径是否正确。缺 v5 解释了完整复现的阻塞，但不能解释或掩盖本轮已经看到的下游失效。

## 6. 从公开资源复现本次组件实验

在仓库根目录，使用 Python 3.12 的隔离环境。已执行版本见 `runtime_versions.json`。下面从空缓存运行会产生 36 次真实 DAV2 推理；无需 v5，但仍是上面明确声明的人工语义辅助实验。

```bash
python -m venv .venv-public-replay
.venv-public-replay/bin/python -m pip install 'torch==2.15.0+cpu' --index-url https://download.pytorch.org/whl/cpu
.venv-public-replay/bin/python -m pip install 'numpy==2.3.5' 'transformers==5.22.1' 'opencv-python-headless==5.0.0.93' 'huggingface-hub==2.4.0' 'safetensors==0.8.0'

BLINDNAV_WORK="$PWD/pc/out/public_replay_inputs"
.venv-public-replay/bin/python pc/tools/fetch_frozen_sanpo_subset.py \
  --data-root "$BLINDNAV_WORK/sanpo-real" \
  --manifest-out "$BLINDNAV_WORK/data_download_manifest.json" --per-session 12
.venv-public-replay/bin/python pc/tools/fetch_frozen_depth_model.py \
  --inventory docs/phase_reports/e3_input_audit_20261107/input_inventory.json \
  --out "$BLINDNAV_WORK/dav2"
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 .venv-public-replay/bin/python \
  pc/tools/joint_route_candidate_eval.py \
  --data-root "$BLINDNAV_WORK/sanpo-real" \
  --depth-snapshot "$BLINDNAV_WORK/dav2" \
  --depth-revision 5426e4f0f36572d16453bbda7a8389317b1bef99 \
  --device cpu --torch-threads 4 --max-width 640 --expected-frames 36 \
  --inference-cache "$BLINDNAV_WORK/depth_cache" \
  --out pc/out/public_replay_result
```

输出目录已存在时入口会拒绝覆盖。上述下载器均检查原 inventory 哈希；数据变动不会被静默接受。完整 481 帧数据 inventory 约 2.114 GB，本轮没有以其总数冒充实际运行量。

## 7. 证据索引

- `summary.json`、`before_after.csv`、`per_frame.csv`：正式统计与逐帧旧新输出。
- `route_certificates.jsonl.gz`：完整 72 份证书的无损 gzip，含候选搜索与地面标定原始记录。
- `run_metadata.json`、`run.log`：正式比较的实际参数、代码哈希、开始 / 完成时间、退出码与日志。
- `fresh_inference/`：原始 36 次新推理记录、原始 metadata、实际 stdin 源码与保留的当时 harness。`execution_record.json` 明确是事后关联记录；未记录的启动时间留空，未重建原始 stdout。
- `shared_inference_manifest.json`：缓存文件与实际深度数组的哈希，并与新推理阶段逐帧关联。
- `data_download_manifest.json`、`model_download_manifest.json`、`input_inventory.json`：来源、大小和校验记录。
- `failure_analysis.json`、`execution_validation.json`、`root_verification.json`：拒绝原因分解、运行后检查及独立复核。格子累计数是每帧诊断路径之和，不是全局唯一格子数量。
- `historical_csv_audit/`：旧 481 帧 CSV 的独立核查，不能混入本轮 36 帧模型结果。

原图、模型权重和深度张量缓存保留在执行环境；本目录提交代码、来源信息和结构化证据。SANPO 数据由官方按 CC BY 4.0 发布，公开复用应保留作者与来源；本报告不包含邀请截图或私人联系方式。
