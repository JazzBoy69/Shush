#!/usr/bin/env python3
"""Assemble boot/system/settings into a flashable image without mounting it."""

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile


def digest(path):
    result = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def main():
    board = Path(__file__).resolve().parent
    binaries = Path(os.environ["BINARIES_DIR"]).resolve()
    host = Path(os.environ["HOST_DIR"])
    build = Path(os.environ["BUILD_DIR"])
    config_source = Path(
        os.environ.get("SHUSH_DEVICE_CONFIG", str(board / "shush.conf"))
    )
    shutil.copyfile(config_source, binaries / "shush.conf")
    shutil.copyfile(board / "cmdline.txt", binaries / "rpi-firmware/cmdline.txt")
    settings = binaries / "settings.ext4"
    with settings.open("wb") as filesystem:
        filesystem.truncate(64 * 1024 * 1024)
    # Separate 64 MiB filesystem, never format physical media. PID 1 assigns
    # its mount root to the fixed service account via StateDirectory=shush.
    subprocess.run(
        [
            str(host / "sbin/mkfs.ext4"),
            "-q",
            "-F",
            "-b",
            "4096",
            "-L",
            "shush-settings",
            "-U",
            "a2ebc8ab-8823-49e2-b94e-e35b54ce6111",
            str(settings),
            "16384",
        ],
        check=True,
    )
    files = sorted(
        p.relative_to(binaries).as_posix()
        for p in (binaries / "rpi-firmware").iterdir()
    )
    files += sorted(p.name for p in binaries.glob("*.dtb"))
    files += ["Image", "shush.conf"]
    config = binaries / "shush-genimage.cfg"
    quoted = ",\n".join("            " + json.dumps(name) for name in files)
    config.write_text(
        """image boot.vfat {
    vfat { files = {
"""
        + quoted
        + """
    } }
    size = 64M
}
image sdcard.img {
    hdimage { }
    partition boot {
        offset = 1M
        partition-type = 0xC
        bootable = true
        image = "boot.vfat"
    }
    partition system {
        partition-type = 0x83
        image = "rootfs.ext4"
    }
    partition settings {
        partition-type = 0x83
        image = "settings.ext4"
    }
}
"""
    )
    # Empty rootpath: rootfs.ext4 is already built, so copying media again is unnecessary.
    with tempfile.TemporaryDirectory(prefix="shush-genimage-root-") as rootpath:
        subprocess.run(
            [
                str(host / "bin/genimage"),
                "--rootpath",
                rootpath,
                "--tmppath",
                str(build / "shush-genimage"),
                "--inputpath",
                str(binaries),
                "--outputpath",
                str(binaries),
                "--config",
                str(config),
            ],
            check=True,
        )
    output = binaries / "sdcard.img"
    sha = digest(output)
    (binaries / "sdcard.img.sha256").write_text(sha + "  sdcard.img\n")
    (binaries / "shush-image.json").write_text(
        json.dumps(
            {
                "buildroot": "2026.08",
                "architecture": "aarch64",
                "board": "Raspberry Pi 3 B+",
                "image": output.name,
                "image_sha256": sha,
                "image_bytes": output.stat().st_size,
                "root_mount": "ro,noload",
                "settings_partition_bytes": settings.stat().st_size,
                "configuration_sha256": digest(binaries / "shush.conf"),
                "hardware_boot_verified": False,
            },
            indent=2,
        )
        + "\n"
    )


if __name__ == "__main__":
    main()
