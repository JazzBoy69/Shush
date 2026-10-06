#!/usr/bin/env python3
"""Generate a complete local neural WAV candidate bank, preserving voice/.

Preparation host only. Dependencies are pinned in voice-samples/requirements.txt.
"""

import argparse
import gc
import importlib.metadata
import json
from pathlib import Path
import platform
import shutil
import tempfile

from preview_voice import MODEL_FILES, RELEASE, digest, load_model
from neural_pipeline import render_prompt
from stress import PASS_VERSION, postprocess_stress

FAMILIES = ("current", "error", "mode", "wake")


def family(filename):
    return filename.split("-", 1)[0].split(".", 1)[0]


ONES = [
    "zero",
    "one",
    "two",
    "three",
    "four",
    "five",
    "six",
    "seven",
    "eight",
    "nine",
    "ten",
    "eleven",
    "twelve",
    "thirteen",
    "fourteen",
    "fifteen",
    "sixteen",
    "seventeen",
    "eighteen",
    "nineteen",
]
TENS = ["", "", "twenty", "thirty", "forty", "fifty"]


def number(value):
    if value < 20:
        return ONES[value]
    tens, ones = divmod(value, 10)
    return TENS[tens] + (" " + ONES[ones] if ones else "")


def time_text(hour, minute):
    if minute == 0:
        return number(hour) + " o'clock"
    if minute < 10:
        return number(hour) + " oh " + number(minute)
    return number(hour) + " " + number(minute)


def prompts():
    result = {
        "mode-reading.wav": "With reading.",
        "mode-noise.wav": "Without reading.",
        "error.wav": "Playback cannot start. Check appliance diagnostics.",
    }
    for hour in range(1, 13):
        for minute in range(60):
            spoken = time_text(hour, minute)
            result[f"current-{hour}-{minute:02}.wav"] = f"The current time is {spoken}."
            if minute % 15 == 0:
                result[f"wake-{hour}-{minute:02}.wav"] = (
                    f"Your alarm is set for {spoken}."
                )
    return dict(sorted(result.items()))


def environment(threads, family_sessions=False):
    import espeakng_loader

    data = Path(espeakng_loader.get_data_path())
    metadata = {
        "generator": "Kokoro v1.0 ONNX / kokoro-onnx 0.4.9",
        "model_release": RELEASE,
        "models": MODEL_FILES,
        "python": platform.python_version(),
        "machine": platform.machine(),
        "packages": sorted(
            f"{p.metadata['Name']}=={p.version}"
            for p in importlib.metadata.distributions()
        ),
        "phonemizer_library_sha256": digest(Path(espeakng_loader.get_library_path())),
        "phonemizer_data": {
            str(p.relative_to(data)): digest(p)
            for p in sorted(data.rglob("*"))
            if p.is_file()
        },
        "inference": {
            "provider": "CPUExecutionProvider",
            "threads": threads,
            "inter_op_threads": 1,
            "seed": 0,
            "deterministic_compute": True,
            "speed": 1.0,
            "language": "en-us",
            "trim": True,
            "speed_dtype": "float32 (adapter for kokoro-onnx 0.4.9)",
            "input_order": "sorted filenames",
        },
        "format": {"channels": 1, "sample_bits": 16, "sample_rate": 24000},
    }
    if family_sessions:
        metadata["inference"].update(
            input_order="sorted filenames within each family",
            session_order=list(FAMILIES),
            session_reset="independent model session and seed zero per family",
        )
    return metadata


def render(kokoro, voice, text, path):
    metadata, _ = render_prompt(kokoro, voice, text, path)
    return metadata


def reuse_family(source, output, voice, threads, group):
    """Reuse a proven unchanged family with its recognized seed-zero recipe."""
    source_manifest = json.loads((source / "manifest.json").read_text())
    proof = json.loads((source / "reproduction-verification.json").read_text())
    if proof.get("all_manifests_and_wavs_identical") is not True or proof.get(
        "manifest_sha256"
    ) != digest(source / "manifest.json"):
        raise RuntimeError(
            "Current-time cache lacks matching complete reproduction proof"
        )
    if source_manifest["voice"] != voice:
        raise RuntimeError("Current-time cache uses a different voice")
    recipe = source_manifest["inference"]
    legacy_prefix = (
        recipe.get("input_order") == "sorted filenames"
        and "session_order" not in recipe
        and "session_reset" not in recipe
        and [e["file"] for e in source_manifest["assets"]]
        == sorted(e["file"] for e in source_manifest["assets"])
        and all(family(e["file"]) == "current" for e in source_manifest["assets"][:720])
    )
    partitioned = (
        recipe.get("input_order") == "sorted filenames within each family"
        and recipe.get("session_order") == list(FAMILIES)
        and recipe.get("session_reset")
        == "independent model session and seed zero per family"
    )
    if not (partitioned or (group == "current" and legacy_prefix)):
        raise RuntimeError("Current-time cache has an incompatible inference recipe")
    recorded = {
        k: v
        for k, v in source_manifest.items()
        if k not in ("voice", "assets", "stress_pass_version")
    }
    expected = environment(threads, family_sessions=True)
    # The original bank began with the same sorted current-time family in a
    # fresh seed-zero session. Later-family partitioning cannot affect it.
    for config in (recorded, expected):
        config["inference"] = dict(config["inference"])
        for key in ("input_order", "session_order", "session_reset"):
            config["inference"].pop(key, None)
    if recorded != expected:
        raise RuntimeError("Current-time cache preparation environment differs")
    entries = [e for e in source_manifest["assets"] if family(e["file"]) == group]
    if [(e["file"], e["text"]) for e in entries] != [
        (name, text) for name, text in prompts().items() if family(name) == group
    ]:
        raise RuntimeError("Current-time cache is incomplete or has changed text")
    result = []
    for entry in entries:
        corrected, corrections = postprocess_stress(
            entry["text"], entry["raw_phonemes"]
        )
        if (
            corrected != entry["corrected_phonemes"]
            or corrections != entry["corrections"]
        ):
            raise RuntimeError(f"Current-time cache stress differs: {entry['file']}")
        if digest(source / entry["file"]) != entry["sha256"]:
            raise RuntimeError(f"Current-time cache hash differs: {entry['file']}")
        shutil.copyfile(source / entry["file"], output / entry["file"])
        result.append({**entry, "stress_pass_version": PASS_VERSION})
    return result


