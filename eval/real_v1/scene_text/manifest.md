# Scene/Text real-data manifest

- BLV-Road-Nav-Accessibility frames + per-frame object labels: usable for object
  presence, not for scene salience or speech usefulness.
- GuideDog gold: gated Hugging Face dataset requiring an authenticated HF token;
  not downloaded. No fake metrics are reported.
- `app/src/test/resources/last10m/scene_frames.tsv`: existing VLM replay frames
  for SceneMemory/SpeechPolicy tests.

Status: Scene/Text production NOT_CONNECTED; policy-level tests only.
