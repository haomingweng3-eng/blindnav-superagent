# ImVisible traffic-light three-state eval

The stub has **no pixel model**: `mode` is mapped to green/red/uncertain, and the only
decision logic is the consecutive-frame confirmation filter. Raw mapping accuracy is
therefore 100% by construction; the filtered numbers measure the stub's operational
per-frame output on a pseudo-temporal replay of the labels.

## Inputs

- Splits: testing, validation
- Rows: 1603 (testing=739, validation=864)
- Images found: 1603/1603 under `/Users/mima0000/blindnav-app/pc/data/eval/imvisible/extracted/PTL_Dataset_876x657`
- Pseudo-sequences: 9 (length histogram {2: 1, 70: 1, 79: 1, 163: 1, 187: 1, 231: 1, 273: 1, 275: 1, 323: 1})

## Three-state rubric (GT = same collapse)

| ImVisible mode | meaning | state | rows |
|---:|---|---|---:|
| 0 | red | red | 461 |
| 1 | green | green | 424 |
| 2 | green countdown | green | 315 |
| 3 | countdown blank | uncertain | 283 |
| 4 | no light | uncertain | 120 |

Raw mapping fidelity: **1.0000** (measured; exact by construction).

Mode-level collapse (rows = GT mode, columns = predicted state):

| mode | green | red | uncertain |
|---:|---:|---:|---:|
| 0 | 0 | 461 | 0 |
| 1 | 424 | 0 | 0 |
| 2 | 315 | 0 | 0 |
| 3 | 0 | 0 | 283 |
| 4 | 0 | 0 | 120 |

## Confirmation filter (min_confirm=3, min_confidence=0.6)

- Frames: 1603
- Accuracy: **0.3812**
- Stable (confirmed) rate: 12.98%
- Conservative abstention (GT green/red -> uncertain): 61.88%
- Unsafe green (GT red/uncertain -> green): **0**

Confusion matrix (rows = GT, columns = predicted):

| GT \ pred | green | red | uncertain |
|---|---:|---:|---:|
| green | 164 | 0 | 575 |
| red | 0 | 44 | 417 |
| uncertain | 0 | 0 | 403 |

Per-class:

| state | support | predicted | precision | recall | f1 |
|---|---:|---:|---:|---:|---:|
| green | 739 | 164 | 100.00% | 22.19% | 36.32% |
| red | 461 | 44 | 100.00% | 9.54% | 17.43% |
| uncertain | 403 | 1395 | 28.89% | 100.00% | 44.83% |

Per split (same filter settings):

| split | frames | accuracy | stable rate | abstention | unsafe green |
|---|---:|---:|---:|---:|---:|
| testing | 739 | 0.3857 | 13.67% | 61.43% | 0 |
| validation | 864 | 0.3773 | 12.38% | 62.27% | 0 |

## Confirmation-window sweep

| min_confirm | accuracy | stable rate | abstention | unsafe green | green recall | red recall |
|---:|---:|---:|---:|---:|---:|---:|
| 1 | 1.0000 | 74.86% | 0.00% | 0 | 100.00% | 100.00% |
| 2 | 0.5577 | 30.63% | 44.23% | 0 | 46.14% | 32.54% |
| 3 | 0.3812 | 12.98% | 61.88% | 0 | 22.19% | 9.54% |
| 4 | 0.3088 | 5.74% | 69.12% | 0 | 10.01% | 3.90% |
| 5 | 0.2757 | 2.43% | 72.43% | 0 | 4.06% | 1.95% |

## Caveats

- The CSV rows are individual labelled frames, not a certified contiguous video. Filter
  numbers are an order-proxy stress test; they are a lower bound on warm-up behaviour.
- GT is the three-state collapse of the dataset's own `mode` column, so the raw mapping
  cannot disagree with GT. There is no learned traffic-light detector in this stub.
- `green` is never a crossing permit: `auto_go=False` and green requires user confirmation.
