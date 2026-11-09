# P0 独立核查刷新复核（2026-11-07）

## 结论

**PASS（交付完整性）；INCONCLUSIVE（实质性复现门禁）。**

刷新后的 `outputs/blindnav-superagent-20261107.zip` SHA-256 为：

`cf83c51ff29b9319671a630c6b0748d3ea8deaf9c826c9892bf1082da150808e`

以下六个文件均存在于 ZIP，路径可由 `unzip -l` 复核：

1. `docs/phase_reports/p0_20261107/sanpo_gcs_object_audit.json`
2. `docs/phase_reports/p0_20261107/sanpo_imu_gap_report.md`
3. `docs/phase_reports/p0_20261107/tumvi_basalt_source_audit.md`
4. `docs/phase_reports/p0_20261107/environment_manifest.json`
5. `docs/phase_reports/p0_20261107/dataset_inventory.csv`
6. `docs/verification/P0_INDEPENDENT_AUDIT_20261107.md`

## 内容关系与阻断

五个 P0 报告现在位于 `docs/phase_reports/p0_20261107/`，而原独立审查报告位于 `docs/verification/`，目录职责清楚。P0 报告之间的关系也基本清楚：GCS JSON 给出三个 prefix 的对象数和零名称匹配；IMU gap 报告引用该 JSON 并把结论限定为三 prefix；TUM-VI/Basalt 报告明确 exact file MD5 为 **PENDING**；环境 manifest 标记 `P0_PARTIAL_NOT_PASSED`；inventory 标记 TUM-VI 为未下载。

但原独立审查报告是对旧 ZIP 的历史结论，正文仍写“当前工作区/压缩包不包含五个文件”。刷新 ZIP 已改变该事实，因此该报告不应被当作刷新快照的现状结论；它与新路径之间缺少明确的“已被刷新证据取代”标记。此为文档时效性问题，不影响六文件交付完整性。

本复核只确认文件存在、路径、SHA-256 和报告之间的声明关系，**不能替代重新执行 GCS JSON API 查询**，也不能独立证明 JSON 中的对象计数、分页完整性、过滤器覆盖范围或远端抓取时点。`sanpo_imu_gap_report.md` 自身说明 live GCS 查询未用 gcloud，而采用只读 JSON API；TUM-VI 文件未下载，MD5 仍待完成。因此：P0.1 三 prefix 证据交付可判 PASS（artifact-complete），P0/P0.3 实质门禁仍 INCONCLUSIVE/PARTIAL，P1 继续 HOLD。

## 可复核命令

```sh
shasum -a 256 outputs/blindnav-superagent-20261107.zip
unzip -l outputs/blindnav-superagent-20261107.zip | rg 'docs/(phase_reports/p0_20261107|verification/P0_INDEPENDENT_AUDIT)'
unzip -p outputs/blindnav-superagent-20261107.zip docs/phase_reports/p0_20261107/sanpo_gcs_object_audit.json
unzip -p outputs/blindnav-superagent-20261107.zip docs/phase_reports/p0_20261107/tumvi_basalt_source_audit.md
unzip -p outputs/blindnav-superagent-20261107.zip docs/verification/P0_INDEPENDENT_AUDIT_20261107.md
```
