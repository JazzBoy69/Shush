#!/usr/bin/env python3
"""Replay the historical fifteen-only comparison, preserving its evidence.

Preparation only; does not change the appliance's runtime voice bank.
Requires --historical; active previews use preview_stress.py and the shared pass.
"""

import argparse
import gc
import json
from pathlib import Path
import tempfile

from generate_neural_voice import environment
from preview_voice import digest, load_model, write_wav

PHRASES = {
    "alarm-6-15": "Your alarm is set for six fifteen.",
    "current-9-15": "The current time is nine fifteen.",
}
ORIGINAL = "fˈɪftiːn"
CORRECTED = "fɪftˈiːn"


def synthesize(model, phonemes):
    import numpy as np

    audio, rate = model.create(
        phonemes,
        voice="am_michael",
        speed=1.0,
        lang="en-us",
        is_phonemes=True,
        trim=True,
    )
    if rate != 24000 or not audio.size or not np.isfinite(audio).all():
        raise RuntimeError("Invalid speech output")
    return np.rint(np.clip(audio, -1.0, 1.0) * 32767).astype("<i2")


def generate(models, output):
    import numpy as np

    output.mkdir(parents=True, exist_ok=True)
    entries = []
    samples = {}
    for condition in ["original", "corrected"]:
        # Reset session/seed for each condition rather than letting different
        # initial random-operator state masquerade as a stress correction.
        model = load_model(models, 2)
        for label, text in PHRASES.items():
            original_phonemes = model.tokenizer.phonemize(text, "en-us")
            if original_phonemes.count(ORIGINAL) != 1:
                raise RuntimeError(
                    f"Unexpected fifteen phonemes: {original_phonemes!r}"
                )
            phonemes = (
                original_phonemes.replace(ORIGINAL, CORRECTED)
                if condition == "corrected"
                else original_phonemes
            )
            tokens = model.tokenizer.tokenize(phonemes)
            original_tokens = model.tokenizer.tokenize(original_phonemes)
            if condition == "corrected" and tokens == original_tokens:
                raise RuntimeError("Stress correction did not change model tokens")
            pcm = synthesize(model, phonemes)
            path = output / f"{condition}-{label}.wav"
            write_wav(path, pcm, 24000)
            samples[f"{condition}-{label}"] = pcm
            entries.append(
                {
                    "file": path.name,
                    "condition": condition,
                    "text": text,
                    "original_phonemes": original_phonemes,
                    "phonemes": phonemes,
                    "model_tokens": tokens,
                    "frames": int(pcm.size),
                    "sha256": digest(path),
                }
            )
        if condition == "corrected":
            for label, text in [
                ("mode-reading", "Reading."),
                ("mode-noise", "No reading."),
            ]:
                phonemes = model.tokenizer.phonemize(text, "en-us")
                pcm = synthesize(model, phonemes)
                path = output / f"{label}.wav"
                write_wav(path, pcm, 24000)
                samples[label] = pcm
                entries.append(
                    {
                        "file": path.name,
                        "condition": "selected mode words",
                        "text": text,
                        "phonemes": phonemes,
                        "model_tokens": model.tokenizer.tokenize(phonemes),
                        "frames": int(pcm.size),
                        "sha256": digest(path),
                    }
                )
        del model
        gc.collect()
    orders = {
        "comparison.wav": [
            "original-alarm-6-15",
            "corrected-alarm-6-15",
            "original-current-9-15",
            "corrected-current-9-15",
        ],
        "corrected-preview.wav": [
            "corrected-alarm-6-15",
            "mode-reading",
            "mode-noise",
            "corrected-current-9-15",
        ],
    }
    for filename, order in orders.items():
        parts = []
        for label in order:
            parts.extend([samples[label], np.zeros(12000, dtype="<i2")])
        pcm = np.concatenate(parts[:-1])
        path = output / filename
        write_wav(path, pcm, 24000)
        entries.append(
            {
                "file": filename,
                "sequence": order,
                "gap_frames": 12000,
                "frames": int(pcm.size),
                "sha256": digest(path),
            }
        )
    manifest = {
        **environment(2),
        "voice": "am_michael",
        "purpose": "Fifteen lexical stress comparison; no pitch processing or bank replacement.",
        "phoneme_override": {"from": ORIGINAL, "to": CORRECTED},
        "session_order": [
            "original alarm/current",
            "corrected alarm/current/Reading/No reading",
        ],
        "assets": entries,
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models", type=Path, default=Path("/tmp/shush-kokoro-models"))
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "voice-samples/fifteen",
    )
    parser.add_argument("--verify", action="store_true")
    parser.add_argument(
        "--historical",
        action="store_true",
        help="explicit replay of archived fifteen-only comparison",
    )
    args = parser.parse_args()
    if not args.historical:
        parser.error(
            "Historical comparison requires --historical; use scripts/preview_stress.py for all-prompt stress processing"
        )
    if args.verify:
        original = json.loads((args.output / "manifest.json").read_text())
        with tempfile.TemporaryDirectory(prefix="shush-fifteen-verify-") as temporary:
            reproduced = generate(args.models, Path(temporary))
        if reproduced != original:
            raise RuntimeError("Stress-preview regeneration differs")
        for entry in original["assets"]:
            if digest(args.output / entry["file"]) != entry["sha256"]:
                raise RuntimeError(f"Packaged preview differs: {entry['file']}")
        print("Verified all eight stress-preview WAVs reproduce bit-for-bit")
    else:
        generated = generate(args.models, args.output)
        print(
            f"Generated {len(generated['assets'])} stress-preview WAVs in {args.output}"
        )


if __name__ == "__main__":
    main()