def reuse_current(source, output, voice, threads):
    return reuse_family(source, output, voice, threads, "current")


def generate(models, output, voice, threads, current_cache=None, unchanged_cache=None):
    output.mkdir(parents=True, exist_ok=True)
    entries = []
    for group in FAMILIES:
        if group != "mode" and unchanged_cache is not None:
            cached = reuse_family(unchanged_cache, output, voice, threads, group)
            entries.extend(cached)
            print(
                f"Reused {len(cached)} independently verified {group} prompts",
                flush=True,
            )
            continue
        if group == "current" and current_cache is not None:
            entries.extend(reuse_current(current_cache, output, voice, threads))
            print("Reused 720 independently verified current-time prompts", flush=True)
            continue
        kokoro = load_model(models, threads)
        for filename, text in prompts().items():
            if family(filename) != group:
                continue
            entries.append(render(kokoro, voice, text, output / filename))
            if len(entries) % 50 == 1:
                print(f"Prepared {len(entries)}/771 prompts", flush=True)
        del kokoro
        gc.collect()
    entries.sort(key=lambda entry: entry["file"])
    manifest = {
        **environment(threads, family_sessions=True),
        "voice": voice,
        "stress_pass_version": PASS_VERSION,
        "assets": entries,
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def verify(models, output, quick):
    manifest = json.loads((output / "manifest.json").read_text())
    if [(entry["file"], entry["text"]) for entry in manifest["assets"]] != list(
        prompts().items()
    ):
        raise RuntimeError(
            "Candidate does not contain the complete canonical prompt corpus in order"
        )
    recorded_environment = {
        k: v
        for k, v in manifest.items()
        if k not in ("voice", "assets", "stress_pass_version")
    }
    threads = manifest["inference"]["threads"]
    if recorded_environment != environment(threads, family_sessions=True):
        raise RuntimeError("Preparation environment differs from recorded manifest")
    if manifest.get("stress_pass_version") != PASS_VERSION:
        raise RuntimeError("Candidate uses a different stress pass")
    for entry in manifest["assets"]:
        if digest(output / entry["file"]) != entry["sha256"]:
            raise RuntimeError(f"Packaged hash differs: {entry['file']}")
    # Recreate every family session and execute its complete inference order.
    # Quick verification still executes all prompts; it limits comparisons.
    selected = {0, 1, 2, 768, 769, 770} if quick else set(range(771))
    with tempfile.TemporaryDirectory(prefix="shush-neural-bank-verify-") as temporary:
        for group in FAMILIES:
            kokoro = load_model(models, threads)
            for i, entry in enumerate(manifest["assets"]):
                if family(entry["file"]) != group:
                    continue
                reproduced = render(
                    kokoro,
                    manifest["voice"],
                    entry["text"],
                    Path(temporary) / entry["file"],
                )
                if i in selected and reproduced != entry:
                    raise RuntimeError(
                        f"Regeneration differs: {entry['file']}; deterministic reproduction failed"
                    )
                if i % 50 == 0:
                    print(f"Verified inference order {i+1}/771", flush=True)
            del kokoro
            gc.collect()
    print(
        f"Verified {len(selected)} regenerated WAV hashes and all 771 packaged hashes"
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models", type=Path, default=Path("/tmp/shush-kokoro-models"))
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "voice-candidate",
    )
    parser.add_argument(
        "--voice", choices=["am_michael", "am_fenrir"], default="am_michael"
    )
    parser.add_argument("--threads", type=int, choices=[1, 2, 4], default=2)
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--quick-verify", action="store_true")
    parser.add_argument(
        "--reuse-current",
        type=Path,
        help="Reuse unchanged current-time WAVs from a bank with complete reproduction proof",
    )
    parser.add_argument(
        "--reuse-unchanged",
        type=Path,
        help="Reuse proven unchanged current/error/wake families; render the mode family anew",
    )
    args = parser.parse_args()
    if args.reuse_current is not None and args.reuse_unchanged is not None:
        parser.error("Choose only one reuse option")
    if args.output.resolve() == Path(__file__).resolve().parents[1] / "voice" and not (
        args.verify or args.quick_verify
    ):
        raise RuntimeError(
            "Generate into a candidate directory before replacing the runtime bank"
        )
    if args.verify or args.quick_verify:
        verify(args.models, args.output, args.quick_verify)
    else:
        manifest = generate(
            args.models,
            args.output,
            args.voice,
            args.threads,
            args.reuse_current,
            args.reuse_unchanged,
        )
        print(
            f"Generated {len(manifest['assets'])} candidate WAV prompts for {args.voice}"
        )


if __name__ == "__main__":
    main()
