#!/usr/bin/env python3
"""Prepare active stress-corrected previews; never modifies voice/.

Historical comparisons require --historical and deliberately bypass the pass.

Use Python 3.13 in an isolated environment with voice-samples/requirements.txt.
Download the two model files from the pinned GitHub release before running.
"""

import argparse
import hashlib
import importlib.metadata
import json
from pathlib import Path
import platform
import tempfile
import wave

MODEL_FILES = {
    "kokoro-v1.0.onnx": "beb0d1848dee9a49da392cc3df26958d46cfa35d321edf434f52949153f0df3a",
    "voices-v1.0.bin": "bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d",
}
RELEASE = (
    "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.1/"
)
VOICES = {"michael": "am_michael", "fenrir": "am_fenrir"}
PROMPTS = {
    "wake-6-30": "Wake time, six thirty.",
    "mode-reading": "Complete reading playlist, then pink noise.",
    "current-9-15": "The time is nine fifteen.",
}
REVISED_PROMPTS = {
    "wake-6-30": "Your alarm is set for six thirty.",
    "mode-reading": "Reading.",
    "mode-noise": "No reading.",
    "current-9-15": "The current time is nine fifteen.",
    "current-6-30": "The current time is six thirty.",
    "compare-wake-original": "Wake time, six thirty.",
    "compare-it-is-6-30": "It is six thirty.",
    "compare-alarm-6-15": "Your alarm is set for six fifteen.",
    "compare-alarm-6-45": "Your alarm is set for six forty-five.",
    "compare-current-original": "The time is nine fifteen.",
    "compare-current-it-is": "It is nine fifteen.",
}


def digest(path):
    sha = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            sha.update(block)
    return sha.hexdigest()


def write_wav(path, pcm, rate):
    with wave.open(str(path), "wb") as destination:
        destination.setnchannels(1)
        destination.setsampwidth(2)
        destination.setframerate(rate)
        destination.writeframes(pcm.tobytes())


def load_model(models, threads=1):
    import numpy as np
    import onnxruntime as rt
    from kokoro_onnx import Kokoro

    for filename, expected in MODEL_FILES.items():
        if digest(models / filename) != expected:
            raise RuntimeError(f"Model hash mismatch: {filename}")
    rt.set_seed(0)
    rt.disable_telemetry_events()
    np.random.seed(0)
    options = rt.SessionOptions()
    options.intra_op_num_threads = threads
    options.inter_op_num_threads = 1
    options.execution_mode = rt.ExecutionMode.ORT_SEQUENTIAL
    options.use_deterministic_compute = True
    session = rt.InferenceSession(
        str(models / "kokoro-v1.0.onnx"),
        sess_options=options,
        providers=["CPUExecutionProvider"],
    )

    class FloatSpeedSession:
        # kokoro-onnx 0.4.9 passes int32 for the new-export speed input.
        # The pinned model declares tensor(float); correct only that dtype.
        def __getattr__(self, name):
            return getattr(session, name)

        def run(self, names, inputs):
            inputs = dict(inputs)
            inputs["speed"] = np.asarray(inputs["speed"], dtype=np.float32)
            result = session.run(names, inputs)
            if names is None:
                # Preserve the model's alignment output without another
                # inference or any change to its waveform/random state.
                self.last_outputs = dict(
                    zip((o.name for o in session.get_outputs()), result)
                )
            return result

    speed_input = next(i for i in session.get_inputs() if i.name == "speed")
    if speed_input.type != "tensor(float)":
        raise RuntimeError("Unexpected model speed type")
    return Kokoro.from_session(FloatSpeedSession(), str(models / "voices-v1.0.bin"))


