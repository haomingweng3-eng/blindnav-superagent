# BlindNav Evaluation Rules

## Evidence Levels

Use only: `DESKTOP_SIMULATION`, `DESKTOP_REAL_DATA`, `DEVICE_VERIFIED`,
`USER_TESTED`, `PROTOTYPE`, `NOT_CONNECTED`.

Forbidden: computer pass → phone pass; class exists → implemented; unit test →
product usable.

## Before / After

Every algorithm or policy change must report:

Baseline → Failure bucket → Cause → Change → Same-set retest → Before/After →
Regression.

Never change to an easier dataset and report an improvement. Never call a
simulation result real-world.

## No One-Sided Metrics

Reducing false STOP must also report blocking recall. A drop from 30% to 5%
false STOP is rejected if blocking recall falls from 92% to 60%.

## Frozen Sets

Formal comparisons use a frozen eval set. New data creates a new version
(`core_v1` → `core_v2`); never silently edit an old set under the same name.

## Failure Buckets First

Classify before changing models: perception, geometry, temporal, pose, data,
policy, speech, integration. Change the relevant layer only.

## Two-Round Rule

If the same failure bucket does not improve after two rounds, stop tuning
thresholds. Research at least two alternatives and report accuracy potential,
latency, mobile feasibility, data requirement, engineering cost, risk. Then
return the decision to the operator.

## Honesty

No fake benchmark, synthetic result presented as real, mock tool presented as
production, accuracy without GT, model prediction used as GT, or deletion of
unfavourable results.
