# Archived phrase-stress v3 production samples

All 18 WAVs here remain the verified v3 reference. V4 changes the exact mode labels to With reading and Without reading; its new mode samples are in `voice-samples/stress-v4/modes/`. Default generic preview commands now target v4.

These messages use the same frozen `shush-phrase-stress-v3` pass and production renderer as the full candidate bank. Primary stress marks the focused number or final error word; introductions and nonfocused number components receive secondary prominence or no stress. No pitch processing is used.

`appliance-preview.wav` contains Reading, No reading, and alarm six o'clock, six fifteen, six thirty, six forty five. `numeral-preview.wav` contains current time nine oh one, nine oh five, nine thirteen, nine seventeen, nine nineteen, nine twenty one, nine thirty five, eleven forty five, and twelve fifty nine. Individual phrase WAVs and `error.wav` are also included.

V3 selects the neutral first-syllable lexical stress for “cannot” documented by [Oxford Learner's Dictionaries](https://www.oxfordlearnersdictionaries.com/definition/english/cannot), with secondary phrase prominence because the error sentence focuses “start.” It changes audited `kænˈɑːt` to `kˌænɑːt`; this is a selected neutral delivery, not a claim that every alternative emphasis is invalid. Diagnostics retains its internal secondary and final primary stress.

`voice-samples/stress-audit-v3.json` covers all 771 prompts; 770 change and Reading alone stays unchanged. Rules cover introductions, hours, zero-minute clock labels, leading “oh,” simple/compound minutes, teen lexical stress, modes and both error sentences. Changed words record role, original/corrected phonemes and rule. All speech sounds must remain identical apart from stress markers, and unexpected audited forms raise `ValueError`.

```sh
/tmp/shush-neural-voice-venv/bin/python scripts/preview_voice.py
/tmp/shush-neural-voice-venv/bin/python scripts/preview_voice.py --verify
python3 scripts/test_stress.py
```

All 18 WAVs and their manifest regenerated bit-for-bit in the pinned environment. The manifest records model/environment, inference order, pass version, raw/corrected phonemes and tokens, corrections and WAV hashes. Natural prosody remains a listening hypothesis; code checks establish the requested marker policy and reproducibility. V1/v2 samples remain unchanged as references.
