# Revised Michael prompts

Historical wording comparison, preserved before the shared stress pass. Active previews are now in `voice-samples/stress/`.

`michael-preview.wav` contains five separately synthesized appliance messages:

1. “Your alarm is set for six thirty.”
2. “Reading.”
3. “No reading.”
4. “The current time is nine fifteen.”
5. “The current time is six thirty.”

`michael-time-comparison.wav` compares these time phrasings, with three quarters of a second between each:

1. “Wake time, six thirty.”
2. “Your alarm is set for six thirty.”
3. “It is six thirty.”
4. “Your alarm is set for six fifteen.”
5. “Your alarm is set for six forty-five.”
6. “The time is nine fifteen.”
7. “It is nine fifteen.”
8. “The current time is nine fifteen.”
9. “The current time is six thirty.”

Individual WAV files are also included. There is no mention of pink noise in the revised mode messages. These are listening samples; the original runtime voice bank remains unchanged.

```sh
/tmp/shush-neural-voice-venv/bin/python scripts/preview_voice.py --historical --revised
/tmp/shush-neural-voice-venv/bin/python scripts/preview_voice.py --historical --revised --verify
```

Generation uses the pinned full Kokoro v1.0 model, `am_michael`, two CPU threads, one inter-op thread, deterministic computation and seed zero. The local manifest records the complete environment and output hashes. See the parent directory's README for dependency/model preparation.
