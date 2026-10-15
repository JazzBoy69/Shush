#!/usr/bin/env python3
"""Verify a built appliance image and execute its AArch64 app under QEMU."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
import tempfile
import time

PROJECT = Path(__file__).resolve().parents[1]


def digest(path):
    result = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def partitions(image):
    with image.open("rb") as source:
        mbr = source.read(512)
    require(mbr[510:512] == b"\x55\xaa", "Missing DOS partition table")
    require(
        [mbr[450 + index * 16] for index in range(4)] == [0xC, 0x83, 0x83, 0],
        "Partition slots must match kernel/fstab device numbers",
    )
    result = []
    for index in range(4):
        entry = mbr[446 + index * 16 : 462 + index * 16]
        start, sectors = struct.unpack_from("<II", entry, 8)
        if entry[4]:
            result.append((entry[4], start * 512, sectors * 512))
    require(
        len(result) == 3,
        "Image must have boot, system, and settings partitions",
    )
    require([p[0] for p in result] == [0xC, 0x83, 0x83], "Unexpected partition types")
    for left, right in zip(result, result[1:]):
        require(left[1] + left[2] <= right[1], "Overlapping partitions")
    require(result[-1][1] + result[-1][2] <= image.stat().st_size, "Truncated SD image")
    return result


def compare_partition(image, offset, size, filesystem):
    require(
        size == filesystem.stat().st_size, f"Partition size differs: {filesystem.name}"
    )
    with image.open("rb") as disk, filesystem.open("rb") as source:
        disk.seek(offset)
        remaining = size
        while remaining:
            count = min(1024 * 1024, remaining)
            require(
                disk.read(count) == source.read(count),
                f"Partition differs: {filesystem.name}",
            )
            remaining -= count


def media_schedules(schedule):
    """Partition complete original playlists; repeat only to satisfy the app schema."""
    require(
        schedule["cycle_days"] == 366
        and set(schedule["days"]) == {str(day) for day in range(366)},
        "Incomplete source schedule for ARM validation",
    )
    parts = []
    for first in range(0, 366, 122):
        days = list(range(first, first + 122))
        fixture = dict(schedule)
        fixture["days"] = {
            str(index): schedule["days"][str(days[index % len(days)])]
            for index in range(366)
        }
        parts.append((days, fixture))
    return parts


def validate_arm_media(command, target, images):
    schedule = json.loads(
        (target / "opt/shush/reference/reading_schedule.json").read_text()
    )
    parts = media_schedules(schedule)
    processes = []
    logs = []
    records = []
    with tempfile.TemporaryDirectory(prefix="shush-arm-media-") as folder:
        try:
            for index, (days, fixture) in enumerate(parts):
                root = Path(folder) / str(index)
                (root / "reference").mkdir(parents=True)
                for name in ("Audio", "voice", "Pink_Noise.wav"):
                    (root / name).symlink_to(target / "opt/shush" / name)
                (root / "reference/reading_schedule.json").write_text(
                    json.dumps(fixture)
                )
                log_path = images / f"arm-media-{index + 1}.log"
                log = log_path.open("w")
                logs.append(log)
                processes.append(
                    subprocess.Popen(
                        command
                        + [
                            "validate-media",
                            "--root",
                            str(root),
                            "--settings",
                            str(root / "settings.json"),
                        ],
                        stdout=log,
                        stderr=subprocess.STDOUT,
                    )
                )
                records.append({"original_days": days, "log": log_path.name})
            previous = [None] * len(parts)
            while True:
                for index, process in enumerate(processes):
                    lines = (images / records[index]["log"]).read_text().splitlines()
                    last = lines[-1] if lines else "starting"
                    if previous[index] != last:
                        print(f"ARM media part {index + 1}: {last}", flush=True)
                        previous[index] = last
                    require(
                        process.poll() in (None, 0),
                        f"ARM media part {index + 1} failed: " + "\n".join(lines[-10:]),
                    )
                if all(process.poll() == 0 for process in processes):
                    break
                time.sleep(5)
        finally:
            for process in processes:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
            for log in logs:
                log.close()
    for record in records:
        record["log_sha256"] = digest(images / record["log"])
    return records


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--structure-only",
        action="store_true",
        help="Verify packaged image and ARM asset loading without repeating MP3 decoding",
    )
    args = parser.parse_args()
    output = args.output.resolve()
    images, host, target = output / "images", output / "host", output / "target"
    image = images / "sdcard.img"
    manifest = json.loads((images / "shush-image.json").read_text())
    require(digest(image) == manifest["image_sha256"], "SD image checksum mismatch")
    table = partitions(image)
    for entry, filename in zip(table, ("boot.vfat", "rootfs.ext4", "settings.ext4")):
        compare_partition(image, entry[1], entry[2], images / filename)
    require(table[2][2] == 64 * 1024 * 1024, "Settings partition must be 64 MiB")
    kernel_config = subprocess.check_output(
        [
            str(output / "build/linux-custom/scripts/extract-ikconfig"),
            str(images / "Image"),
        ]
    ).decode()
    required_kernel_options = (
        "ARM64",
        "INPUT_EVDEV",
        "USB_HID",
        "SND_USB_AUDIO",
        "USB_LAN78XX",
        "EXT4_FS",
        "VFAT_FS",
        "RC_CORE",
        "RC_MAP",
        "IR_MCEUSB",
        "IR_RC6_DECODER",
        "IR_MCE_KBD_DECODER",
        "CGROUPS",
        "MEMCG",
        "CFS_BANDWIDTH",
        "CGROUP_PIDS",
        "CFG80211",
        "BRCMFMAC",
        "BRCMFMAC_SDIO",
    )
    for option in required_kernel_options:
        require(
            f"CONFIG_{option}=y\n" in kernel_config,
            "Missing built-in kernel support: " + option,
        )
    (images / "kernel.config").write_text(kernel_config)
    manifest["kernel_sha256"] = digest(images / "Image")
    manifest["kernel_config_sha256"] = digest(images / "kernel.config")
    rootfs = images / "rootfs.ext4"
    with tempfile.TemporaryDirectory(prefix="shush-image-verify-") as folder:
        scratch = Path(folder)

        def read_root(path):
            dump = scratch / "dump"
            dump.unlink(missing_ok=True)
            subprocess.run(
                [str(host / "sbin/debugfs"), "-R", f"dump {path} {dump}", str(rootfs)],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            require(dump.exists(), f"Missing file in system image: {path}")
            return dump.read_bytes()

        binary = read_root("/opt/shush/bin/shush")
        require(
            binary[:5] == b"\x7fELF\x02" and binary[5] == 1,
            "App must be 64-bit little-endian ELF",
        )
        require(struct.unpack_from("<H", binary, 18)[0] == 183, "App is not AArch64")
        require(
            binary == (target / "opt/shush/bin/shush").read_bytes(),
            "Packaged binary differs",
        )
        fstab = read_root("/etc/fstab").decode()
        require(
            "/ ext4 ro,noload,noatime" in fstab, "System filesystem is not read-only"
        )
        require("/boot vfat ro," in fstab, "Boot filesystem is not read-only")
        require("size=32M" in fstab, "Temporary storage is not bounded")
        unit = read_root("/etc/systemd/system/shush.service").decode()
        dropin = read_root(
            "/etc/systemd/system/shush.service.d/appliance.conf"
        ).decode()
        require("Restart=no" in unit, "Application must not auto-restart")
        overlay = PROJECT / "deploy/appliance/board/rootfs-overlay"
        for path in (
            "etc/systemd/network/20-wifi.network",
            "etc/systemd/system/shush-wifi.service",
        ):
            require(read_root("/" + path) == (overlay / path).read_bytes(),
                    "Packaged Wi-Fi configuration differs: " + path)
        for name in ("wpa_supplicant", "mosquitto_sub", "mosquitto_pub"):
            path = ("/usr/sbin/" if name == "wpa_supplicant" else "/usr/bin/") + name
            require(read_root(path)[:4] == b"\x7fELF", "Missing network executable: " + name)
        for name in (
            "brcm/brcmfmac43455-sdio.raspberrypi,3-model-b-plus.txt",
            "cypress/cyfmac43455-sdio.bin",
            "cypress/cyfmac43455-sdio.clm_blob",
        ):
            require(bool(read_root("/lib/firmware/" + name)), "Missing Pi Wi-Fi firmware")
        for name in ("wpa_supplicant.conf", "mqtt/mosquitto_sub", "mqtt/mosquitto_pub", "mqtt/topic"):
            path = "/etc/shush-private/" + name
            require(bool(read_root(path)), "Missing private network configuration")
            stat = subprocess.run(
                [str(host / "sbin/debugfs"), "-R", "stat " + path, str(rootfs)],
                capture_output=True, text=True, check=True,
            ).stdout
            require("Mode:  0600" in stat, "Private network file permissions are not 0600")
        mqtt_unit = read_root("/etc/systemd/system/shush.service.d/mqtt.conf").decode()
        require("LoadCredential=mqtt:/etc/shush-private/mqtt" in mqtt_unit,
                "MQTT credentials are not provided through systemd")
        require(
            "RequiresMountsFor=/boot /var/lib/shush" in dropin,
            "Persistent settings mount not required",
        )
        require(
            "Requires=systemd-time-wait-sync.service" in dropin,
            "Clock synchronization not required",
        )
        require(
            "EnvironmentFile=/boot/shush.conf" in dropin, "Missing boot configuration"
        )
        require(
            "ExecStart=\nExecStart=/usr/libexec/shush-start" in dropin,
            "Automatic USB selection is not the service entry point",
        )
        helper = (
            PROJECT / "deploy/appliance/board/rootfs-overlay/usr/libexec/shush-start"
        )
        require(
            read_root("/usr/libexec/shush-start") == helper.read_bytes(),
            "Packaged USB discovery helper differs from source",
        )
        require(
            "--uploads" not in helper.read_text(), "FTP playback integration remains"
        )
        require(
            "shushftp:" not in read_root("/etc/passwd").decode(), "FTP account remains"
        )
        require(
            "shushmedia:" not in read_root("/etc/group").decode(),
            "Upload group remains",
        )
        require("shushmedia" not in dropin, "Playback still depends on upload storage")
        for link in (
            "multi-user.target.wants/shush-ftp.service",
            "local-fs.target.wants/var-lib-shushmedia.mount",
        ):
            stat = subprocess.run(
                [
                    str(host / "sbin/debugfs"),
                    "-R",
                    "stat /etc/systemd/system/" + link,
                    str(rootfs),
                ],
                capture_output=True,
                text=True,
            )
            require(
                "Type: symlink" not in stat.stdout, "FTP unit remains enabled: " + link
            )
        mount = read_root("/etc/systemd/system/var-lib-shush.mount").decode()
        require(
            "What=/dev/mmcblk0p3" in mount and "Where=/var/lib/shush" in mount,
            "Wrong persistent settings partition",
        )
        require(
            "Type=ext4" in mount and "Options=rw,nodev,nosuid,noexec,noatime" in mount,
            "Wrong settings filesystem/mount flags",
        )
        var_mount = read_root("/usr/lib/systemd/system/var.mount").decode()
        require(
            "Where=/var" in var_mount and "Type=tmpfs" in var_mount,
            "Missing OS RAM state mount",
        )
        var = read_root("/etc/systemd/system/var.mount.d/shush.conf").decode()
        require("size=64M" in var, "OS RAM state is not bounded")
        passwd = read_root("/etc/passwd").decode()
        require("shush:x:1000:1000:" in passwd, "Wrong fixed service account")
        journal = read_root("/etc/systemd/journald.conf.d/shush.conf").decode()
        require(
            "Storage=volatile" in journal and "RuntimeMaxUse=16M" in journal,
            "Unbounded/persistent logs",
        )
        require(
            read_root("/opt/shush/reference/reading_schedule.json")
            == (PROJECT / "reference/reading_schedule.json").read_bytes(),
            "Wrong schedule",
        )
        # Read actual image assets, not just the staging directory.
        voice_manifest = json.loads((PROJECT / "voice/manifest.json").read_text())
        for entry in voice_manifest["assets"]:
            data = read_root("/opt/shush/voice/" + entry["file"])
            require(
                hashlib.sha256(data).hexdigest() == entry["sha256"],
                "Voice mismatch: " + entry["file"],
            )
        require(len(voice_manifest["assets"]) == 771, "Incomplete prompt bank")
        for source in sorted((PROJECT / "Audio").glob("*.mp3")):
            data = read_root("/opt/shush/Audio/" + source.name)
            require(
                hashlib.sha256(data).hexdigest() == digest(source),
                "Reading audio mismatch: " + source.name,
            )
        require(
            hashlib.sha256(read_root("/opt/shush/Pink_Noise.wav")).hexdigest()
            == digest(PROJECT / "Pink_Noise.wav"),
            "Pink noise mismatch",
        )
    for filename in (
        "config.txt",
        "cmdline.txt",
        "shush.conf",
        "overlays/disable-bt.dtbo",
        "Image",
    ):
        actual = subprocess.check_output(
            [str(host / "bin/mtype"), "-i", str(images / "boot.vfat"), "::" + filename]
        )
        source = images / (
            filename
            if filename in ("shush.conf", "Image")
            else "rpi-firmware/" + filename
        )
        require(actual == source.read_bytes(), "Boot file mismatch: " + filename)
    # QEMU user mode executes the actual target binary against its target libraries.
    # It proves cross-build/asset loading, not Pi boot, USB audio, or endurance.
    with tempfile.TemporaryDirectory(prefix="shush-emulated-settings-") as folder:
        command = [
            str(host / "bin/qemu-aarch64"),
            "-L",
            str(target),
            str(target / "opt/shush/bin/shush"),
        ]
        common = [
            "--root",
            str(target / "opt/shush"),
            "--settings",
            str(Path(folder) / "settings.json"),
        ]
        subprocess.run(command + ["check"] + common, check=True)
        media_records = (
            [] if args.structure_only else validate_arm_media(command, target, images)
        )
    manifest["image_structure_verified"] = True
    manifest["packaged_voice_prompts_verified"] = 771
    manifest["aarch64_asset_check"] = "passed (QEMU user mode)"
    manifest["aarch64_full_media_validation"] = (
        "not run (structure-only verification)"
        if args.structure_only
        else "passed (QEMU user mode; 3 complete-playlist partitions)"
    )
    manifest["aarch64_media_parts"] = media_records
    manifest["aarch64_playlists_validated"] = 0 if args.structure_only else 366
    manifest["verification_script_sha256"] = digest(Path(__file__).resolve())
    manifest["hardware_boot_verified"] = False
    (images / "shush-image.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(
        "Verified image partitions, packaged configuration/prompts, and AArch64 media loading."
    )


if __name__ == "__main__":
    main()
