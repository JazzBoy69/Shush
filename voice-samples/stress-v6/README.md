# V6 continuous mode cadence

The exact phrases are “With reading.” and “Without reading.” Michael renders each as one complete utterance through the shared production pipeline. Corrected phonemes are `wˈɪð ɹˌiːdɪŋ.` (WITH primary, reading secondary) and `wɪðˌaʊt ɹˈiːdɪŋ.` (without secondary, reading primary).

The user rejected v5's forced 200 ms silence as a pause. V6 removes the split-word synthesis and inserted samples. It supplies the complete corrected phrase in one model call; no new silence duration, pitch processing, or time stretching is imposed. Stress prominence supports the requested cadence, but listening determines whether it produces a suitable spoken beat.

The modes are generated and verified in fresh independent sorted mode sessions with the same production renderer used by the complete bank:

```sh
/tmp/shush-neural-voice-venv/bin/python scripts/preview_modes.py
/tmp/shush-neural-voice-venv/bin/python scripts/preview_modes.py --verify
```

`modes/manifest.json` records the actual full-phrase phonemes/tokens, inference order, pinned environment, pass version, and WAV hashes. Earlier `stress-v5/` files retain the rejected forced-pause example. The larger v6 representative set has not been generated. Nonmode stress and synthesis remain unchanged.
