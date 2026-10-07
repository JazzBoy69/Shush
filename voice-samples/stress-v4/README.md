# Archived v4 mode labels

These WAVs preserve the previous mode delivery as a listening reference. Active mode previews now target `stress-v5/modes/`; use explicit `--output voice-samples/stress-v4/modes` only to inspect the recorded archive, not to regenerate it with the current pass.

`modes/mode-reading.wav` says “With reading.” `modes/mode-noise.wav` says “Without reading.” Both use Michael and the same shared production renderer/stress pass as the full bank generator. The words are exact; there is no added “pink noise” phrase.

The audited raw forms are `wɪð ɹˈiːdɪŋ.` and `wɪðˌaʊt ɹˈiːdɪŋ.`: With is already unstressed, Without has secondary prominence, and reading is primary in both. V4 strictly validates these forms without fabricating additional corrections. All nonmode stress rules are unchanged from verified v3. The complete updated corpus audit is `voice-samples/stress-audit-v4.json`; 769 prompts change, and the two mode prompts retain their raw stress.

Both mode WAVs and their manifest regenerated bit-for-bit in fresh, independent sorted mode sessions:

```sh
# Recorded v4 regeneration used the then-current v4 source.
# The current script generates v5 instead.
```

`modes/manifest.json` records the pinned environment, pass version, raw/corrected phonemes, tokens, inference order and hashes. The nested mode directory avoids colliding with the larger representative preview produced by `preview_stress.py`. Earlier samples remain unchanged as references.

The larger representative set is now also generated here: `appliance-preview.wav` contains With reading, Without reading, and alarm six o'clock/fifteen/thirty/forty five. `numeral-preview.wav` contains the nine representative current-time phrases, and the error indication is separate. Its manifest explicitly records one freshly seeded sample session and its actual phrase order; it does not claim the full bank generator's independent-family session protocol.
