# P0 独立核查（2026-11-07）

审查对象：工作区 `referenced-chatgpt-conversation-this-is-an` 及其内的 `outputs/blindnav-superagent-20261107.zip`。目标分支声明为 `agent/corridor-obstacle-split-ad0888814215`（见压缩包内 `docs/PROJECT_REVIEW_AND_ROADMAP_20261107.md:9`）。本审查只读；除本报告外未修改文件。

## 总结门禁

**结论：INCONCLUSIVE（不能批准 P0.1 完成；P1 应继续 HOLD）。**

当前可复核工作区不包含被点名的五个 P0 证据文件：`sanpo_gcs_object_audit.json`、`sanpo_imu_gap_report.md`、`tumvi_basalt_source_audit.md`、`environment_manifest.json`、`dataset_inventory.csv`。压缩包文件清单与内容搜索均没有这些路径。因此，GCS 对象计数、无 IMU 文件的分母、TUM-VI/Basalt 来源/许可/MD5 门禁都无法由本工作区独立复现；这不是对结论为真的证明，而是证据缺失。建议 P0.1 保持未完成/待补证，P1 保持 HOLD，直至五个原始报告及其命令输出和时间戳进入可审计 checkout。

## 逐项证据

1. **GCS 对象计数：INCONCLUSIVE。** `unzip -l outputs/blindnav-superagent-20261107.zip | rg 'sanpo_gcs_object_audit|dataset_inventory'` 无匹配；工作区 `rg --files` 也无匹配。没有对象列表、prefix、分页/过滤规则、抓取时间或原始命令，无法核验计数分母或重复/遗漏。
2. **“无 IMU 文件”结论：仅有二手摘要，不能作为 P0 证据。** 压缩包内 `docs/PROJECT_REVIEW_AND_ROADMAP_20261107.md:80-85` 写明本地 SANPO sessions “no real RGB+IMU observation files”，而同文件 `:42-46` 将该数据用于 481-frame A/B 并报告 9/295、13/481、405/481。该摘要没有列出对象总数、session-prefix、文件后缀白名单、零结果命令或抓取时间；所以“无 IMU”最多是项目摘要陈述，不能独立证明全量 GCS 对象缺失。
3. **TUM-VI/Basalt 来源、许可、MD5：INCONCLUSIVE。** 五个指定 P0 文件及任何 `tumvi`/`basalt` source audit 均未在工作区或压缩包清单中出现。不能确认下载源是否官方、许可是否兼容、MD5 是实际数据文件 checksum 还是仅校验机制/脚本门禁。未见任何 `md5sum` 输出、期望值、实际值、文件大小与下载 URL 的绑定。
4. **分母、路径、时间、证据等级：FAIL（证据链不完整）。** 现有摘要给出 `9/295`、`13/481`、`405/481`（`PROJECT_REVIEW_AND_ROADMAP_20261107.md:42-46`），但没有说明 295 是 D-condition 帧总数还是有效帧分母，也没有将其与 GCS 对象数或 IMU 文件数连接。压缩包元数据时间为 2026-11-07 01:52，而当前审计日期为 2026-11-07；没有 P0 报告生成时间、环境 manifest 或命令日志来证明同一快照。

## 主动反证尝试（至少三项）

1. **反证“仓库已包含 P0 证据”：** 搜索工作区与压缩包清单中的五个精确文件名；结果为零。若存在未打包的分支文件，应能在当前 checkout 直接读取；当前无法证实。
2. **反证“无 IMU”是全量结论：** 从摘要追踪到对象级证据、session 分母、扩展名过滤和抓取时间；均不存在。一个未列出的 IMU 对象或不同 prefix 即可推翻全量结论，因此当前结论不能升级为 PASS。
3. **反证“MD5 门禁证明数据完整且来源合规”：** 搜索 `tumvi`、`basalt`、`md5sum`、checksum 输出和许可证文本；未发现指定审计文件或实际 checksum 对。MD5 机制本身不能证明文件已下载、文件内容匹配、来源可信或许可成立。
4. **反证“P1 HOLD 无依据”：** 摘要明确把 Path Keeper 标为 `BLOCKED_BY_EVIDENCE`，并要求真实同步 RGB+IMU/depth 与独立连续安全走廊真值（`PROJECT_REVIEW_AND_ROADMAP_20261107.md:40-52`）。这支持继续 HOLD 的方向，但不替代缺失的 P0 原始审计；若后续原始对象清单显示 IMU 存在，P0/P1 判断需重开。

## 可复核命令

在包含目标分支完整 checkout 的目录执行：

```sh
rg --files | rg '(^|/)(sanpo_gcs_object_audit\.json|sanpo_imu_gap_report\.md|tumvi_basalt_source_audit\.md|environment_manifest\.json|dataset_inventory\.csv)$'
unzip -l outputs/blindnav-superagent-20261107.zip | rg 'sanpo_gcs|sanpo_imu_gap|tumvi_basalt|environment_manifest|dataset_inventory'
rg -n -i 'gcs|imu|tum\.vi|tumvi|basalt|md5|checksum|license|denominator|prefix' docs outputs
sha256sum docs/verification/{sanpo_gcs_object_audit.json,sanpo_imu_gap_report.md,tumvi_basalt_source_audit.md,environment_manifest.json,dataset_inventory.csv}
md5sum <every downloaded TUM-VI/Basalt data file>
```

## 最终建议

- **P0.1：HOLD / 未完成（INCONCLUSIVE），不要标记 PASS。** 补交五个原始文件、完整 GCS 对象列表或可重放命令、明确分母与 prefix、UTC 抓取时间、环境 manifest、实际数据文件 MD5（期望值/实测值/路径）及许可证原文链接。
- **P1：继续 HOLD。** 现有路线图的研究暂停结论有摘要支持，但应在 P0 证据补齐后重新核验；任何声称“P0 已完成”或“MD5 已通过”的结论目前均超出可审计证据。
