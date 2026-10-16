"""Failed candidates must not destroy the published appliance image."""
import json
from pathlib import Path
import tempfile
import unittest

from build_appliance import digest, publish_candidate


class ImagePublicationTests(unittest.TestCase):
    def test_rejects_unverified_and_changed_candidates_then_preserves_previous(self):
        with tempfile.TemporaryDirectory() as directory:
            images = Path(directory)
            old = {"sdcard.img": b"old image", "sdcard.img.sha256": b"old checksum",
                   "shush-image.json": b"old manifest"}
            for name, data in old.items():
                (images / name).write_bytes(data)
            candidate = images / "sdcard-candidate.img"
            candidate.write_bytes(b"new image")
            manifest = {"image_sha256": digest(candidate)}
            record = images / "shush-candidate.json"
            record.write_text(json.dumps(manifest))
            with self.assertRaisesRegex(RuntimeError, "full verification"):
                publish_candidate(images)
            manifest.update(image_structure_verified=True, aarch64_playlists_validated=366)
            record.write_text(json.dumps(manifest))
            candidate.write_bytes(b"changed image")
            with self.assertRaisesRegex(RuntimeError, "changed"):
                publish_candidate(images)
            for name, data in old.items():
                self.assertEqual((images / name).read_bytes(), data)
            candidate.write_bytes(b"new image")
            publish_candidate(images)
            self.assertEqual((images / "sdcard.img").read_bytes(), b"new image")
            backups = list(images.glob("previous-image-*"))
            self.assertEqual(len(backups), 1)
            for name, data in old.items():
                self.assertEqual((backups[0] / name).read_bytes(), data)
            self.assertEqual(json.loads((images / "shush-image.json").read_text())["image"], "sdcard.img")
            self.assertEqual((images / "sdcard.img.sha256").read_text(), manifest["image_sha256"] + "  sdcard.img\n")


if __name__ == "__main__":
    unittest.main()
