# Entrances Last-10 m door proposal eval

The stub parses GT YOLO door boxes, estimates range from the box bottom on a flat-ground
plane, keeps boxes within the last `max_range`, ranks them and proposes at most three.
It refuses rather than guessing when its reliability rules fail.

## Inputs

- Dataset: `/Users/mima0000/blindnav-app/pc/data/eval/entrances/extracted/final_dataset`
- Splits: test, train, val
- Frames: 854 (with >=1 GT door: 847; with an in-range GT door: 846; empty labels: 7)
- GT boxes: 922 (in range: 920; unknown range: 0)
- Config: max_range=10 m, min_confidence=0.5, require_distance=True

## Recall

| metric | definition | value | numerator / denominator |
|---|---|---:|---:|
| frame recall (in-range GT) | frames with an in-range GT door that get >=1 proposal | **99.88%** | 845/846 |
| frame recall (all GT) | frames with any GT door that get >=1 proposal | 99.76% | 845/847 |
| box recall (in-range) | proposed in-range boxes / in-range GT boxes | 99.57% | 916/920 |

GT range is the same flat-ground estimate the stub uses (Entrances has no distance
labels), so `in-range` is a proxy, not an independent annotation.

## Refusals

- Refused frames: 9/854 (1.05%)
- Refuse rate on frames with GT doors: 0.24%
- Refuse rate on frames with in-range GT doors: 0.12%
- False proposals on empty-label frames: 0

| reason | frames | of which refused |
|---|---:|---:|
| ok | 845 | 0 |
| no_candidate | 7 | 7 |
| low_confidence | 0 | 0 |
| distance_unknown | 0 | 0 |
| out_of_range | 1 | 1 |
| ambiguous | 1 | 1 |

`ok` is a non-refusal reason, so its refused count is zero by definition.

Frames with GT doors that were refused:

| split | frame | reason | GT boxes | in range | range (m) |
|---|---|---|---:|---:|---|
| train | downtown_190_171568564887514 | out_of_range | 2 | 0 | 13.573-15.1585 |
| train | more_downtown_117_649303247313302 | ambiguous | 4 | 4 | 3.7862-4.0208 |

## Candidate-count distribution

| proposed candidates | frames |
|---|---:|
| 0 | 9 |
| 1 | 778 |
| 2 | 63 |
| 3 | 4 |

Eligible (in-range, confidence-passing) candidates before the 3-cap:

| eligible candidates | frames |
|---|---:|
| 0 | 8 |
| 1 | 778 |
| 2 | 63 |
| 3 | 4 |
| 4 | 1 |

## Estimated range (metres)

| split of boxes | p5 | p25 | p50 | p75 | p95 |
|---|---:|---:|---:|---:|---:|
| all boxes | 2.634 | 3.211 | 3.81 | 4.057 | 4.476 |
| in-range boxes | 2.634 | 3.208 | 3.802 | 4.057 | 4.476 |

## Range sensitivity

| max_range (m) | frame recall (in-range GT) | box recall (in-range) | refuse rate | refusals |
|---:|---:|---:|---:|---|
| 5 | 99.88% | 99.56% | 1.76% | {'ok': 0, 'no_candidate': 7, 'low_confidence': 0, 'distance_unknown': 0, 'out_of_range': 7, 'ambiguous': 1} |
| 10 | 99.88% | 99.57% | 1.05% | {'ok': 0, 'no_candidate': 7, 'low_confidence': 0, 'distance_unknown': 0, 'out_of_range': 1, 'ambiguous': 1} |
| 15 | 99.88% | 99.57% | 0.94% | {'ok': 0, 'no_candidate': 7, 'low_confidence': 0, 'distance_unknown': 0, 'out_of_range': 0, 'ambiguous': 1} |
| 20 | 99.88% | 99.57% | 0.94% | {'ok': 0, 'no_candidate': 7, 'low_confidence': 0, 'distance_unknown': 0, 'out_of_range': 0, 'ambiguous': 1} |

## Per split

| split | frames | with GT | in-range GT | frame recall (in-range) | refuse rate |
|---|---:|---:|---:|---:|---:|
| test | 30 | 30 | 30 | 100.00% | 0.00% |
| train | 795 | 788 | 787 | 99.87% | 1.13% |
| val | 29 | 29 | 29 | 100.00% | 0.00% |

## Caveats

- Range is a flat-ground estimate, not a labelled distance.  The `in-range` subsets are
  therefore defined by the same model under test; treat them as a consistency check.
- Entrances labels carry no detector confidence, so the stub sees every box at 1.0.
  `low_confidence` and `distance_unknown` refusals are untestable on this dataset.
- `ambiguous` fires only when >3 in-range boxes have a near-tie at the 3rd/4th cut-off.
