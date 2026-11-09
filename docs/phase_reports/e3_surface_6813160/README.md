# E3 Surface 射线诊断：阶段性回放与独立核查

状态：**REPLAY_COMPLETED；局部 INDEPENDENTLY_CHECKED；完整交付与门禁待核查**。本报告不是导航性能或部署通过结论。代码快照 `6813160c555e297b03a599eb689b8083fe4ce36a`，输入清单 SHA256 `2b4997b6c3e561e62a046fb812edbd31de2ad231596da0ab9219755f2a20e37c`。使用 SANPO 记录位姿作为离线代理，不是实际 RGB＋IMU VIO。

同一冻结输入分别关闭和开启 trace，均成功退出；每次包含 baseline 和 Mobilio 两臂，各 481 帧。独立程序逐帧核对的 17 项状态、命令、授权、时间和来源字段没有差异，帧键与顺序一致。独立检查覆盖的字段、哈希和统计见 [independent_results.json](independent_results.json)，复算程序见 [independent_check.py](independent_check.py)。这一结果不涵盖 TTS 或 Android 端到端行为。

| 射线记录状态 | 帧数 | 说明 |
|---|---:|---|
| RECORDED | 398 | 扫描过的射线上存在观测证据 |
| NO_OBSERVATION | 74 | 扫描射线上没有观测证据 |
| NO_METRIC_DEPTH | 9 | 没有可用公制几何 |

472 帧各记录 181 条射线，共 85,432 条；其余 9 帧无射线。对于最终原因为 `surface:no_candidate_ray` 的 125 帧，51 帧有射线上观测证据，74 帧没有；按预先定义的最长连续可走段排序，23 帧有最佳被拒射线（其中终止于 NON_WALKABLE 16、UNKNOWN_GAP 7），102 帧没有正长度的被拒射线。此处是 trace 的描述性统计，不证明分割、投影或地图更新中哪个环节是根因。扫描经过网格角点可能产生近零长度单元，记录中单独标记；连续长度仅覆盖原算法停止之前的已访问区间，不能据此推测停止之后的道路。

独立核对的生产臂危险代理分母为 295，confirmed-clear 分母为 150。Mobilio 的 UNKNOWN 为 275/481、CLEAR 为 49/481、OBSERVE 为 157/481，`any_miss`（危险代理帧上被计为放行）为 17/295。UNKNOWN 不是明确预警；代理标签不是临床安全真值，也不支持左右方向正确率。

历史冻结 CSV 与本次关闭 trace 的帧键和上述分母一致；独立核查的主要状态和有效指令一致，但 4 帧 `guidance` 由空值变为观察提示，详见 [history_comparison.json](history_comparison.json)。这 4 帧当前均为 OBSERVE、`authorized_motion` 为空，不能计入行走授权；尚需结合历史提交完整解释版本差异。

## 复现及剩余工作

使用清单所列本地数据和权重、固定代码快照、Python 环境以及 `HF_HUB_OFFLINE=1`，在新输出目录分别调用 `pc/tools/mobilio_sanpo_ab.py --conditions production --max-width 640 --seed 0 --device mps --e3-frozen --trace-code-commit 6813160c555e297b03a599eb689b8083fe4ce36a --trace-input-manifest docs/phase_reports/e3_input_audit_20261107/input_inventory.json`，显式提供数据目录、checkpoint、不同 `--out` 和 `--trace-run-id`；开启版本额外指定输出目录内的 `--ray-trace-out`。然后运行：

```sh
python docs/phase_reports/e3_surface_6813160/independent_check.py OFF/per_frame.csv ON/per_frame.csv ON/rays.jsonl docs/phase_reports/e3_input_audit_20261107/input_inventory.json
```

复算程序输出检查结果，调用者必须检查结果，而非仅凭进程退出码判定通过。原始本地射线文件约 83 MB，报告未上传原图、模型权重或本机绝对路径。两次输出的实际 SHA256 记录在独立结果中。

完整 E3 交付尚缺：完整逐项独立验收（像素化画面及几何、网格和射线的实际案例见下方增补）。因此暂不宣告 G2 GO，也不把本次 trace 描述为算法改进。Last10m 产品优先级不受本离线诊断改变。

