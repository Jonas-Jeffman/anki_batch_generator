from __future__ import annotations

from dataclasses import replace
import json
import sqlite3
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from tests import support  # noqa: F401 - installs offline dependency stubs

from anki_generator.cards.english import resolve_canonical_content
from anki_generator.cards.renderers import build_canonical_en_word_card
from anki_generator.dictionary.longman import parse_longman_provider_entries
from anki_generator.export import anki as exporter
from anki_generator.senses.store import canonical_guid_seed
from anki_generator.cards.preview import SENSE_PREVIEW_SCHEMA_VERSION, write_preview_json
from anki_generator.models import (
    AudioAsset,
    BuiltCard,
    CanonicalSense,
    CanonicalSenseRequest,
    InputItem,
    ResolvedCanonicalContent,
    ResolvedContentField,
)
from anki_generator.utils import stable_anki_id, stable_guid


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


class _SemanticPackage:
    def __init__(self, deck):
        self.deck = deck
        self.media_files = []

    def write_to_file(self, output):
        output_path = Path(output)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            collection = root / "collection.anki2"
            database = sqlite3.connect(collection)
            database.execute(
                "CREATE TABLE export_meta (deck_id INTEGER, deck_name TEXT, "
                "model_id INTEGER, model_name TEXT, templates TEXT, css TEXT)"
            )
            model = self.deck.notes[0].kwargs["model"]
            database.execute(
                "INSERT INTO export_meta VALUES (?, ?, ?, ?, ?, ?)",
                (
                    self.deck.deck_id,
                    self.deck.name,
                    model.kwargs["model_id"],
                    model.kwargs["name"],
                    json.dumps(model.kwargs["templates"]),
                    model.kwargs["css"],
                ),
            )
            database.execute(
                "CREATE TABLE notes (guid TEXT, front TEXT, back TEXT, tags TEXT)"
            )
            database.executemany(
                "INSERT INTO notes VALUES (?, ?, ?, ?)",
                [
                    (
                        note.kwargs["guid"],
                        note.kwargs["fields"][0],
                        note.kwargs["fields"][1],
                        json.dumps(note.kwargs["tags"]),
                    )
                    for note in self.deck.notes
                ],
            )
            database.commit()
            database.close()

            media = {
                str(index): Path(path).name
                for index, path in enumerate(self.media_files)
            }
            with zipfile.ZipFile(output_path, "w") as archive:
                archive.write(collection, "collection.anki2")
                archive.writestr("media", json.dumps(media))
                for index, path in enumerate(self.media_files):
                    archive.write(path, str(index))


class _SemanticGenanki:
    Model = _Model
    Deck = _Deck
    Note = _Note
    Package = _SemanticPackage


def resolved(index: int) -> ResolvedCanonicalContent:
    return ResolvedCanonicalContent(
        word="trunk",
        pos="noun",
        index=index,
        canonical_key=f"canonical::trunk-{index}",
        status="active",
        definition=ResolvedContentField(
            f"definition {index}", "cambridge", f"C{index}"
        ),
        example=ResolvedContentField(
            f"example {index}", "longman", f"L{index}"
        ),
        example_audio=ResolvedContentField(
            f"https://www.ldoceonline.com/media/english/exaProns/{index}.mp3",
            "longman",
            f"L{index}",
        ),
        ipa=ResolvedContentField("trʌŋk", "oxford"),
        word_audio_urls=[
            ResolvedContentField(
                "https://www.oxfordlearnersdictionaries.com/trunk.mp3",
                "oxford",
                selection_scope="exact_pos",
            )
        ],
        image=ResolvedContentField(
            f"https://dictionary.cambridge.org/images/full/trunk-{index}.jpg",
            "cambridge",
            f"C{index}",
        ),
        image_alt="trunk",
    )


def card(content: ResolvedCanonicalContent) -> BuiltCard:
    return BuiltCard(
        front=f"<b>trunk</b> noun {content.index}",
        back=f"definition {content.index}",
        tags=["english", "vocab"],
        guid_seed=canonical_guid_seed(content.canonical_key),
    )


