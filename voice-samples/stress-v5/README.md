# Archived v5 forced pause

The user rejected this 200 ms silence as a pause rather than the requested spoken beat. These WAVs preserve that rejected delivery as a reference. Active v6 uses a single continuous synthesis call for each mode, with no inserted silence. The current preview commands generate v6 and cannot regenerate this archive without its historical v5 source.

`modes/mode-reading.wav` says “With reading.” with WITH primary and reading secondary: `wˈɪð ɹˌiːdɪŋ.`. `modes/mode-noise.wav` says “Without reading.” with secondary without, primary reading, and a 200 ms beat between the words. Both use Michael through the shared production renderer.

The without prompt is synthesized as two actual model inputs, `wɪðˌaʊt,` and `ɹˈiːdɪŋ.`. After the model's normal trim, only exact digital-zero samples at the segment edges are removed. The renderer inserts exactly 4,800 zero PCM samples at 24 kHz between the segments; it applies no pitch processing or time stretching. The manifest records both submitted token lists, the logical full phrase separately, segment lengths, trim counts, inserted pause frames, and hashes. Ordinary prompts retain their previous rendering and metadata.

Both WAVs and their complete manifest regenerated bit-for-bit in fresh independent sorted mode sessions. The without WAV has 23,040 samples per word plus the 4,800-sample beat (2.12 seconds total); the with WAV lasts 1.216 seconds. Samples adjacent to the inserted zero run are nonzero. These checks establish the implemented timing and reproducibility; listening determines whether the delivery is suitable.

```sh
# Recorded v5 verification used the then-current v5 source.
```

The corpus audit `voice-samples/stress-audit-v5.json` covers all 771 canonical prompts. V5 changes stress markers in 770 prompts; without retains its existing stress pattern and gains the rendered beat. The 769 nonmode prompts preserve their prior stress and synthesis recipe. Earlier v4 mode samples remain in `stress-v4/` as references. No larger v5 preview set has been generated.
