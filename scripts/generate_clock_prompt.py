#!/usr/bin/env python3
"""Generate the supplemental boot diagnostic using the selected Michael voice.

Use the pinned Python/dependency environment documented in preview_voice.py.
The original 771-prompt bank and its reproduction manifest remain unchanged.
"""
import argparse
import json
from pathlib import Path

from generate_neural_voice import environment
from neural_pipeline import render_prompt
from preview_voice import load_model


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    model = load_model(args.models, threads=2)
    asset, _ = render_prompt(
        model, "am_michael",
        "Unable to set the clock. Check the network connection.",
        args.output / "clock-network-error.wav",
    )
    metadata = environment(2)
    metadata["inference"]["input_order"] = "single supplemental clock failure prompt"
    metadata.update(voice="am_michael", asset=asset)
    (args.output / "clock-network-error.json").write_text(json.dumps(metadata, indent=2) + "\n")


if __name__ == "__main__":
    main()
