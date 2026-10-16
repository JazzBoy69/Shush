"""Reject corrupt image layouts and incomplete read-only filesystem assembly."""

import os
import json
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest

from verify_appliance import EARLY_FIRMWARE, media_schedules, partitions, validate_fstab, validate_early_firmware

PROJECT = Path(__file__).resolve().parents[1]


class ApplianceTests(unittest.TestCase):
    def test_early_firmware_must_be_in_kernel_not_only_in_root_filesystem(self):
        files = {name: ("fixture:" + name).encode() for name in EARLY_FIRMWARE}
        config = 'CONFIG_EXTRA_FIRMWARE="' + " ".join(EARLY_FIRMWARE) + '"\n'
        image = b"kernel" + b"\0".join(files.values())
        validate_early_firmware(config, image, files.__getitem__)
        for name in EARLY_FIRMWARE:
            with self.subTest(name=name), self.assertRaises(RuntimeError):
                validate_early_firmware(config.replace(name, ""), image, files.__getitem__)
            with self.subTest(missing_bytes=name), self.assertRaises(RuntimeError):
                validate_early_firmware(config, image.replace(files[name], b""), files.__getitem__)

    def test_fstab_requires_valid_mount_points_and_actual_storage_limits(self):
        text = (PROJECT / "deploy/appliance/board/rootfs-overlay/etc/fstab").read_text()
        validate_fstab(text)
        for broken in (
            text.replace("tmpfs /tmp tmpfs", "/tmp tmpfs tmpfs"),
            text.replace("size=32M", "size=50%"),
            text.replace("size=32M", "size=32M,size=50%"),
            text.replace("ro,noload,noatime", "rw,noload,noatime"),
            text.replace("tmpfs /tmp tmpfs", "/dev/mmcblk0p3 /tmp ext4"),
            text + "tmpfs /tmp tmpfs size=32M 0 0\n",
        ):
            with self.subTest(broken=broken), self.assertRaises(RuntimeError):
                validate_fstab(broken)

    def test_playback_has_no_ftp_or_upload_dependency(self):
        overlay = PROJECT / "deploy/appliance/board/rootfs-overlay"
        app = (
            overlay / "etc/systemd/system/shush.service.d/appliance.conf"
        ).read_text()
        self.assertIn("RequiresMountsFor=/boot /var/lib/shush\n", app)
        self.assertNotIn("shushmedia", app)
        self.assertNotIn("--uploads", (overlay / "usr/libexec/shush-start").read_text())
        self.assertFalse((overlay / "etc/systemd/system/shush-ftp.service").exists())
        self.assertFalse(
            (overlay / "etc/systemd/system/var-lib-shushmedia.mount").exists()
        )
        preset = (overlay / "etc/systemd/system-preset/00-shush.preset").read_text()
        self.assertNotIn("ftp", preset)
        self.assertNotIn("shushmedia", preset)

    def test_parallel_validation_covers_every_original_playlist_and_segment(self):
        schedule = json.loads((PROJECT / "reference/reading_schedule.json").read_text())
        before = json.dumps(schedule, sort_keys=True)
        parts = media_schedules(schedule)
        self.assertEqual([day for days, _ in parts for day in days], list(range(366)))
        originals = set()
        checked = set()
        for day in range(366):
            originals.add(json.dumps(schedule["days"][str(day)], sort_keys=True))
        for days, fixture in parts:
            self.assertEqual(set(fixture["days"]), {str(i) for i in range(366)})
            for index, day in enumerate(days):
                self.assertEqual(
                    fixture["days"][str(index)], schedule["days"][str(day)]
                )
            allowed = {
                json.dumps(schedule["days"][str(day)], sort_keys=True) for day in days
            }
            actual = {
                json.dumps(tracks, sort_keys=True)
                for tracks in fixture["days"].values()
            }
            self.assertEqual(actual, allowed)
            checked.update(actual)
        self.assertEqual(checked, originals)
        self.assertEqual(json.dumps(schedule, sort_keys=True), before)
        del schedule["days"]["365"]
        with self.assertRaises(RuntimeError):
            media_schedules(schedule)

    def make_disk(self, folder, entries, signature=True, size=4096):
        image = Path(folder) / "sdcard.img"
        data = bytearray(size)
        for index, (kind, start, sectors) in enumerate(entries):
            data[446 + index * 16 + 4] = kind
            struct.pack_into("<II", data, 446 + index * 16 + 8, start, sectors)
        if signature:
            data[510:512] = b"\x55\xaa"
        image.write_bytes(data)
        return image

    def test_partition_layout_requires_three_nonoverlapping_complete_partitions(self):
        valid = [(0xC, 1, 1), (0x83, 2, 2), (0x83, 4, 1)]
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(len(partitions(self.make_disk(folder, valid))), 3)
            invalid = [
                valid[:2],
                [valid[0], (0, 0, 0), valid[1], valid[2]],
                valid + [(0x83, 5, 1)],
                [(0xC, 1, 2), (0x83, 2, 2), (0x83, 4, 1)],
                [(0xC, 1, 1), (0x83, 2, 2), (0x83, 7, 2)],
                [(0x83, 1, 1), (0x83, 2, 2), (0x83, 4, 1)],
            ]
            for entries in invalid:
                with self.subTest(entries=entries), self.assertRaises(RuntimeError):
                    partitions(self.make_disk(folder, entries))
            with self.assertRaises(RuntimeError):
                partitions(self.make_disk(folder, valid, signature=False))

    def test_post_build_keeps_mutable_state_out_of_etc_and_disables_logins(self):
        script = PROJECT / "deploy/appliance/board/post-build.py"
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "etc").mkdir()
            (root / "etc/os-release").write_text("ID=buildroot\n")
            zone = root / "usr/share/zoneinfo/America/Chicago"
            zone.parent.mkdir(parents=True)
            zone.write_bytes(b"test-timezone")
            subprocess.run(["/usr/bin/python3", str(script), str(root)], check=True)
            self.assertEqual(
                os.readlink(root / "etc/resolv.conf"),
                "../run/systemd/resolve/stub-resolv.conf",
            )
            self.assertEqual(
                os.readlink(root / "etc/localtime"),
                "../usr/share/zoneinfo/America/Chicago",
            )
            self.assertEqual((root / "etc/machine-id").read_bytes(), b"")
            for name in (
                "getty@.service",
                "serial-getty@.service",
                "console-getty.service",
                "debug-shell.service",
            ):
                self.assertEqual(
                    os.readlink(root / "etc/systemd/system" / name), "/dev/null"
                )
            self.assertTrue((root / "boot").is_dir())
            self.assertTrue((root / "var/lib/shush").is_dir())

    def test_post_build_rejects_incomplete_target(self):
        with tempfile.TemporaryDirectory() as folder:
            result = subprocess.run(
                [
                    "/usr/bin/python3",
                    str(PROJECT / "deploy/appliance/board/post-build.py"),
                    folder,
                ],
                capture_output=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(b"Not a Buildroot target", result.stderr)

    def test_invalid_device_overrides_cannot_start(self):
        helper = (
            PROJECT / "deploy/appliance/board/rootfs-overlay/usr/libexec/shush-start"
        )
        for receiver, audio in [
            ("/dev/input/event0", "plughw:CARD=Device,DEV=0"),
            ("/dev/input/by-id/REPLACE-event-kbd", "plughw:CARD=Device,DEV=0"),
            ("/dev/input/by-id/receiver-event-kbd", "default"),
            ("/dev/input/by-id/receiver-event-kbd", ""),
        ]:
            with self.subTest(receiver=receiver, audio=audio):
                result = subprocess.run(
                    ["/bin/sh", str(helper)],
                    env={
                        "INPUT_DEVICE": receiver,
                        "AUDIO_DEVICE": audio,
                        "PATH": "/usr/bin:/bin",
                    },
                    capture_output=True,
                    timeout=2,
                )
                self.assertNotEqual(result.returncode, 0)

    def device_fixture(self, root, category, node, driver, physical):
        device = root / "sys/devices" / physical
        interface = device / "interface"
        driver_path = root / "sys/bus/usb/drivers" / driver
        driver_path.mkdir(parents=True, exist_ok=True)
        interface.mkdir(parents=True, exist_ok=True)
        (interface / "driver").symlink_to(driver_path)
        leaf = interface / "children" / node
        leaf.mkdir(parents=True)
        if category == "input":
            (leaf / "device").symlink_to(interface)
        class_path = root / "sys/class" / category
        class_path.mkdir(parents=True, exist_ok=True)
        (class_path / node).symlink_to(leaf)
        dev = root / "dev" / ("snd" if category == "sound" else category)
        dev.mkdir(parents=True, exist_ok=True)
        (dev / node).symlink_to("/dev/null")
        if category == "sound":
            index = node.removeprefix("pcmC").split("D")[0]
            control = dev / f"controlC{index}"
            if not control.exists():
                control.symlink_to("/dev/null")

    def probe(self, root, mode="--probe"):
        helper = (
            PROJECT / "deploy/appliance/board/rootfs-overlay/usr/libexec/shush-start"
        )
        # Exhaust the startup wait immediately in absent-device fixtures.
        tools = root / "tools"
        tools.mkdir(exist_ok=True)
        sleep = tools / "sleep"
        sleep.write_text("#!/bin/sh\nexit 0\n")
        sleep.chmod(0o755)
        return subprocess.run(
            ["/bin/sh", str(helper), mode],
            env={
                "INPUT_DEVICE": "auto",
                "AUDIO_DEVICE": "auto",
                "SHUSH_SYSFS": str(root / "sys"),
                "SHUSH_DEVFS": str(root / "dev"),
                "PATH": str(tools) + ":/usr/bin:/bin",
            },
            capture_output=True,
            text=True,
            timeout=5,
        )

    def test_discovery_uses_drivers_and_follows_boot_device_numbering(self):
        for event, card, pcm in [("event2", 0, 0), ("event17", 3, 1)]:
            with self.subTest(event=event), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                self.device_fixture(root, "input", "event0", "usbhid", "keyboard")
                self.device_fixture(root, "input", event, "mceusb", "receiver")
                self.device_fixture(root, "sound", "pcmC9D0p", "snd-other", "onboard")
                name = f"pcmC{card}D{pcm}p"
                self.device_fixture(root, "sound", name, "snd-usb-audio", "speakers")
                result = self.probe(root)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(
                    result.stdout.splitlines(),
                    [str(root / "dev/input" / event), f"plughw:CARD={card},DEV={pcm}"],
                )

    def test_clock_error_discovery_needs_speakers_but_not_receiver(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            self.device_fixture(root, "sound", "pcmC2D0p", "snd-usb-audio", "speakers")
            result = self.probe(root, "--probe-clock-error")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("plughw:CARD=2,DEV=0", result.stdout)
            self.assertNotEqual(self.probe(root).returncode, 0)

    def test_discovery_refuses_missing_or_ambiguous_hardware(self):
        for extra in (
            "input",
            "sound",
            "missing-input",
            "missing-sound",
            "missing-control",
        ):
            with self.subTest(extra=extra), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                if extra != "missing-input":
                    self.device_fixture(root, "input", "event2", "mceusb", "receiver")
                if extra != "missing-sound":
                    self.device_fixture(
                        root, "sound", "pcmC0D0p", "snd-usb-audio", "speakers"
                    )
                if extra == "input":
                    self.device_fixture(root, "input", "event3", "mceusb", "receiver2")
                if extra == "sound":
                    self.device_fixture(
                        root, "sound", "pcmC1D0p", "snd-usb-audio", "speakers2"
                    )
                if extra == "missing-control":
                    (root / "dev/snd/controlC0").unlink()
                result = self.probe(root)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(
                    "multiple" if extra in ("input", "sound") else "not ready",
                    result.stderr,
                )


if __name__ == "__main__":
    unittest.main()
