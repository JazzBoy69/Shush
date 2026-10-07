# Male American English voice previews

Active Michael previews target `stress-v7/`; its mode delivery uses primary WITH, secondary reading, and a continuous “Without reading” utterance with secondary without and primary reading. V7 adds a 40 ms hold at the model-aligned boundary within that connected utterance, with 2 ms edge fades. The user rejected the forced 200 ms pause in v5, which remains archived. Use `scripts/preview_modes.py` for the two active modes. Generic `scripts/preview_voice.py` defaults to the shared v7 production phrase-stress pipeline; the larger v7 representative set has not been rendered. All active prompts pass through explicit phonemization, bounded word-role stress rules, and synthesis with corrected phonemes. Earlier samples remain references.

The original Michael/Fenrir WAVs below are retained historical comparisons. Reproduce them explicitly with `scripts/preview_voice.py --historical`, adding `--verify` to check their recorded output. Historical reproduction intentionally bypasses the active pass so the earlier evidence is retained.

Listen to `michael-preview.wav` and `fenrir-preview.wav`. Each contains three actual short prompts synthesized separately, with half a second of silence between them:

- “Wake time, six thirty.”
- “Complete reading playlist, then pink noise.”
- “The time is nine fifteen.”

Individual prompt WAVs are included for closer listening. These samples do not replace the appliance's packaged voice bank and do not imply a voice has been approved.

The selected voice is Kokoro `am_michael`; `am_fenrir` remains in the historical comparison. Both use the full Kokoro v1.0 ONNX model. Generation happens on a preparation host; the Raspberry Pi would receive ordinary WAV files. eSpeak supplies phonemes to the neural model, rather than synthesizing the audible voice.

To reproduce on an x86-64 Linux host with Python 3.13:

```sh
python3 -m venv /tmp/shush-neural-voice-venv
/tmp/shush-neural-voice-venv/bin/pip install -r voice-samples/requirements.txt
mkdir -p /tmp/shush-kokoro-models
curl -fL https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.1/kokoro-v1.0.onnx -o /tmp/shush-kokoro-models/kokoro-v1.0.onnx
curl -fL https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.1/voices-v1.0.bin -o /tmp/shush-kokoro-models/voices-v1.0.bin
/tmp/shush-neural-voice-venv/bin/python scripts/preview_voice.py
/tmp/shush-neural-voice-venv/bin/python scripts/preview_voice.py --verify
```

The generator checks SHA-256 hashes of both model files before inference. The manifest records package versions, phonemizer library/data hashes, model hashes, CPU execution settings, phrase text, and WAV hashes. Active CPU inference uses two threads, one inter-op thread, deterministic computation, and seed zero. Historical initial comparisons used one thread. Output is mono, 16-bit PCM at 24 kHz. A narrow adapter corrects the `speed` input from the wrapper's int32 to the model's declared float32 type.

Exact regeneration is checked in the recorded environment; different CPUs or numerical-library builds may produce different floating-point output. No voice-quality claim is made before listening.