def generate(models, output, revised=False):
    """Historical raw replay only: preserve the previously presented evidence."""
    import numpy as np
    import espeakng_loader

    threads = 2 if revised else 1
    kokoro = load_model(models, threads)
    output.mkdir(parents=True, exist_ok=True)
    entries = []
    voices = {"michael": "am_michael"} if revised else VOICES
    phrases = REVISED_PROMPTS if revised else PROMPTS
    rendered = {}
    for name, voice in voices.items():
        parts = []
        for label, text in phrases.items():
            # Synthesize each actual short prompt separately. A long combined
            # utterance would conceal short-prompt pronunciation/prosody issues.
            audio, rate = kokoro.create(
                text, voice=voice, speed=1.0, lang="en-us", trim=True
            )
            if rate != 24000 or audio.size == 0 or not np.isfinite(audio).all():
                raise RuntimeError("Invalid neural speech output")
            pcm = np.rint(np.clip(audio, -1.0, 1.0) * 32767).astype("<i2")
            path = output / f"{name}-{label}.wav"
            write_wav(path, pcm, rate)
            entries.append(
                {
                    "file": path.name,
                    "voice": voice,
                    "text": text,
                    "frames": int(pcm.size),
                    "sha256": digest(path),
                }
            )
            parts.append(pcm)
            parts.append(np.zeros(12000, dtype="<i2"))
            rendered[label] = pcm
        if revised:
            # Only the five revised appliance messages belong in this preview.
            parts = []
            for label in list(REVISED_PROMPTS)[:5]:
                parts.extend([rendered[label], np.zeros(12000, dtype="<i2")])
        combined = np.concatenate(parts[:-1])
        path = output / f"{name}-preview.wav"
        write_wav(path, combined, 24000)
        entries.append(
            {
                "file": path.name,
                "voice": voice,
                "text": " | ".join(
                    list(phrases.values())[:5] if revised else phrases.values()
                ),
                "frames": int(combined.size),
                "sha256": digest(path),
            }
        )
        if revised:
            comparison = [
                "compare-wake-original",
                "wake-6-30",
                "compare-it-is-6-30",
                "compare-alarm-6-15",
                "compare-alarm-6-45",
                "compare-current-original",
                "compare-current-it-is",
                "current-9-15",
                "current-6-30",
            ]
            parts = []
            for label in comparison:
                parts.extend([rendered[label], np.zeros(18000, dtype="<i2")])
            pcm = np.concatenate(parts[:-1])
            path = output / "michael-time-comparison.wav"
            write_wav(path, pcm, 24000)
            entries.append(
                {
                    "file": path.name,
                    "voice": voice,
                    "text": " | ".join(phrases[label] for label in comparison),
                    "frames": int(pcm.size),
                    "sha256": digest(path),
                }
            )
    library = Path(espeakng_loader.get_library_path())
    data = Path(espeakng_loader.get_data_path())
    manifest = {
        "purpose": "Voice previews; no runtime voice bank replaced or voice approval inferred.",
        "model_release": RELEASE,
        "models": MODEL_FILES,
        "python": platform.python_version(),
        "machine": platform.machine(),
        "packages": sorted(
            f"{p.metadata['Name']}=={p.version}"
            for p in importlib.metadata.distributions()
        ),
        "phonemizer_library_sha256": digest(library),
        "phonemizer_data": {
            str(p.relative_to(data)): digest(p)
            for p in sorted(data.rglob("*"))
            if p.is_file()
        },
        "inference": {
            "provider": "CPUExecutionProvider",
            "threads": threads,
            "seed": 0,
            "deterministic_compute": True,
            "speed": 1.0,
            "language": "en-us",
            "trim": True,
            "speed_dtype": "float32 (adapter for kokoro-onnx 0.4.9)",
        },
        "format": {"channels": 1, "sample_bits": 16, "sample_rate": 24000},
        "preview_gap_frames": 12000,
        "assets": entries,
    }
    if revised:
        manifest["time_comparison_gap_frames"] = 18000
        manifest["revision"] = "Michael revised mode and time wording"
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models", type=Path, default=Path("/tmp/shush-kokoro-models"))
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "voice-samples",
    )
    parser.add_argument("--verify", action="store_true")
    parser.add_argument(
        "--historical",
        action="store_true",
        help="explicit raw replay of archived comparison samples",
    )
    parser.add_argument(
        "--revised",
        action="store_true",
        help="Michael-only revised short mode/time messages",
    )
    args = parser.parse_args()
    if not args.historical:
        if args.revised:
            parser.error(
                "--revised reproduces archived samples and requires --historical; default previews use the active shared stress pass"
            )
        from preview_stress import generate as active_generate

        if args.output == Path(__file__).resolve().parents[1] / "voice-samples":
            args.output = args.output / "stress-v7"
        if args.verify:
            original = json.loads((args.output / "manifest.json").read_text())
            with tempfile.TemporaryDirectory(
                prefix="shush-active-preview-verify-"
            ) as temporary:
                regenerated = active_generate(args.models, Path(temporary))
            if original != regenerated:
                raise RuntimeError("Active previews differ on regeneration")
            for entry in original["assets"]:
                if digest(args.output / entry["file"]) != entry["sha256"]:
                    raise RuntimeError(
                        f"Active packaged preview differs: {entry['file']}"
                    )
            print("Verified active stress-corrected previews bit-for-bit")
        else:
            active_generate(args.models, args.output)
            print(f"Generated active stress-corrected previews in {args.output}")
        return
    if (
        args.revised
        and args.output == Path(__file__).resolve().parents[1] / "voice-samples"
    ):
        args.output = args.output / "revised"
    if args.verify:
        original = json.loads((args.output / "manifest.json").read_text())
        with tempfile.TemporaryDirectory(
            prefix="shush-neural-preview-verify-"
        ) as temporary:
            regenerated = generate(args.models, Path(temporary), args.revised)
        if original != regenerated:
            raise RuntimeError("Regenerated samples differ")
        for entry in original["assets"]:
            if digest(args.output / entry["file"]) != entry["sha256"]:
                raise RuntimeError(f"Packaged sample differs: {entry['file']}")
        print(
            "Verified all neural voice previews reproduce exactly in this environment"
        )
    else:
        manifest = generate(args.models, args.output, args.revised)
        print(f"Generated {len(manifest['assets'])} WAV samples in {args.output}")


if __name__ == "__main__":
    main()
