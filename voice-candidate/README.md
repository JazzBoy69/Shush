# Neural voice candidate bank

Generation was stopped for voice-wording review. This directory currently contains an incomplete, unvalidated bank using the earlier wording. Do not deploy it. Active corrected listening samples are in `voice-samples/stress/`; the full corrected bank has not been rendered.

This directory is prepared separately from `voice/`. Generation uses the male American English Kokoro `am_michael` voice by default, with existing runtime filenames and spelled-out clock times. Speech inference is a preparation step; the appliance consumes local WAV assets.

The generator now applies the shared stress pass to every canonical message: “Reading,” “No reading,” alarm settings, current time, and the error indication. It records raw/corrected phonemes, tokens, corrections and pass version, and rejects unrecognized forms. The full corpus audit covers all 771 prompts with exactly 97 stress changes.

The bank uses two CPU inference threads and one inter-op thread. A benchmark of the three sample phrases reproduced exact PCM hashes with one, two, and four threads; two threads took 3.49 seconds versus 5.42 seconds with one thread on this host. The chosen settings are fixed in the candidate manifest.

```sh
/tmp/shush-neural-voice-venv/bin/python scripts/generate_neural_voice.py
/tmp/shush-neural-voice-venv/bin/python scripts/generate_neural_voice.py --quick-verify
```

Use the isolated dependency environment and model downloads documented in `voice-samples/README.md`. All model hashes, dependency versions, phonemizer hashes, text, settings, and output hashes are recorded in `manifest.json` when generation completes.

The quick verification checks all 771 packaged hashes, then recreates the inference session, follows the complete original sorted input order, and compares the first three and final three regenerated WAVs. Full `--verify` compares every regenerated WAV. Replaying the full order is necessary because the model contains random operators whose state advances during inference. Neither command changes the packaged candidate files.

Run application media/voice checks against this bank before replacing the runtime bank. Retain the baseline until candidate checks finish.
