# Fifteen stress correction

Historical fifteen-only comparison. All active prompts now use the shared pass, including the unstressed “No” correction; see `voice-samples/stress/`.

The local phonemizer emits `fˈɪftiːn` (“FIFteen”) for the selected alarm and current-time wording. These samples explicitly replace that sequence with `fɪftˈiːn` (“fifTEEN”). All other phonemes, Michael's voice, and synthesis settings remain the same. The changed stress marker produces changed model tokens; the manifest records both.

`comparison.wav` contains, in order:

1. Original “Your alarm is set for six fifteen.”
2. Corrected stress in that alarm phrase.
3. Original “The current time is nine fifteen.”
4. Corrected stress in that current-time phrase.

`corrected-preview.wav` contains the corrected alarm phrase, “Reading,” “No reading,” and the corrected current-time phrase. Individual WAV files are included. No pitch modification, extra spoken context, or waveform cutting is used. These are listening candidates; no voice-bank replacement or subjective acceptance is implied.

```sh
/tmp/shush-neural-voice-venv/bin/python scripts/preview_fifteen.py --historical
/tmp/shush-neural-voice-venv/bin/python scripts/preview_fifteen.py --historical --verify
```

Generation uses the pinned Kokoro model and dependency environment described in `voice-samples/README.md`. Original and corrected conditions each start a new fixed-seed session, using two CPU threads and one inter-op thread. The manifest records exact phonemes, model tokens, inference order, preparation environment, and WAV hashes.
