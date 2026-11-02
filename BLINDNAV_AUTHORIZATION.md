# BlindNav Authorization

## Level A — Superagent may act autonomously

No question needed: build failure, test failure, lint/regression, benchmark
script bug, data-processing bug, small code bug, adding regression/tests,
test-tool refactor, benchmark automation, downloading reasonable small public
test data, small performance fix, documentation sync. Fix → test → PR.

## Level B — research and experiment only

May branch, benchmark and report, but must request a technical decision before
adopting: segmentation model swap, detector swap, VLM A/B, major OCR-policy
change, corridor algorithm change, tracker change, new third-party dependency,
new cloud service.

## Level C — operator only

Superagent must not decide: product priorities, STOP Safety Policy principles,
MotionAuthority, ArrivalAuthority, real BLV testing, opening experimental
features to real users, overall architecture, official cloud/model vendor,
large app rewrite, deleting historical data, privacy policy, auto-merge, or
making the repository/data public.

## Delivery Requirements

Every PR must state: Problem, Evidence Before, Change, Evidence After,
Regression, Evidence Level, Remaining Risks. Never write “fixed” when only
simulation confirms it.

## Device Work

Phone, camera, microphone, outdoor walking and real entrance capture are
operator actions. Finish all desktop work first, then ask for at most three
device actions.

## Safety Claim

BlindNav is not a certified mobility aid. Never claim safe for blind users,
production safe, replacement for the cane, or a validated safety system.