class SensePreviewExportTests(unittest.TestCase):
    def test_territory_html_to_cards_and_json_preview_offline(self):
        page = (
            Path(__file__).resolve().parents[1]
            / "docs" / "dictionary" / "territory" / "territory_longman.md"
        )
        entries = parse_longman_provider_entries(
            page.read_text(encoding="utf-8"),
            "https://www.ldoceonline.com/dictionary/territory",
            "territory",
        )
        cards = []
        contents = []
        for native_id, index, signpost in (
            ("territory__1", 4, "government/military"),
            ("territory__2", 8, "type of land"),
        ):
            # Deterministic fixture identities, not a claim about online alignment.
            request = CanonicalSenseRequest(
                item=InputItem("en_word", "territory"),
                word="territory", pos="noun",
                canonical_sense=CanonicalSense(
                    word="territory", pos="noun", index=index,
                    backbone_source="longman", backbone_sense_id=native_id,
                    longman_sense_ids=[native_id],
                    canonical_key=f"canonical::fixture-{native_id}",
                ),
                provider_entries=entries,
            )
            content = resolve_canonical_content(request)
            card = build_canonical_en_word_card(
                content,
                [AudioAsset("word.mp3", Path("word.mp3"))],
                AudioAsset("example.mp3", Path("example.mp3")),
                None,
            )
            self.assertEqual(signpost, content.definition_signpost)
            self.assertEqual(native_id, content.definition.sense_id)
            self.assertEqual(native_id, content.example.sense_id)
            self.assertIn(
                f'>{signpost}</span> {content.definition.value}<br>', card.back
            )
            self.assertIn(content.example.value, card.back)
            self.assertIn('preload="none" src="example.mp3"', card.back)
            self.assertNotIn("occupied/enemy/disputed/hostile territory", card.back)
            self.assertNotIn("uncharted/unexplored territory", card.back)
            self.assertNotIn("countable", card.back)
            self.assertNotIn("<b>Image:</b>", card.back)
            baseline = build_canonical_en_word_card(
                replace(content, definition_signpost="", definition_thesaurus_terms=[]),
                [], None, None,
            )
            self.assertEqual(baseline.front, card.front)
            self.assertEqual(baseline.guid_seed, card.guid_seed)
            self.assertEqual(
                canonical_guid_seed(content.canonical_key), card.guid_seed
            )
            if native_id == "territory__1":
                self.assertEqual(["land"], content.definition_thesaurus_terms)
                self.assertIn(
                    "land that is owned or controlled by a particular country, "
                    "ruler, or military force",
                    card.back,
                )
                self.assertIn(
                    "The plane was flying over enemy territory.", card.back
                )
                self.assertTrue(card.back.endswith(
                    '</audio><br><b>Thesaurus:</b> land'
                ))
                self.assertEqual(1, card.back.count("Thesaurus:"))
            else:
                self.assertNotIn("Thesaurus:", card.back)
                self.assertNotIn("government/military", card.back)
            cards.append(card)
            contents.append(content)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "preview.json"
            write_preview_json(path, cards, contents)
            payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(SENSE_PREVIEW_SCHEMA_VERSION, payload["schema_version"])
        self.assertEqual(2, payload["card_count"])
        for row, card in zip(payload["cards"], cards):
            self.assertEqual(card.front, row["front"])
            self.assertEqual(card.back, row["back"])
            self.assertEqual(card.guid_seed, row["guid_seed"])
            self.assertEqual("longman", row["content"]["definition"]["source"])

    def test_preview_contains_all_active_senses_and_provenance(self):
        contents = [resolved(index) for index in range(1, 9)]
        cards = [card(content) for content in contents]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "preview.json"
            write_preview_json(path, cards, contents)
            payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(SENSE_PREVIEW_SCHEMA_VERSION, payload["schema_version"])
        self.assertEqual(8, payload["card_count"])
        self.assertEqual(list(range(1, 9)), [
            row["sense"]["index"] for row in payload["cards"]
        ])
        second = payload["cards"][1]
        self.assertEqual("canonical::trunk-2", second["sense"]["canonical_key"])
        self.assertEqual("active", second["sense"]["status"])
        self.assertEqual(
            {"value": "definition 2", "source": "cambridge", "sense_id": "C2"},
            second["content"]["definition"],
        )
        self.assertEqual("longman", second["content"]["example_audio"]["source"])
        self.assertEqual("oxford", second["content"]["word_audio_urls"][0]["source"])
        self.assertEqual(
            "exact_pos",
            second["content"]["word_audio_urls"][0]["selection_scope"],
        )

    def test_explicit_index_preview_contains_one_card(self):
        content = resolved(2)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "preview.json"
            write_preview_json(path, [card(content)], [content])
            payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(1, payload["card_count"])
        self.assertEqual(2, payload["cards"][0]["sense"]["index"])

    def test_unpacked_apkg_preserves_ids_notes_guids_fields_and_media_order(self):
        contents = [resolved(index) for index in range(1, 9)]
        cards = [card(content) for content in contents]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            media = [root / "word.mp3", root / "example.mp3", root / "image.jpg"]
            for index, path in enumerate(media):
                path.write_bytes(f"media-{index}".encode())
            output = root / "nested" / "trunk.apkg"
            with patch.object(exporter, "genanki", _SemanticGenanki):
                exporter.create_deck_apkg(
                    "English::Canonical",
                    output,
                    cards,
                    media,
                )

            with zipfile.ZipFile(output) as archive:
                self.assertEqual(
                    ["collection.anki2", "media", "0", "1", "2"],
                    archive.namelist(),
                )
                media_manifest = json.loads(archive.read("media"))
                self.assertEqual(
                    {"0": "word.mp3", "1": "example.mp3", "2": "image.jpg"},
                    media_manifest,
                )
                collection = root / "unpacked.anki2"
                collection.write_bytes(archive.read("collection.anki2"))

            database = sqlite3.connect(collection)
            metadata = database.execute("SELECT * FROM export_meta").fetchone()
            notes = database.execute(
                "SELECT guid, front, back, tags FROM notes ORDER BY rowid"
            ).fetchall()
            database.close()

        self.assertEqual(stable_anki_id("deck::English::Canonical"), metadata[0])
        self.assertEqual("English::Canonical", metadata[1])
        self.assertEqual(
            stable_anki_id("model::anki_batch_generator::basic_v2"), metadata[2]
        )
        self.assertEqual("BatchAIGeneratedBasicModelV2", metadata[3])
        self.assertEqual(
            [{
                "name": "Card 1",
                "qfmt": "{{Front}}",
                "afmt": '{{FrontSide}}<hr id="answer">{{Back}}',
            }],
            json.loads(metadata[4]),
        )
        self.assertIn("font-family: Arial, sans-serif", metadata[5])
        self.assertEqual(8, len(notes))
        self.assertEqual(
            [stable_guid("English::Canonical", item.guid_seed) for item in cards],
            [row[0] for row in notes],
        )
        self.assertEqual(
            [(item.front, item.back) for item in cards],
            [(row[1], row[2]) for row in notes],
        )


if __name__ == "__main__":
    unittest.main()
