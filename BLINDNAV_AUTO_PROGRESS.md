# BlindNav Auto Progress — 2026-11-03

1. **今天真实改善了什么？**  
   - Walking：false-safe v5 **16.5%**、TTA **14.2%**，corridor **95%**；v5+v6 ensemble 把 curb 从 **0 → 31.6%**。共享 backbone 双 head 原型已实现，但本机只跑 80 帧/2 epochs，欠拟合（FS 36.5% / curb 13.4%），不作为路线结论。  
   - Obstacle：世界坐标 3D 体素 + 人体宽度/膝上高度，单帧 **63.6% recall / 18.0% FP**；≥4 cells 为 45.5% / 11.7%。8 帧世界累积反而更差。  
   - Door：真实 OpenImages storefront 多域训练把外部召回从 **58.4% → 69.4%（最终）/75.1%（中间）**，但平均候选 **6.3–11.4**；几何排序 top3 外域 **56.1%**、域内 90.6%，不能把候选压到 ≤3 还保住召回。

2. **当前最大的 RED 是什么？**  
   - Walking：false-safe 仍 14.2–16.5%，curb 31.6%，没有同一配置同时达标；多头模型欠拟合。  
   - Obstacle：没有点同时满足 recall ≥50%、FP <15%（最好 63.6/18.0 或 45.5/11.7）。  
   - Door：召回 69–75%，但候选数 6.3–11.4，远超 ≤3。

3. **有没有需要人决定的事情？**  
   `NO_HUMAN_ACTION_REQUIRED`

下一轮自动继续：Walking 在完整数据上把双 head 训练到收敛；Obstacle 做深度补全与在线地面估计；Door 做候选排序/relation，把 11.4 个候选压到 ≤3。未达到上手机门槛，不请求真机测试。
