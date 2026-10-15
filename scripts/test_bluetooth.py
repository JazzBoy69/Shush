"""Idle-only Bluetooth policy and the real ALSA-to-FIFO stereo contract."""
import importlib.util
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import threading
import unittest
import wave

PROJECT = Path(__file__).resolve().parents[1]
HELPER = PROJECT / "deploy/appliance/board/rootfs-overlay/usr/libexec/shush-bluetooth.py"
spec = importlib.util.spec_from_file_location("shush_bluetooth", HELPER)
bluetooth = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bluetooth)


class BluetoothTests(unittest.TestCase):
    def test_pairing_requires_live_idle_heartbeat_and_explicit_blue_button(self):
        policy = bluetooth.Policy()
        policy.apply(b"pair", 10)
        self.assertFalse(policy.pairing_allowed(10))
        policy.apply(b"idle", 11)
        self.assertFalse(policy.audio_allowed(11))
        policy.apply(b"pair", 11)
        self.assertTrue(policy.pairing_allowed(11))
        policy.apply(b"busy", 12)
        self.assertFalse(policy.pairing_allowed(12))
        self.assertFalse(policy.audio_allowed(12))
        policy.apply(b"pair", 12)
        self.assertFalse(policy.audio_allowed(12))
        policy.apply(b"idle", 13)
        self.assertFalse(policy.audio_allowed(13))

    def test_pairing_window_expires_without_stopping_paired_idle_audio(self):
        policy = bluetooth.Policy()
        policy.apply(b"idle", 1)
        policy.apply(b"pair", 1)
        for second in range(2, 122):
            policy.apply(b"idle", second)
        self.assertFalse(policy.pairing_allowed(121))
        self.assertTrue(policy.audio_allowed(121))
        policy.apply(b"pair", 122)
        self.assertTrue(policy.pairing_allowed(122))
        policy.apply(b"stop", 122)
        self.assertFalse(policy.audio_allowed(122))

    def test_controller_failure_disables_radio_even_during_pairing(self):
        policy = bluetooth.Policy()
        policy.apply(b"idle", 1)
        policy.apply(b"pair", 1)
        self.assertFalse(policy.audio_allowed(4))
        self.assertFalse(policy.pairing_allowed(4))
        policy.apply(b"idle", 5)
        self.assertFalse(policy.audio_allowed(5))

    @unittest.skipUnless(shutil.which("aplay") and hasattr(os, "mkfifo"), "requires Linux ALSA utilities")
    def test_alsa_ingress_preserves_signed_little_endian_stereo_frames(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            fifo = root / "audio.fifo"
            os.mkfifo(fifo)
            config = root / "asound.conf"
            source = PROJECT / "deploy/appliance/board/rootfs-overlay/etc/shush-bluetooth-asound.conf"
            config.write_text(source.read_text().replace("/run/shush-bluetooth/audio.fifo", str(fifo)))
            recording = root / "fixture.wav"
            pcm = struct.pack("<hh", 1000, -2000) * 4410
            with wave.open(str(recording), "wb") as output:
                output.setnchannels(2)
                output.setsampwidth(2)
                output.setframerate(44100)
                output.writeframes(pcm)
            captured = bytearray()

            def read():
                with fifo.open("rb") as stream:
                    captured.extend(stream.read())

            reader = threading.Thread(target=read, daemon=True)
            reader.start()
            env = os.environ.copy()
            env["ALSA_CONFIG_PATH"] = str(config)
            result = subprocess.run(["aplay", "-q", "-D", "shush_bluetooth", str(recording)],
                                    env=env, capture_output=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr.decode())
            reader.join(timeout=5)
            self.assertFalse(reader.is_alive())
            self.assertGreaterEqual(len(captured), len(pcm))
            self.assertEqual(captured[:len(pcm)], pcm)


if __name__ == "__main__":
    unittest.main()
