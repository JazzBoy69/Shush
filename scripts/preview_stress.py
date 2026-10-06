#!/usr/bin/env python3
"""Generate small corrected previews through the active production pipeline."""

import argparse
import json
from pathlib import Path
import tempfile

from generate_neural_voice import environment
from neural_pipeline import render_prompt
from preview_voice import digest, load_model, write_wav
from stress import PASS_VERSION

PROMPTS = {
    "mode-reading.wav": "With reading.",
    "mode-noise.wav": "Without reading.",
    "wake-6-00.wav": "Your alarm is set for six o'clock.",
    "wake-6-15.wav": "Your alarm is set for six fifteen.",
    "wake-6-30.wav": "Your alarm is set for six thirty.",
    "wake-6-45.wav": "Your alarm is set for six forty five.",
    "current-9-01.wav": "The current time is nine oh one.",
    "current-9-05.wav": "The current time is nine oh five.",
    "current-9-13.wav": "The current time is nine thirteen.",
    "current-9-17.wav": "The current time is nine seventeen.",
    "current-9-19.wav": "The current time is nine nineteen.",
    "current-9-21.wav": "The current time is nine twenty one.",
    "current-9-35.wav": "The current time is nine thirty five.",
    "current-11-45.wav": "The current time is eleven forty five.",
    "current-12-59.wav": "The current time is twelve fifty nine.",
    "error.wav": "Playback cannot start. Check appliance diagnostics.",
}


def generate(models, output):
    import numpy as np

    output.mkdir(parents=True, exist_ok=True)
    model = load_model(models, 2)
    entries = []
    sounds = {}
    for filename, text in PROMPTS.items():
        metadata, pcm = render_prompt(model, "am_michael", text, output / filename)
        entries.append(metadata)
        sounds[filename] = pcm
    for filename, order in {
        "appliance-preview.wav": list(PROMPTS)[:6],
        "numeral-preview.wav": list(PROMPTS)[6:-1],
    }.items():
        parts = []
        for name in order:
            parts.extend([sounds[name], np.zeros(12000, dtype="<i2")])
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
        "stress_pass_version": PASS_VERSION,
        "purpose": "Small production-pipeline listening candidates; runtime bank unchanged.",
        "inference_order": list(PROMPTS),
        "assets": entries,
    }
    # Samples deliberately use one explicit sequence, rather than claiming
    # the full generator's independent current/error/mode/wake sessions.
    manifest["inference"][
        "input_order"
    ] = "explicit sample order recorded in inference_order"
    manifest["sample_session_protocol"] = "single freshly-seeded model session"
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models", type=Path, default=Path("/tmp/shush-kokoro-models"))
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "voice-samples/stress-v7",
    )
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    if args.verify:
        original = json.loads((args.output / "manifest.json").read_text())
        with tempfile.TemporaryDirectory(prefix="shush-stress-verify-") as temporary:
            regenerated = generate(args.models, Path(temporary))
        if regenerated != original:
            raise RuntimeError("Corrected stress previews do not reproduce")
        for entry in original["assets"]:
            if digest(args.output / entry["file"]) != entry["sha256"]:
                raise RuntimeError(f"Packaged preview differs: {entry['file']}")
        print(
            f"Verified all {len(original['assets'])} production-pipeline preview WAVs bit-for-bit"
        )
    else:
        generate(args.models, args.output)
        print(f"Generated corrected production-path samples in {args.output}")


if __name__ == "__main__":
    main()
