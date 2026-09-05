from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.support import GOLDEN_ROOT, load_json

import anki_batch_generator
from anki import exporter
from common import BuiltCard, stable_anki_id, stable_guid


class _Model:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


class _Deck:
    def __init__(self, deck_id, name):
        self.deck_id = deck_id
        self.name = name
        self.notes = []

    def add_note(self, note):
        self.notes.append(note)


class _Note:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


class _Package:
    instances = []

    def __init__(self, deck):
        self.deck = deck
        self.media_files = []
        self.output = None
        self.instances.append(self)

    def write_to_file(self, output):
        self.output = output
        Path(output).touch()


class _Genanki:
    Model = _Model
    Deck = _Deck
    Note = _Note
    Package = _Package


class CliAndExporterCharacterizationTests(unittest.TestCase):
    maxDiff = None

    def test_entry_reexports_exporter_function(self):
        self.assertIs(anki_batch_generator.create_deck_apkg, exporter.create_deck_apkg)

    def test_missing_genanki_preserves_error(self):
        with patch.object(exporter, "genanki", None):
            with self.assertRaisesRegex(
                RuntimeError,
                "genanki is not installed; install project requirements to generate .apkg files",
            ):
                exporter.create_deck_apkg("Deck", Path("unused.apkg"), [], [])

    def test_cli_defaults_and_explicit_values(self):
        expected = load_json(GOLDEN_ROOT / "cli.json")
        cases = {
            "defaults": ["anki_batch_generator.py"],
            "explicit": [
                "anki_batch_generator.py", "--mode", "en_word", "--deck-name", "English::Daily",
                "--terms-json", '["rose"]', "--hint", "flower", "--tags", "one", "two words",
                "--output", "deck.apkg", "--preview-json", "preview.json", "--cache-path", "cache.json",
                "--media-dir", "media", "--model", "fixture-model", "--tts-model", "fixture-tts",
                "--tts-voice-en", "echo", "--tts-voice-ja", "alloy", "--reasoning-effort", "low",
                "--openai-api-key", "fixture-key", "--openai-base-url", "https://example.test/v1",
                "--sleep", "0.5", "--dict-test-only",
            ],
        }
        actual = {}
        for name, argv in cases.items():
            with patch("sys.argv", argv):
                actual[name] = vars(anki_batch_generator.parse_args())
        self.assertEqual(expected, actual)

    def test_exporter_semantics(self):
        expected = load_json(GOLDEN_ROOT / "exporter.json")
        card = BuiltCard(
            front="<b>rose</b>",
            back="<b>Definition (EN):</b> a flower",
            tags=["english", "vocab"],
            guid_seed="en_word::rose",
        )
        with tempfile.TemporaryDirectory() as tmp:
            _Package.instances.clear()
            output = Path(tmp) / "deck.apkg"
            media = [Path(tmp) / "audio.mp3", Path(tmp) / "image.jpg"]
            with patch.object(exporter, "genanki", _Genanki):
                anki_batch_generator.create_deck_apkg("Regression::Deck", output, [card], media)
            package = _Package.instances[-1]
            model = package.deck.notes[0].kwargs["model"]
            actual = {
                "deck_id": package.deck.deck_id,
                "expected_deck_id": stable_anki_id("deck::Regression::Deck"),
                "deck_name": package.deck.name,
                "model": model.kwargs,
                "notes": [
                    {
                        "fields": note.kwargs["fields"],
                        "tags": note.kwargs["tags"],
                        "guid": note.kwargs["guid"],
                    }
                    for note in package.deck.notes
                ],
                "expected_guid": stable_guid("Regression::Deck", "en_word::rose"),
                "media_basenames": [Path(path).name for path in package.media_files],
                "output_basename": Path(package.output).name,
            }
        self.assertEqual(expected, actual)


if __name__ == "__main__":
    unittest.main()