## 增补：可供异地审查的派生证据

[分组表](per_group.csv)按会话、危险代理标签、两个地图状态、融合状态及最终原因分组；[无候选射线表](no_candidate_buckets.csv)在 125 帧内按同一标签与最佳被拒射线终止原因分组。这些是互斥的逐帧分组，不是根因归因。独立核查确认分组表合计 481 帧、无候选表合计 125 帧，且 `danger_any` 与 `confirmed_clear` 没有重叠。

[三个真实射线示例](ray_examples.svg)展示被拒射线和无观测的距离记录；它们不是原图、道路地图或运动授权。蓝线是原规划器的 `observed_distance_m`，不等同于物理连续可走长度；红线只覆盖搜索停止之前已扫描的连续可走段。

[关闭 trace 的逐帧表](off_per_frame.csv.gz)、[开启 trace 的逐帧表](on_per_frame.csv.gz)和[射线记录](rays.jsonl.gz)均为 gzip 压缩的派生数据，不含图像或模型权重；解压后 SHA256 与 [independent_results.json](independent_results.json) 一致。先在独立目录用 `gzip -dc` 解压，再运行文中的复算程序。`group_analysis.py` 和 `render_ray_examples.py` 可从解压后的 ON 表与射线记录重新生成分组和图例。

数据基于 Google Research 的 [SANPO 数据集](https://github.com/google-research-datasets/sanpo_dataset)，Waghmare 等，WACV 2025；官方说明其数据以 Creative Commons 4.0 许可发布并允许共享、改编。以上派生记录来自本次运行，保留公开数据集会话及帧标识，不上传原始视频。运行环境与脱敏日志见 [environment_and_run.json](environment_and_run.json)、[OFF 日志](replay_off.log)、[ON 日志](replay_on.log)及[实际命令](commands.md)。

独立历史代码审查发现，`0d0da47` 引入 OBSERVE 下的谨慎提示，后续 `325a87e` 将提示与运动授权分开，行为与上述 4 帧差异相符。但历史 CSV 未记生成 commit，故这只是有证据支持的解释，不能证明其原始版本。候选历史版本 `2ee1f58791ea19d0660daf2dfb2ff03ee009ab40` 已在 481 帧上重跑。独立核对的 11 项字段与历史 CSV 逐帧一致；对当前 OFF 仅 4 帧 guidance 不同，其他 10 项一致，详见 [历史复算](historical_rerun_review.json)、[旧版逐帧数据](historical_rerun_per_frame.csv.gz)和[运行日志](replay_historical.log)。旧版未锁定模型快照，日志无法证明模型字节完全一致；历史 CSV 原始 commit 仍未知，不能据此断定原始生成版本或完全同输入。

## 按会话指标与独立复核

[按会话指标](per_session.csv)和[汇总指标及定义](metrics_selected.json)从开启 trace 的逐帧表重新计算，[脚本](metrics_by_session.py)保留计算规则。独立验证者逐会话复算所有列，结果一致。当前生产输入下，基线在 confirmed-clear 代理帧上输出授权方向为 76/150，Mobilio 为 29/150；这只衡量有无授权，不能证明左右方向正确。Mobilio 的 4 帧仅观察提示均无行走授权。指令翻转分别为 201/478 与 136/478，分母是每个会话相邻的已处理标注帧，并非连续视频帧。

危险识别的明确预警率在此表标为 NOT_EVALUABLE：旧汇总的 `warning` 定义包含 UNKNOWN，不能解释为明确危险识别。没有可靠方向真值时也不计算方向正确率。[定向测试日志](tests.log)记录 32 项通过，不能替代完整回放或真实安全验证。

## 真实地图与射线案例

[四个脱敏可视化案例和复现说明](visualizations/README.md)展示真实回放的角色、深度、网格、选中／被拒绝射线及画面中的地面采样点。独立审查确认四例各 181 条射线与冻结记录一致。画面经整体像素化以降低隐私风险，不能保证匿名；采样点不等于最终地图单元。全量采集的受检输出与冻结回放一致，单会话选中案例的输入模型未逐字节锁定。
