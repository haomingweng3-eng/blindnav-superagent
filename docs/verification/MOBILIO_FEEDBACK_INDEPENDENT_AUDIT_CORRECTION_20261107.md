# Mobilio feedback 独立复核更正（2026-11-07）

## 结论

**INCONCLUSIVE（上一份刷新审查存在四组授权数字错误；已定位并更正，未修改 layer_matrix 或 README）。**

错误在 `MOBILIO_FEEDBACK_INDEPENDENT_AUDIT_REFRESH_20261107.md` 的四组摘要：它把 962 行（annotated + production 两半）条件汇总误写为授权 29、21、25、269。按随包 `four_way_per_frame.csv` 逐行筛选 `arm=mobilio`，每条件恰为 481 行，且 `effective_command ∈ {LEFT,RIGHT,STRAIGHT}` 的正确授权数为：

| 条件 | 帧数 | 授权数 | UNKNOWN | any_miss |
|---|---:|---:|---:|---:|
| A_gt_gt | 481 | 29 | 37 | 10 |
| B_v5_gt | 481 | 15 | 61 | 8 |
| C_gt_dav2 | 481 | 57 | 276 | 18 |
| D_v5_dav2 | 481 | 49 | 275 | 17 |

这四组与 `layer_matrix.json` 的 `four_way:*` 条目逐项一致（A 29、B 15、C 57、D 49）。因此，原刷新报告的四组数字结论不能视为 PASS；本更正将总体判定降为 INCONCLUSIVE。

## 重新核对的其他断言

- Harvard：`harvard_fixed_per_frame.csv` 按 `arm=mobilio`、每条件 481 行重算，A 为 surface CLEAR 23、最终授权 8、UNKNOWN 168；D 为 surface CLEAR 80、最终授权 71、UNKNOWN 347；与矩阵一致。
- D 原因：`production:production` 481 行中 `surface:no_candidate_ray=125`、`occupancy:no_observed_clearance=97`、`occupancy:no_fully_observed_clearance=44`；三类 reason 非互斥，不能相加成分母。
- E1/E2/E3：README 明确 UNKNOWN 不得重编码为 CLEAR；E2/E3 的 `observation_hint` 仅为非授权候选，不得变成 `authorized_motion`、方向 TTS 或 CLEAR；CLEAR certificate 保持独立。语义断言与字段分离一致，但仍未执行新变体实验。

## 反证与门禁

1. 962→481 的分母混淆已实际推翻上一报告四组数字；必须按 `arm=mobilio` 取单半部。
2. `effective_command` 与 `guidance` 字段计数不同，若错用 guidance 会产生另一组数字；本次严格使用报告要求的最终授权字段。
3. D 的 125/97/44 是原因计数而非互斥类别，若相加会错误推导 occupancy 分母；README 已明确这一点。
4. 未发现 UNKNOWN 或 observation_hint 到授权运动的规则映射，但没有 E2/E3 新实验结果，不能把语义约束等同于行为验证。

## 可复核命令

```sh
unzip -p outputs/blindnav-superagent-20261107.zip docs/assets/review_20261107/mobilio_feedback_20261107/four_way_per_frame.csv > /tmp/four_way_per_frame.csv
python - <<'PY'
import csv,collections
rows=list(csv.DictReader(open('/tmp/four_way_per_frame.csv')))
for c in sorted({r['condition'] for r in rows}):
  x=[r for r in rows if r['condition']==c and r['arm']=='mobilio']
  print(c,len(x),sum(r['effective_command'] in {'LEFT','RIGHT','STRAIGHT'} for r in x),sum(r['safety_state']=='UNKNOWN' for r in x))
PY
```
