"""Regression checks against the audited appliance prompt corpus."""

import json
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from stress import postprocess_stress
from generate_neural_voice import generate, prompts, reuse_current, verify
from neural_pipeline import render_prompt


class StressPassTests(unittest.TestCase):
    def test_without_reading_has_short_aligned_hold(self):
        import wave
        import array

        root = Path(__file__).resolve().parents[1] / "voice-samples/stress-v7/modes"
        entries = json.loads((root / "manifest.json").read_text())["assets"]
        noise = next(e for e in entries if e["file"] == "mode-noise.wav")
        reading = next(e for e in entries if e["file"] == "mode-reading.wav")
        hold = noise["rhythmic_hold"]
        with wave.open(str(root / noise["file"]), "rb") as wav:
            self.assertEqual(wav.getframerate(), 24000)
            pcm = array.array("h", wav.readframes(wav.getnframes()))
        self.assertEqual(hold["protocol"], "single-utterance-aligned-rhythmic-hold")
        self.assertEqual(hold["hold_frames"], 960)
        self.assertEqual(hold["hold_milliseconds"], 40)
        self.assertEqual(len(pcm), hold["base_utterance_frames"] + 960)
        position = hold["hold_start_frame"]
        self.assertLessEqual(hold["boundary_start_frame"], position)
        self.assertLess(position, hold["boundary_end_frame"])
        self.assertTrue(all(value == 0 for value in pcm[position : position + 960]))
        self.assertEqual(
            hashlib.sha256((root / noise["file"]).read_bytes()).hexdigest(),
            noise["sha256"],
        )
        self.assertNotIn("segments", noise)
        self.assertNotIn("rhythmic_hold", reading)
        self.assertEqual(reading["corrected_phonemes"], "wˈɪð ɹˌiːdɪŋ.")

    def test_complete_corpus_preserves_sounds_and_limits_changes(self):
        corpus = json.loads(
            (
                Path(__file__).resolve().parents[1]
                / "voice-samples/stress-audit-raw.json"
            ).read_text()
        )["corpus"]
        self.assertEqual(len(corpus), 771)
        self.assertEqual(
            {filename: entry["text"] for filename, entry in corpus.items()}, prompts()
        )
        changed = set()
        stress_marks = str.maketrans("", "", "ˈˌ")
        for filename, entry in corpus.items():
            with self.subTest(filename=filename):
                original = entry["phonemes"]
                corrected, corrections = postprocess_stress(entry["text"], original)
                self.assertEqual(
                    original.translate(stress_marks), corrected.translate(stress_marks)
                )
                expected_change = entry["text"] != "Without reading."
                self.assertEqual(corrected != original, expected_change)
                self.assertEqual(bool(corrections), expected_change)
                if expected_change:
                    changed.add(filename)
                if filename.startswith(("current-", "wake-")):
                    self.assertEqual(corrected.count("ˈ"), 1, corrected)
                    self.assertGreaterEqual(corrected.count("ˌ"), 1, corrected)
        self.assertEqual(len(changed), 770)

    def test_phrase_focus_and_lexical_stress(self):
        corpus = json.loads(
            (
                Path(__file__).resolve().parents[1]
                / "voice-samples/stress-audit-raw.json"
            ).read_text()
        )["corpus"]
        expected = {
            "mode-reading.wav": "wˈɪð ɹˌiːdɪŋ.",
            "mode-noise.wav": "wɪðˌaʊt ɹˈiːdɪŋ.",
            "wake-6-00.wav": "jʊɹ ɐlˌɑːɹm ɪz sɛt fɔːɹ sˈɪks əklˌɑːk.",
            "wake-6-15.wav": "jʊɹ ɐlˌɑːɹm ɪz sɛt fɔːɹ sˌɪks fɪftˈiːn.",
            "wake-6-45.wav": "jʊɹ ɐlˌɑːɹm ɪz sɛt fɔːɹ sˌɪks fˌɔːɹɾi fˈaɪv.",
            "current-9-01.wav": "ðə kˌɜːɹənt tˌaɪm ɪz nˌaɪn oʊ wˈʌn.",
            "current-9-21.wav": "ðə kˌɜːɹənt tˌaɪm ɪz nˌaɪn twˌɛnti wˈʌn.",
            "current-11-45.wav": "ðə kˌɜːɹənt tˌaɪm ɪz ᵻlˌɛvən fˌɔːɹɾi fˈaɪv.",
            "error.wav": "plˌeɪbæk kˌænɑːt stˈɑːɹt. tʃˌɛk ɐplˌaɪəns dˌaɪəɡnˈɑːstɪks.",
        }
        for filename, desired in expected.items():
            with self.subTest(filename=filename):
                entry = corpus[filename]
                actual, _ = postprocess_stress(entry["text"], entry["phonemes"])
                self.assertEqual(actual, desired)

    def test_unexpected_target_pronunciation_is_rejected(self):
        with self.assertRaises(ValueError):
            postprocess_stress(
                "The current time is nine fifteen.",
                "ðə kˈɜːɹənt tˈaɪm ɪz nˈaɪn fɪftɪn.",
            )

    def test_incomplete_or_reordered_bank_cannot_verify(self):
        entries = [{"file": name, "text": text} for name, text in prompts().items()]
        for invalid in (entries[:-1], list(reversed(entries)), entries + entries[:1]):
            with self.subTest(
                size=len(invalid)
            ), tempfile.TemporaryDirectory() as folder:
                output = Path(folder)
                (output / "manifest.json").write_text(json.dumps({"assets": invalid}))
                with self.assertRaisesRegex(
                    RuntimeError, "complete canonical prompt corpus"
                ):
                    verify(Path(folder), output, False)

    def test_mode_wording_does_not_change_other_family_sessions(self):
        texts = {
            "current-1-00.wav": "current a",
            "current-1-01.wav": "current b",
            "error.wav": "error",
            "mode-noise.wav": "without",
            "mode-reading.wav": "with",
            "wake-1-00.wav": "wake a",
            "wake-1-15.wav": "wake b",
        }

        def render_fake(model, voice, text, path):
            # Stateful synthesis deliberately depends on earlier input lengths.
            state = model["state"]
            model["state"] += len(text)
            return {"file": path.name, "text": text, "state": state}

        with tempfile.TemporaryDirectory() as folder, patch(
            "generate_neural_voice.load_model", side_effect=lambda *_: {"state": 0}
        ), patch("generate_neural_voice.render", side_effect=render_fake), patch(
            "generate_neural_voice.environment", return_value={}
        ):
            with patch(
                "generate_neural_voice.prompts",
                return_value=dict(sorted(texts.items())),
            ):
                before = generate(
                    Path(folder), Path(folder) / "before", "am_michael", 2
                )
            texts["mode-noise.wav"] = "a much longer mode phrase"
            with patch(
                "generate_neural_voice.prompts",
                return_value=dict(sorted(texts.items())),
            ):
                after = generate(Path(folder), Path(folder) / "after", "am_michael", 2)
            self.assertEqual(
                [e for e in before["assets"] if not e["file"].startswith("mode-")],
                [e for e in after["assets"] if not e["file"].startswith("mode-")],
            )

    def test_reuse_rejects_proof_for_a_different_manifest(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "manifest.json").write_text("{}")
            (root / "reproduction-verification.json").write_text(
                json.dumps(
                    {
                        "all_manifests_and_wavs_identical": True,
                        "manifest_sha256": "incorrect",
                    }
                )
            )
            with self.assertRaisesRegex(
                RuntimeError, "matching complete reproduction proof"
            ):
                reuse_current(root, root, "am_michael", 2)

    def test_reuse_rejects_a_different_random_state_recipe(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            manifest = {
                "voice": "am_michael",
                "inference": {"input_order": "reversed filenames"},
                "assets": [],
            }
            data = json.dumps(manifest).encode()
            (root / "manifest.json").write_bytes(data)
            (root / "reproduction-verification.json").write_text(
                json.dumps(
                    {
                        "all_manifests_and_wavs_identical": True,
                        "manifest_sha256": hashlib.sha256(data).hexdigest(),
                    }
                )
            )
            with self.assertRaisesRegex(RuntimeError, "incompatible inference recipe"):
                reuse_current(root, root, "am_michael", 2)


if __name__ == "__main__":
    unittest.main()
