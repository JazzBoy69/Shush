# Archived v1 production-pipeline samples

These WAVs preserve the earlier v1 result. The active pass is now phrase-stress v2, with representative samples in `voice-samples/stress-v2/`. Default preview generation/verification targets v2 and does not overwrite these files.

Every individual message here was generated through `neural_pipeline.render_prompt` with `shush-lexical-stress-v1`. All ten WAVs regenerated bit-for-bit before v2 replaced the pass. No pitch processing or waveform cutting was used.

`appliance-preview.wav` contains:

1. “Reading.”
2. “No reading.” (only “No” loses its stress marker)
3. “Your alarm is set for six fifteen.” (fifTEEN)
4. “The current time is nine fifteen.” (fifTEEN)

`numeral-preview.wav` contains current time nine thirteen, nine seventeen, nine nineteen, and eleven forty five. The teen words receive final-syllable primary stress. Eleven and forty five retain their original lexical stress.

The complete 771-prompt raw corpus is recorded in `voice-samples/stress-audit-raw.json`; `stress-audit.json` records the corrected phonemes and correction metadata. Exactly 97 prompts change: 24 uses of fifteen, 12 uses each of thirteen, fourteen, sixteen, seventeen, eighteen, and nineteen, plus “No reading.” All other audited lexical stress is retained, including the hour/minute contextual stress difference in “one.” Unexpected text or phoneme forms raise `ValueError`, and speech sounds other than stress markers cannot change.

The manifests and v1 audit retain the exact phonemes, hashes and correction evidence. Current `scripts/test_stress.py` validates the active v2 corpus rather than reproducing v1 samples.

The manifest records raw/corrected phonemes, raw/model tokens, correction rules, pass version, model and environment hashes, inference order, and WAV hashes. Listening quality remains subject to review. The runtime voice bank and incomplete candidate bank have not been replaced.
