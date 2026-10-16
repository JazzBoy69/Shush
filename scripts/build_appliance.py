#!/usr/bin/env python3
"""Build a pinned Shush Pi 3 B+ SD image; never write a physical device."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile

VERSION = "2026.08"
ARCHIVE_SHA256 = "87aaca4164ea9d5c8085854953018263f7963f07c22e73a2a2185cc98c581c34"
PROJECT = Path(__file__).resolve().parents[1]


def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def extract_buildroot(archive, destination):
    """Defer rootfs absolute symlinks until all archive file writes are done."""
    destination = Path(destination).resolve()
    with tarfile.open(archive) as bundle:
        members = bundle.getmembers()
        links = [item for item in members if item.issym() and item.linkname.startswith("/")]
        bundle.extractall(destination, members=(item for item in members if item not in links),
                          filter="data")
        for item in links:
            path = destination / item.name
            if not path.resolve().is_relative_to(destination):
                raise ValueError("Buildroot symlink path escapes extraction directory")
            path.symlink_to(item.linkname)


def publish_candidate(images):
    """Publish only after verification; keep the previous image and metadata."""
    candidate = images / "sdcard-candidate.img"
    manifest = json.loads((images / "shush-candidate.json").read_text())
    if not manifest.get("image_structure_verified") or manifest.get("aarch64_playlists_validated") != 366:
        raise RuntimeError("Candidate has not passed full verification")
    if digest(candidate) != manifest["image_sha256"]:
        raise RuntimeError("Candidate changed after verification")
    published = ("sdcard.img", "sdcard.img.sha256", "shush-image.json")
    if any((images / name).exists() for name in published):
        backup = Path(tempfile.mkdtemp(prefix="previous-image-", dir=images))
        for name in published:
            if (images / name).exists():
                if name == "sdcard.img":
                    # Image publication always replaces the inode.
                    os.link(images / name, backup / name)
                else:
                    shutil.copyfile(images / name, backup / name)
    manifest["image"] = "sdcard.img"
    (images / "shush-candidate.json").write_text(json.dumps(manifest, indent=2) + "\n")
    (images / "sdcard-candidate.img.sha256").write_text(manifest["image_sha256"] + "  sdcard.img\n")
    os.replace(candidate, images / "sdcard.img")
    os.replace(images / "sdcard-candidate.img.sha256", images / "sdcard.img.sha256")
    os.replace(images / "shush-candidate.json", images / "shush-image.json")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, default=PROJECT / ".appliance-build")
    parser.add_argument(
        "--config",
        type=Path,
        help="Optional boot shush.conf overrides; defaults to automatic USB selection",
    )
    parser.add_argument("--jobs", type=int, default=4)
    parser.add_argument(
        "--host-tools", type=Path, help="Additional directory of host tools"
    )
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument("--private-config", type=Path, default=PROJECT / ".local/appliance",
                        help="Private Wi-Fi/MQTT directory (never committed)")
    args = parser.parse_args()
    work = args.work.resolve()
    if any(character.isspace() for character in str(work) + str(PROJECT)):
        parser.error("Buildroot build and project paths must contain no whitespace")
    if not 1 <= args.jobs <= 32:
        parser.error("--jobs must be between 1 and 32")
    config = args.config.resolve() if args.config else None
    if config and not config.is_file():
        parser.error("Device configuration file does not exist")
    work.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    # Exclude whitespace-bearing WSL/desktop PATH entries, which Buildroot rejects.
    paths = [
        entry
        for entry in env.get("PATH", "").split(os.pathsep)
        if entry and not any(character.isspace() for character in entry)
    ]
    if args.host_tools:
        paths.insert(0, str(args.host_tools.resolve()))
    env["PATH"] = os.pathsep.join(paths)
    env["PYTHON"] = "/usr/bin/python3"
    private_config = args.private_config.resolve()
    if not args.verify_only and not private_config.is_dir():
        parser.error("Private network configuration missing; run scripts/configure_network.py")
    env["SHUSH_PRIVATE_CONFIG"] = str(private_config)
    if config:
        env["SHUSH_DEVICE_CONFIG"] = str(config)
    if not args.verify_only:
        required = (
            "make",
            "gcc",
            "g++",
            "perl",
            "tar",
            "wget",
            "cpio",
            "unzip",
            "rsync",
            "bc",
            "patch",
            "curl",
            "python3",
            "file",
        )
        missing = [
            tool for tool in required if not shutil.which(tool, path=env["PATH"])
        ]
        if missing:
            parser.error("Missing build tools: " + ", ".join(missing))
        archive = work / f"buildroot-{VERSION}.tar.xz"
        if not archive.exists():
            partial = archive.with_suffix(archive.suffix + ".part")
            subprocess.run(
                [
                    "curl",
                    "--fail",
                    "--location",
                    "--retry",
                    "2",
                    f"https://buildroot.org/downloads/buildroot-{VERSION}.tar.xz",
                    "--output",
                    str(partial),
                ],
                check=True,
                env=env,
            )
            if digest(partial) != ARCHIVE_SHA256:
                raise SystemExit("Buildroot archive checksum mismatch")
            partial.rename(archive)
        if digest(archive) != ARCHIVE_SHA256:
            raise SystemExit("Buildroot archive checksum mismatch")
        source = work / f"buildroot-{VERSION}"
        if not source.exists():
            with tempfile.TemporaryDirectory(
                dir=work, prefix="extract-buildroot-"
            ) as folder:
                extract_buildroot(archive, folder)
                extracted = Path(folder) / source.name
                if not (extracted / "Makefile").is_file():
                    raise SystemExit("Buildroot archive lacks its expected source tree")
                extracted.rename(source)
        output = work / "output"
        command = [
            "make",
            "-C",
            str(source),
            f"O={output}",
            f"BR2_EXTERNAL={PROJECT / 'deploy/appliance'}",
            f"BR2_DL_DIR={work / 'downloads'}",
            f"BR2_JLEVEL={args.jobs}",
        ]
        profile = hashlib.sha256()
        for path in sorted((PROJECT / "deploy/appliance").rglob("*")):
            if (
                path.is_file()
                and path.suffix != ".md"
                and "__pycache__" not in path.parts
            ):
                profile.update(path.relative_to(PROJECT).as_posix().encode())
                profile.update(path.read_bytes())
        fingerprint = profile.hexdigest()
        record = output / ".shush-profile-sha256"
        config_record = output / ".shush-config-sha256"
        if (output / ".config").exists():
            if not record.is_file() or record.read_text().strip() != fingerprint:
                raise SystemExit(
                    "Appliance profile changed or output belongs to another build. Use a fresh --work directory."
                )
            if (
                not config_record.is_file()
                or config_record.read_text().strip() != digest(output / ".config")
            ):
                raise SystemExit(
                    "Build configuration was changed outside the appliance builder. Use a fresh --work directory."
                )
        else:
            subprocess.run(command + ["shush_pi3_defconfig"], check=True, env=env)
            record.write_text(fingerprint + "\n")
            config_record.write_text(digest(output / ".config") + "\n")
        print(
            f"Building appliance image; full output in {work / 'build.log'}", flush=True
        )
        with (work / "build.log").open("a") as log:
            try:
                # Local Rust sources and the lockfile may have changed since
                # the preceding build; resync and revendor before compilation.
                subprocess.run(
                    command + ["shush-reconfigure"],
                    check=True,
                    env=env,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                )
                subprocess.run(
                    command, check=True, env=env, stdout=log, stderr=subprocess.STDOUT
                )
            except subprocess.CalledProcessError:
                print(
                    "\n".join(
                        (work / "build.log")
                        .read_text(errors="replace")
                        .splitlines()[-35:]
                    )
                )
                raise
    subprocess.run(
        [
            "/usr/bin/python3",
            str(PROJECT / "scripts/verify_appliance.py"),
            "--output",
            str(work / "output"),
        ] + ([] if args.verify_only else ["--candidate"]),
        check=True,
        env=env,
    )
    if not args.verify_only:
        publish_candidate(work / "output/images")
    print(f"Image: {work / 'output/images/sdcard.img'}", flush=True)


if __name__ == "__main__":
    main()
