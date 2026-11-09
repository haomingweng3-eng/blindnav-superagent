# P0 最终 ZIP 独立核验（2026-11-07）

## 结论

**PASS（最终 ZIP 交付完整性）；INCONCLUSIVE（实质数据门禁）。**

已核对最终 ZIP `outputs/blindnav-superagent-20261107.zip` 的 SHA-256：

`3de5b3afe13f718167e12f257792c06bdfab5ea7ec1f2637a5466b3881a261dc`

ZIP 内确认存在：

- 五个 P0 证据：`docs/phase_reports/p0_20261107/sanpo_gcs_object_audit.json`、`sanpo_imu_gap_report.md`、`tumvi_basalt_source_audit.md`、`environment_manifest.json`、`dataset_inventory.csv`；
- 原始独立审查：`docs/verification/P0_INDEPENDENT_AUDIT_20261107.md`；
- 刷新独立审查：`docs/verification/P0_INDEPENDENT_AUDIT_REFRESH_20261107.md`。

## 历史结论核对

原始独立审查正文确实记录了旧 ZIP 中“五个文件缺失”的结论；刷新审查明确说明该结论针对旧 ZIP、已被刷新快照取代，并将刷新状态判为“交付完整性 PASS、实质复现 INCONCLUSIVE”。因此，报告没有把旧 ZIP 的缺失结论误当作最终 ZIP 的当前状态。最终 ZIP 的现状应以五个 `phase_reports` 文件及刷新审查为准。

## 限定

本次只读核验确认包内文件、路径、报告关系和指定 SHA-256；不能替代重新执行 GCS JSON API，也不能证明对象计数、分页完整性、过滤器覆盖或远端时间点。TUM-VI exact file MD5 仍为 PENDING，故 P0/P0.3 实质门禁继续为 INCONCLUSIVE/PARTIAL，P1 继续 HOLD。

## 复核命令

```sh
shasum -a 256 outputs/blindnav-superagent-20261107.zip
unzip -l outputs/blindnav-superagent-20261107.zip | rg 'docs/(phase_reports/p0_20261107/(sanpo_gcs_object_audit|sanpo_imu_gap_report|tumvi_basalt_source_audit|environment_manifest|dataset_inventory)|verification/P0_INDEPENDENT_AUDIT(_REFRESH)?_20261107)'
unzip -p outputs/blindnav-superagent-20261107.zip docs/verification/P0_INDEPENDENT_AUDIT_REFRESH_20261107.md | rg -n '旧 ZIP|历史结论|INCONCLUSIVE|P1 继续 HOLD'
```
