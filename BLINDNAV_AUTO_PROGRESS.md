# BlindNav Auto Progress — 2026-11-03

1. **今天真实改善了什么？**  
   - Walking road false-safe：real_v2 36.0% → 16.5%，frozen45 10.0% → 1.9%；无盲道 corridor 保持 95.0%。  
   - Obstacle class-independent occupancy：从 2.3% recall 提升到 52.3% recall（FP 37.4%），第一次有可用信号，但仍远高于 10% False STOP 门槛。  
   - Door：域内 held-out Recall@IoU0.3 95.7%、平均候选 2.72，首次达到目标；外部 OpenImages 仅 46.8%，跨域未达。

2. **当前最大的 RED 是什么？**  
   - Walking：curb recall 仍为 0，Cityscapes 第二域 road false-safe 33.3%。  
   - Obstacle：False-positive 37.4%，远高于 ≤10% 的目标。  
   - Door：跨域召回 46.8%，不能直接接 production。

3. **有没有需要人决定的事情？**  
   `NO_HUMAN_ACTION_REQUIRED`

下一轮自动继续：P0 补 curb/boundary 监督；P1 做深度补全与在线地面估计以压低 occupancy FP；P2 用更多真实入口数据做门检测域泛化。未达到上手机门槛，不请求真机测试。
