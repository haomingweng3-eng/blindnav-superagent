# Last10m evaluation protocol

This is the active product path after the Mobilio experiment was marked
`BLOCKED_BY_EVIDENCE`. The existing Android chain remains the system under
test: OCR -> target lock -> entrance candidates -> target/door relation ->
ground evidence -> arrival authority. The demo runner and replay reasoner use
the same state machine and mark replay evidence explicitly.

## Required case schema

Each real case must identify the target store and the correct entrance before
the run is scored:

```json
{
  "case_id": "store_001",
  "target": {"name": "Example Shop", "aliases": ["Example"]},
  "frames": [
    {
      "frame_id": 1,
      "image": "frames/001.jpg",
      "ocr": [{"text": "Example Shop", "box": [100, 50, 400, 150]}],
      "doors": [{"id": "door_left", "box": [80, 200, 300, 700]}],
      "correct_entrance_id": "door_left",
      "target_visible": true,
      "relation_evidence": "sign_over_door"
    }
  ]
}
```

`correct_entrance_id` is required for entrance accuracy. A frame with only a
door box, without a target-to-door relation label, is `NOT_EVALUABLE` for
entrance accuracy and must not be counted as a success.

## Comparisons

The same frame sequence is replayed through the existing state machine with:

1. fixed single-frame observation;
2. OCR-triggered observation;
3. active multi-view observation using `NeedViewPlanner` and `ScanCoach`.

Every output records the state, target lock, candidate entrance IDs, relation
verdict, need-view prompt, speech decision, and whether motion authorization was
issued. Entrance identification and motion authorization are scored
separately; an unverified entrance never authorizes movement.

## Metrics

* target-lock accuracy and target-missing refusal rate;
* correct entrance accuracy over cases with `correct_entrance_id`;
* wrong-entrance rate;
* target-visible-but-unconfirmed rate;
* target-absent refusal rate;
* median views required to reach a confirmed relation;
* state transitions and replay/real evidence provenance;
* motion-authorized frames after entrance and ground evidence are confirmed.

The current Entrances archive can measure door-box proposal and range behavior
with `pc/tools/last10m_demo.py`, but it has no target-store/entrance relation
labels. Its door proposal metrics must therefore remain separate from this
protocol and cannot be reported as correct-entrance accuracy.
