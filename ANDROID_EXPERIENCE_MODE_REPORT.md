# Android Experience Mode — 2026-11-03

Not implemented in this round.

Reason: Experience Mode was gated on the first three algorithm stages having no
major blocker. Real-data baseline found two major blockers:

- General traversability: 0/45 no-tactile SANPO frames get a valid corridor from
  the current detector.
- Real-video obstacle recall: 36–38% on UrbanEgo, far below the 90% target.

Shipping a polished test shell would hide these failures. The debug/shadow
screens remain as-is. No UI code changed. This is `NOT_DONE` by design, not a
silent omission.

Next step after the architecture decision: build the four-entry Experience Mode
(环境感知 / 走直线 / 避障 / 最后10米) with only “what it sees / what it decides /
what to do next” on the main screen, and Developer Details hidden behind a
separate entry.
