#!/usr/bin/env python3
"""Generate the exact v7 mode labels/prosody in a fresh production mode session."""

import argparse
import json
from pathlib import Path
import tempfile

from generate_neural_voice import environment
from neural_pipeline import render_prompt
from preview_voice import digest, load_model
from stress import PASS_VERSION

PROMPTS = {"mode-noise.wav": "Without reading.", "mode-reading.wav": "With reading."}


def generate(models, output):
    output.mkdir(parents=True, exist_ok=True)
    model = load_model(models, 2)
    entries = []
    for filename, text in sorted(PROMPTS.items()):
        entry, _ = render_prompt(model, "am_michael", text, output / filename)
        entries.append(entry)
    manifest = {
        **environment(2),
        "voice": "am_michael",
        "stress_pass_version": PASS_VERSION,
        "family": "mode",
        "inference_order": sorted(PROMPTS),
        "purpose": "Exact mode wording samples from an independent sorted mode session.",
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
        default=Path(__file__).resolve().parents[1] / "voice-samples/stress-v7/modes",
    )
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    if args.verify:
        original = json.loads((args.output / "manifest.json").read_text())
        with tempfile.TemporaryDirectory(prefix="shush-mode-verify-") as temporary:
            regenerated = generate(args.models, Path(temporary))
        if regenerated != original:
            raise RuntimeError("Mode sample regeneration differs")
        for entry in original["assets"]:
            if digest(args.output / entry["file"]) != entry["sha256"]:
                raise RuntimeError(f"Packaged mode differs: {entry['file']}")
        print("Verified both exact v7 mode WAVs bit-for-bit")
    else:
        generate(args.models, args.output)
        print(f"Generated both exact v7 mode WAVs in {args.output}")


if __name__ == "__main__":
    main()
