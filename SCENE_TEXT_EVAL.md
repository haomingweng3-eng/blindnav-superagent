# Scene / Text Evaluation — 2026-11-03

## Scene

- `SceneAwarenessEngine`, `SceneMemory`, `SpeechPolicy` pass 15 JVM tests.
- Production caller: **NONE** (`NOT_CONNECTED`).
- GuideDog gold is gated behind an authenticated Hugging Face dataset; not
  downloaded. No caption/guidance accuracy is reported.
- BLV frames provide object presence only, not “what should be said”.

## Text

- `TextSaliencePolicy` classifies TARGET / WAYFINDING / GENERAL / NOISE and
  passes regression tests (KFC silent when target is library, EXIT salient,
  general silent, 10 s dedupe).
- Production always-watching OCR: **NOT_CONNECTED**. OCR remains inside
  Last10m only.

## Decision

`NEED_DECISION` before wiring SceneEventBus/SpeechPolicy into CameraActivity.
No production scene/text change was made. The permanent desk regression remains:
Safety must not STOP; absence of a scene description is acceptable until the bus
is connected.
