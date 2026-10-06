#!/usr/bin/env python3
"""Reproduce the historical, unselected eSpeak NG baseline bank.

The active selected neural pipeline is generate_neural_voice.py and applies
the shared post-phonemizer lexical stress pass to every appliance prompt.

Preparation tool only. Uses curl and dpkg-deb, never installs system packages.
The pinned generator is Linux x86-64; the resulting PCM WAVs are portable.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import wave

PACKAGES = [
    (
        "universe/e/espeak-ng/espeak-ng_1.51+dfsg-12build1_amd64.deb",
        "ffeac730f1f43b5cdbca708a8215d6b7310bf3bd40d7dcf0affd6a62f86aa6df",
    ),
    (
        "main/e/espeak-ng/libespeak-ng1_1.51+dfsg-12build1_amd64.deb",
        "aab65c41ccd62c3998d0fc45cdb9e7e3a2d91031d7ee51b913775718b3f0f60e",
    ),
    (
        "main/e/espeak-ng/espeak-ng-data_1.51+dfsg-12build1_amd64.deb",
        "60057f68ba6f79e69aafd943e67e7d9a844de0531e77dc86b1971c43782e4d88",
    ),
    (
        "main/p/pcaudiolib/libpcaudio0_1.2-2build3_amd64.deb",
        "a0528a39edac8a05174a219315392422e54ce9f85364a3ca001fdbf25da2dc4e",
    ),
    (
        "main/s/sonic/libsonic0_0.2.0-13build1_amd64.deb",
        "2cdd82b71f417cd41725452e91649708e129b4f706eca7a11a7815acb0ed4ad1",
    ),
    (
        "main/p/pulseaudio/libpulse0_16.1+dfsg1-2ubuntu10.1_amd64.deb",
        "b8d52aa6c2f74ae99e749c9f98d6ad2dbf924fd422371ac469725245cdd919fc",
    ),
    (
        "main/liba/libasyncns/libasyncns0_0.8-6build4_amd64.deb",
        "f8483917fd216e248143eba1c80dfc9384b94fb4dac402194a98c11505674269",
    ),
    (
        "main/libs/libsndfile/libsndfile1_1.2.2-1ubuntu5.24.04.1_amd64.deb",
        "fe2461b27e747fa88ede157ea39c6379e2101c280a730cda9c825f566a3808e5",
    ),
    (
        "main/f/flac/libflac12t64_1.4.3+ds-2.1ubuntu2_amd64.deb",
        "f2396692573407b2fbc92cd83148342d4aec717d42d5e24ac64c471204a0b7a9",
    ),
    (
        "main/libv/libvorbis/libvorbis0a_1.3.7-1build3_amd64.deb",
        "d704ac0a5dd7a9797a2f2dc86fc8333c9973023b98b3510dcd2f1cc00da3624d",
    ),
    (
        "main/libv/libvorbis/libvorbisenc2_1.3.7-1build3_amd64.deb",
        "866c561fe81d4430a48c820f4e62f3cd138f4c8c7664f9203f02c1ced2eb55d4",
    ),
    (
        "main/o/opus/libopus0_1.4-1build1_amd64.deb",
        "6d8da32a832e655c9603543b47a0bc90c6f60bdee0c00fe7499bd50acad566cf",
    ),
    (
        "main/libo/libogg/libogg0_1.3.5-3build1_amd64.deb",
        "fbdb2fedbafb02c3f07f2c2ddeb4018b5e94e13462f93fbececdcefc06fe2ac1",
    ),
    (
        "main/m/mpg123/libmpg123-0t64_1.32.5-1ubuntu1.1_amd64.deb",
        "bbe32128e3358f141f9f58ef6e76a7bef3e07d09165203665d30fdc27372b418",
    ),
    (
        "main/l/lame/libmp3lame0_3.100-6build1_amd64.deb",
        "d739e9a3565f98b8b72a9e203a9a537bd1090af6cafb94c98adf8e27f9d3fb94",
    ),
]
VOICE = ["-v", "en-us", "-s", "155", "-p", "50", "-a", "100", "-g", "0"]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def bootstrap(cache):
    packages = cache / "debs"
    root = cache / "root"
    packages.mkdir(parents=True, exist_ok=True)
    root.mkdir(parents=True, exist_ok=True)
    for relative, expected in PACKAGES:
        target = packages / relative.rsplit("/", 1)[1]
        if not target.exists():
            subprocess.run(
                [
                    "curl",
                    "--fail",
                    "--location",
                    "--retry",
                    "2",
                    "https://archive.ubuntu.com/ubuntu/pool/" + relative,
                    "--output",
                    str(target),
                ],
                check=True,
            )
        if digest(target) != expected:
            raise RuntimeError(f"Package checksum mismatch: {target}")
        subprocess.run(["dpkg-deb", "--extract", str(target), str(root)], check=True)
    return root


def time_text(hour, minute):
    if minute == 0:
        return f"{hour} o'clock"
    if minute < 10:
        return f"{hour} oh {minute}"
    return f"{hour} {minute}"


def prompts():
    result = {
        "mode-reading.wav": "Complete reading playlist, then pink noise.",
        "mode-noise.wav": "Pink noise immediately.",
        "error.wav": "Playback cannot start. Check appliance diagnostics.",
    }
    for hour in range(1, 13):
        for minute in range(60):
            spoken = time_text(hour, minute)
            result[f"current-{hour}-{minute:02}.wav"] = f"The time is {spoken}."
            if minute % 15 == 0:
                result[f"wake-{hour}-{minute:02}.wav"] = f"Wake time, {spoken}."
    return dict(sorted(result.items()))


def generate(root, output):
    output.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env["LD_LIBRARY_PATH"] = (
        str(root / "usr/lib/x86_64-linux-gnu")
        + ":"
        + str(root / "usr/lib/x86_64-linux-gnu/pulseaudio")
    )
    env["ESPEAK_DATA_PATH"] = str(root / "usr/lib/x86_64-linux-gnu")
    env["LC_ALL"] = "C"
    binary = root / "usr/bin/espeak-ng"
    version = subprocess.check_output(
        [str(binary), "--version"], env=env, text=True
    ).strip()
    if not version.startswith("eSpeak NG text-to-speech: 1.51"):
        raise RuntimeError(f"Unexpected generator: {version}")
    entries = []
    for filename, text in prompts().items():
        path = output / filename
        subprocess.run(
            [str(binary), *VOICE, "-w", str(path), text], env=env, check=True
        )
        with wave.open(str(path), "rb") as wav:
            if (wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) != (
                1,
                2,
                22050,
            ):
                raise RuntimeError(f"Unexpected WAV format: {path}")
            frames = wav.getnframes()
        entries.append(
            {"file": filename, "text": text, "sha256": digest(path), "frames": frames}
        )
    manifest = {
        "generator": "eSpeak NG 1.51, Ubuntu 1.51+dfsg-12build1, x86-64",
        "packages": [{"path": p, "sha256": sha} for p, sha in PACKAGES],
        "arguments": VOICE,
        "format": {"channels": 1, "sample_bits": 16, "sample_rate": 22050},
        "assets": entries,
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cache", type=Path, default=Path("/tmp/shush-voice-generator")
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "voice-espeak-baseline",
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="regenerate and compare without changing packaged assets",
    )
    args = parser.parse_args()
    root = bootstrap(args.cache.resolve())
    if args.verify:
        original = json.loads((args.output / "manifest.json").read_text())
        with tempfile.TemporaryDirectory(prefix="shush-voice-verify-") as temporary:
            generated = generate(root, Path(temporary))
        if generated != original:
            raise RuntimeError("Regenerated voice manifest differs")
        for entry in original["assets"]:
            if digest(args.output / entry["file"]) != entry["sha256"]:
                raise RuntimeError(f"Packaged asset checksum mismatch: {entry['file']}")
        print(f"Verified {len(original['assets'])} reproducible WAV assets")
    else:
        manifest = generate(root, args.output.resolve())
        print(f"Generated {len(manifest['assets'])} WAV assets in {args.output}")


if __name__ == "__main__":
    main()
