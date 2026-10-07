# Archived phrase-stress v2 samples

All 18 WAVs here regenerated bit-for-bit with v2. They are retained as references; active generation now uses v3 in `voice-samples/stress-v3/`, which adds the neutral first-syllable stress choice for “cannot.” Default preview commands below now target v3 rather than these archived files.

These representative messages use the same frozen `shush-phrase-stress-v2` pass and production renderer as the full candidate bank. Primary stress marks the focused number or final error word; introductions and nonfocused number components receive secondary prominence or no stress. Lexical syllable positions are retained except the explicit teen correction. No pitch processing is used.

`appliance-preview.wav` contains Reading, No reading, and alarm six o'clock, six fifteen, six thirty, six forty five. `numeral-preview.wav` contains current time nine oh one, nine oh five, nine thirteen, nine seventeen, nine nineteen, nine twenty one, nine thirty five, eleven forty five, and twelve fifty nine. Each phrase is also available separately, along with the error indication.

The corpus audit is in `voice-samples/stress-audit-v2.json`. It covers all 771 canonical prompts; 770 change and Reading alone is unchanged. Rules cover introductions, hours, zero-minute clock labels, leading “oh,” simple and compound minutes, teen lexical stress, modes and both error sentences. Each changed word records its role, original/corrected phonemes and rule. All speech sounds other than stress markers must remain identical, and unrecognized audited forms raise `ValueError`.

```sh
/tmp/shush-neural-voice-venv/bin/python scripts/preview_voice.py
/tmp/shush-neural-voice-venv/bin/python scripts/preview_voice.py --verify
python3 scripts/test_stress.py
```

The manifest records the pinned model/environment, exact inference order, pass version, raw/corrected phonemes and tokens, corrections and WAV hashes. Natural prosody remains a listening hypothesis; code checks establish the requested marker policy and reproducibility.
