"""Private network generation and image-installation regression checks."""
import importlib.util
import os
from pathlib import Path
import tempfile
import tarfile
import io
import unittest
from unittest.mock import patch

from configure_network import mqtt_config, topic_config, wifi_config, private_file
from build_appliance import extract_buildroot

PROJECT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("post_build", PROJECT / "deploy/appliance/board/post-build.py")
post_build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(post_build)


class NetworkTests(unittest.TestCase):
    def test_buildroot_absolute_rootfs_symlinks_are_created_after_safe_extraction(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            archive = root / "test.tar"
            with tarfile.open(archive, "w") as bundle:
                link = tarfile.TarInfo("buildroot/skeleton/link")
                link.type = tarfile.SYMTYPE
                link.linkname = "/etc/init.d/rcS"
                bundle.addfile(link)
                file = tarfile.TarInfo("buildroot/Makefile")
                file.size = 4
                bundle.addfile(file, io.BytesIO(b"test"))
            # Parent directories normally have explicit entries in the real archive.
            (root / "out/buildroot/skeleton").mkdir(parents=True)
            extract_buildroot(archive, root / "out")
            self.assertEqual(os.readlink(root / "out/buildroot/skeleton/link"), "/etc/init.d/rcS")
            self.assertEqual((root / "out/buildroot/Makefile").read_bytes(), b"test")
            with tarfile.open(archive, "w") as bundle:
                file = tarfile.TarInfo("../escape")
                bundle.addfile(file)
            with self.assertRaises(tarfile.TarError):
                extract_buildroot(archive, root / "out")

    def test_wifi_psk_matches_known_wpa_vector_and_ssid_cannot_inject_settings(self):
        result = wifi_config("IEEE", "password", "US")
        self.assertIn("f42c6fc52df0ebef9ebb4b90b38a5f902e83fe1b135a70e23aed762e9710a12e", result)
        self.assertIn("ssid=49454545", result)
        self.assertNotIn("password", result)
        result = wifi_config('name"\nnetwork={', "password", "US")
        self.assertEqual(result.count("network={"), 1)
        for ssid, password, country in [("", "password", "US"), ("a" * 33, "password", "US"),
                                        ("wifi", "short", "US"), ("wifi", "password", "U\n")]:
            with self.assertRaises(ValueError):
                wifi_config(ssid, password, country)

    def test_mqtt_rejects_config_injection_and_topic_wildcards(self):
        for value in ["password\n-t other", "password\r-u other", "nul\x00value"]:
            with self.assertRaises(ValueError):
                mqtt_config("localhost", 1883, "user", value)
        for value in ["", "shush/#", "shush/+", "shush/", "shush\n"]:
            with self.assertRaises(ValueError):
                topic_config(value)

    def test_secrets_are_private_and_missing_source_preserves_installed_config(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "target"
            source = Path(folder) / "private"
            names = ["wpa_supplicant.conf", "mqtt/mosquitto_sub", "mqtt/mosquitto_pub", "mqtt/topic"]
            for name in names:
                path = source / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("test-secret\n")
            post_build.install_private_network(root, source)
            for name in names:
                installed = root / "etc/shush-private" / name
                self.assertEqual(installed.read_text(), "test-secret\n")
                if os.name != "nt":
                    self.assertEqual(installed.stat().st_mode & 0o777, 0o600)
            self.assertFalse((root / "boot").exists())
            unit = root / "etc/systemd/system/shush.service.d/mqtt.conf"
            for name in ("mosquitto_sub", "mosquitto_pub", "topic"):
                self.assertIn(f"LoadCredential={name}:/etc/shush-private/mqtt/{name}\n", unit.read_text())
            post_build.install_private_network(root, None)
            self.assertTrue(unit.exists())
            self.assertEqual((root / "etc/shush-private/wpa_supplicant.conf").read_text(), "test-secret\n")
            with self.assertRaises(SystemExit):
                post_build.install_private_network(root, Path(folder) / "missing")
            self.assertTrue(unit.exists())
            self.assertEqual((root / "etc/shush-private/wpa_supplicant.conf").read_text(), "test-secret\n")

    def test_incomplete_private_config_fails_before_installing_any_secrets(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "target"
            with self.assertRaises(SystemExit):
                post_build.install_private_network(root, Path(folder) / "missing")
            self.assertFalse((root / "etc/shush-private").exists())

    def test_atomic_secret_write_preserves_original_on_failure(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "secret"
            private_file(path, "original")
            with patch("configure_network.os.replace", side_effect=OSError("injected")):
                with self.assertRaises(OSError):
                    private_file(path, "replacement")
            self.assertEqual(path.read_text(), "original")
            self.assertEqual(list(Path(folder).iterdir()), [path])
            private_file(path, "replacement")
            self.assertEqual(path.read_text(), "replacement")
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_install_copy_failure_preserves_existing_configuration(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "target"
            source = Path(folder) / "source"
            for name in ("wpa_supplicant.conf", "mqtt/mosquitto_sub", "mqtt/mosquitto_pub", "mqtt/topic"):
                p = source / name
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text("original")
            post_build.install_private_network(root, source)
            unit = root / "etc/systemd/system/shush.service.d/mqtt.conf"
            old_unit = unit.read_bytes()
            with patch.object(post_build.shutil, "copyfile", side_effect=OSError("injected")):
                with self.assertRaises(OSError):
                    post_build.install_private_network(root, source)
            self.assertEqual((root / "etc/shush-private/wpa_supplicant.conf").read_text(), "original")
            self.assertEqual(unit.read_bytes(), old_unit)
            with patch.object(post_build.os, "replace", side_effect=OSError("unit install failure")):
                with self.assertRaises(OSError):
                    post_build.install_private_network(root, source)
            self.assertEqual((root / "etc/shush-private/wpa_supplicant.conf").read_text(), "original")
            self.assertEqual(unit.read_bytes(), old_unit)
            (source / "wpa_supplicant.conf").write_text("replacement")
            post_build.install_private_network(root, source)
            self.assertEqual((root / "etc/shush-private/wpa_supplicant.conf").read_text(), "replacement")
            backups = list(Path(folder).glob(".shush-private-backup-*/private/wpa_supplicant.conf"))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_text(), "original")


if __name__ == "__main__":
    unittest.main()
