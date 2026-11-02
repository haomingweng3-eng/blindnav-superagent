# Last10m Real-Data Report — 2026-11-03

## Status

- SEARCH / LOCK: `DESKTOP_VERIFIED` by the existing 166-test suite; no new
  real Signboard data was downloaded (Google Drive access not selected).
- ENTRANCE: `NOT_CONNECTED`. `EntranceProposal` is an interface; production
  candidates still come from manual panel taps and VLM facts.
- ARRIVAL: `DESKTOP_VERIFIED`; six guards and cross-session/stale evidence
  rejection remain unchanged.
- Target→Door relation: `NO_GT`. No `relation_seed_v1` was fabricated.

## What was tested

- Existing `Last10m*Test` suites, including alias safety, target lock, temporal
  evidence, arrival fuzz, neighbor-door conflict, cross-session protection.
- No DeepDoors2/Signboard metrics are reported because the data is not local.

## Door proposal baseline

`UNAVAILABLE`. DeepDoors2 data is hosted on Google Drive and was not downloaded.
Reporting Recall@IoU without images/annotations would be fake.

## Safety invariants preserved

Single-frame FUZZY cannot lock; weak derived alias cannot lock independently;
stale target expires; neighbor door cannot produce ARRIVAL; cross-session
evidence cannot produce ARRIVAL; door ≠ arrival; target sign ≠ arrival.

## Decision

`NEED_DATA` for automatic entrance; no state-machine rewrite.
