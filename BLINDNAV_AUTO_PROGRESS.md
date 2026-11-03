# BlindNav Auto Progress — 2026-11-03

1. **今天真实改善了什么？**  
   - Walking：road false-safe v5 到 **16.5%**，翻转型 TTA 到 **14.2%**，corridor 保持 **95%**；v5+v6 ensemble 把 curb recall 从 **0 → 31.6%**。  
   - Obstacle：ground-plane occupancy 保持 **52.3% recall / 37.4% FP**；像素级 3 帧多数把 FP 压到 **17.5%**，但 recall 掉到 22.7%，没有同时达标。  
   - Door：held-out 域内 **95.7% Recall@0.3 / 2.72 候选**；重增强把外域从 **46.8% → 58.4%**，仍未到 75%。

2. **当前最大的 RED 是什么？**  
   - Walking：false-safe 仍 **14.2–16.5%**，未到 <10%；curb 31.6% 仍低，且与 false-safe 互相拉扯。  
   - Obstacle：FP **37.4%** 远高于 ≤10%，像素级时序只能牺牲 recall 换 FP。  
   - Door：跨域 **58.4%**，离 75% 第一里程碑差 17 个百分点。

3. **有没有需要人决定的事情？**  
   `NO_HUMAN_ACTION_REQUIRED`

下一轮自动继续：Walking 统一 curb + false-safe 训练目标并补真实 boundary 数据；Obstacle 做深度补全/在线地面估计；Door 引入真实 storefront/entrance 多域数据，停止在 Roboflow 上刷分。未达到上手机门槛，不请求真机测试。
