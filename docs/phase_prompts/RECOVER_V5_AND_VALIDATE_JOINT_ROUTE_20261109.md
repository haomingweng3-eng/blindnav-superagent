# 唯一下一步：回收原 v5 权重并验证 481 帧同路径候选（2026-11-09）

**执行范围：**只在原 E3 的 PC/本地已有资源执行，不运行 Android，不接入真人运动授权，不修改生产 `mobilio_fusion`、`SafetyCoordinator`、安全阈值、深度模型或旧结果。先读 [本次独立审查](../reviews/JOINT_ROUTE_PUBLIC_INDEPENDENT_REVIEW_20261109_1050Z.md) 和 [36 帧正式回放](../phase_reports/joint_route_public_20261109/README.md)。本任务没有承诺新候选必然放行。

## 唯一目标与停止条件

将 `pc/blindnav_pc/mobilio_joint_route.py` 的同路径扫掠带检查，接入**独立离线 evaluator**，在已有 v5/相对 DAV2/原 SANPO 481 帧**同一输入**上比较旧查询和新候选各自独立的地图。证明链路可用、保留失败；候选必须出示地面与障碍同方向、同起点、同快照、人体宽度、距离和观察年龄证书。不能用 36 帧人工语义新结果代替生产链。不能把候选的 `movement_authorized` 接到真实运动授权；`full_volume_visibility_certified=False` 仍需物理传感器/验证，当前任何正例只为 PC 地图契约。

## 直接执行，不再研究选型

**0. 原开发机一次性定位资源，不大下载、不重新安装：**

```bash
git checkout agent/corridor-obstacle-split-ad0888814215
git rev-parse HEAD
find . .. -type f -name 'segformer_sanpo_b0_v5.pt' -print 2>/dev/null
# 对实际存在文件运行：sha256sum <ABSOLUTE_PATH>; stat -c %s <ABSOLUTE_PATH>
```

期望原权重 **14,947,603 字节**，SHA256 **952c2a156ab43090aa7aa4088a812b21f0639e75bb2eb8d8d7851696a17696bf**。仅在本地私有工作目录使用，不得上传公共仓库；如不存在或 hash 不匹配，记录 `V5_ASSET_BLOCKED`、路径查找证据并停止完整生产复现，不得拿另一个 SegFormer 模型冒充。

**1. 输入冻结与最小集成。**读取 E3 原始 `on_per_frame.csv.gz`、原 481 帧 manifest、原 checkpoint 和缓存（如存在）；固定同一 session/frame 顺序、RGB、GT 标签（仅评分）、v5 类别、相对 DAV2 深度、K/up/height、pose、目标角、每帧时序。严禁在条件间使用不同目标拟合或不同标定。一次旧新对照要求各地图独立演化、共享每帧只读模型产品，不变更原地图更新与衰减。当前 `joint_route_candidate_eval.py` 只支持 GT 语义的两个人工辅助条件，**没有 `--v5-checkpoint` 参数**；执行者先以最小代码扩展其隔离输入/复用 E3 原 evaluator，切勿直接以不存在参数运行。

**2. 只做目标变更对应的 17 项契约测试；代码接法变化时检查 map 初始状态、同输入 old/new、未准备位姿绝不建图或授权、即时人体占用优先、全高度未知不得声称物理安全。**不得重新跑 E3 全套 OFF/ON 或新增 display demo。

**3. 一次完整 481 帧旧新比较**（仅资源齐备时）。输出 `before_after.csv`、`per_frame.csv`、每帧/每候选证书（可 gzip）、`run_metadata.json`、`run.log`、逐 session 分子分母、哈希、退出码和 10 个明确失败例。必须检查 481 唯一键、两 arm 配对相同输入/时间/几何、NOT_READY 分母保留、模式真实模型调用次数或缓存复用证明。旧历史 D 逐帧 parity **只在预处理、目标方向、标定、时间和输入完全相同时有资格要求**；条件不等价时标 NOT_COMPARABLE，不能调参凑零差异。

**4. 报告分开列：**全部授权、confirmed-clear 代理授权、danger 代理授权、UNKNOWN/OBSERVE/BLOCKED/STOP、每个 session、原 49 帧角度不一致的去向、近场 `[0,0.5]`、`(0.5,1]`、`(1,2]` 米的地面/占据证据缺口，标定成功与复用情况。所选通道方向/扫掠真值缺失时 LEFT/RIGHT 正确性、真实绕障成功率及碰撞率全部 NOT_EVALUABLE。单凭减少放行不加分，也不得把未知填为 FREE。

**5. 如果 v5 文件仍缺失：**不要重新下载 DAV2、不要重演36帧。只从本轮已归档 `route_certificates.jsonl.gz` 做一次近场缺失分桶、定位为何起始人体足迹始终未知；把原始观察 vs 地图历史更新缺口区分开。必要时只提交最小离线分析脚本及产物，不修改生产授权。若需额外私有数据、安装或大下载，明确记录待批准项而不是暗自执行。

## 验收反馈

交本次唯一任务真实源码 SHA、实际命令、退出码、耗时、模型/数据哈希、成对分母、会话结果、完整失败证据和明确 `GO/ADJUST/HOLD/STOP`。无新实证时不得自称成功。下一轮仅按修复的证据选择一个因素（近场可观测性或地面标定），不并行 Basalt/Metric3D/手机。
