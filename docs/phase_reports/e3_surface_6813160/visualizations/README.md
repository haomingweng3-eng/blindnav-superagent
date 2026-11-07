# E3 真实观测的可视化补充

四组 PNG 从 SANPO 真实回放生成，并对 RGB 面板整体降到 32×24 后放大；其余面板保留派生的预测角色、标定 DAV2 深度和 Surface 网格。像素化降低细节但不能保证匿名，模型派生图仍可能显示人物轮廓或场景。没有上传原始 RGB。原始数据出处：[Google Research SANPO](https://github.com/google-research-datasets/sanpo_dataset)，Waghmare 等，WACV 2025。

- [frame 75：最佳被拒绝射线终止于 NON_WALKABLE](<./-PqSDmiEe2pXjmYHgxh4YEBsj0T5LU10_75_privacy.png>)
- [frame 305：射线上没有观测](<./-PqSDmiEe2pXjmYHgxh4YEBsj0T5LU10_305_privacy.png>)
- [另一场景 frame 35：最佳被拒绝射线终止于 UNKNOWN_GAP](<./UGNwBEZMxhzlzXVyl8XUfflcvjGwdpA5_35_privacy.png>)
- [frame 35：有原算法选中射线](<./-PqSDmiEe2pXjmYHgxh4YEBsj0T5LU10_35_privacy.png>)

左上点是当前帧的地面高度带输入采样，stride 4、地面高度绝对值 ≤ 0.15m；在此之后还存在地图窗口和距离过滤，且历史地图不只包含当前帧。右下网格将世界格中心转换到手机局部坐标；红线是最佳被拒射线，蓝线是原算法选中射线，终点为实际 trace 终止距离。**线段及终点不是已验证的安全通路或行走授权。** 帧 305 没有最佳被拒射线时不画红线。

## 来源和复现

捕获回放代码位于提交 `e1e7615d8a1cead460d0774e1a6d5dc597a8d13a`，其 `pc/` 相对于 `6813160` 无差异。三个无候选帧来自一次完整 481 帧冻结回放：与原 ON 输出 962 行帧键及 16 项受检状态、命令、授权、时间与来源字段一致。frame 35 的选中射线来自单会话 121 帧补充回放：242 行及 11 项受检决策字段一致；该次使用非冻结入口，不能证明模型字节相同。

独立审查用四个捕获网格的角色及证据重建射线，与冻结 JSONL 各 181 条射线完整字典比较，共 724 条一致；这不证明所有地图单元或输入数组逐字节一致。独立审查还重算四张像素化面板及地面采样，确认其与数据一致。表中 JSON 是捕获的地图和相机位姿，NPZ 是预测角色、深度、相机参数与位姿，不含 RGB。三次回放的脱敏日志包含一次主动中断记录（KeyboardInterrupt，退出 130），未将它计为成功。

两个 `e3_capture_*.py` 是实际运行的捕获旁路，分别从原 evaluator 调用冻结全量和单 session 参数；它们写入 `/tmp/e3_visual_capture`，应先确认此目录不与现有文件冲突。`e3_render_capture.py` 与实际脚本仅在路径上作脱敏：设置 `SANPO_REAL` 和 `E3_CAPTURE_DIR`，并在仓库根运行；它读取原 RGB、捕获 NPZ/JSON 和冻结 ON 的 `rays.jsonl`，在本地生成包含未处理 RGB 的中间图，切勿直接公开。`e3_privacy_panels.py --capture-dir ... --pc-dir pc --output-dir ...` 再生成本目录的隐私保护版。脚本不改变导航状态或阈值。输出只说明离线回放，不能证明手机、TTS、VIO 或现实行走安全。
