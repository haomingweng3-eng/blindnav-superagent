# E3 input inventory — 2026-11-07

Decision: **ADJUST / G2 HOLD**. Evidence level: local file inventory and
independent sample audit; no full E3 replay is claimed.

The historical `production_per_frame.csv`, filtered to production/mobilio,
contains 481 unique (session, frame) keys, grouped 121/184/176. The inventory
lists 1,449 required image, segmentation, depth, session description and
corrected camera-pose files. All were present at inventory time; each was
hashed by the developer. The v5 checkpoint hash matches the prior freeze
manifest. Local Hugging Face cache `main` revisions and file hashes are also
listed, but the inventory does not prove the evaluator loaded those files.

An independent read-only reviewer reconstructed the required file set from the
historical CSV and found 1,449 unique paths and zero missing files. The reviewer
verified hashes and sizes for three sampled files and the v5 checkpoint. This is
not an independent hash verification of all 1,449 files or a pipeline rerun.

Inventory SHA-256:
`2b4997b6c3e561e62a046fb812edbd31de2ad231596da0ab9219755f2a20e37c`.
The inventory uses session-relative paths and does not contain local user paths,
raw images or weights. Model cache versions are inventory observations only.

Remaining before G2: bind and verify every actual input and model loaded at
runtime, record the actual code commit and dirty diff/configuration, complete
G1 trace edge cases and demonstrate trace-on/off equivalence on the frozen run.
Independent validation of those artifacts remains required. A 12-frame smoke
comparison is not a substitute for the 481-frame comparison.
