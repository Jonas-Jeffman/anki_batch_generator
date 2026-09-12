from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from tests import support  # noqa: F401 - installs offline dependency stubs

from cards.builder import resolve_canonical_content
from cards.renderers import build_canonical_en_word_card
from config import EXAMPLE_AUDIO_ICON_FILENAME, EXAMPLE_AUDIO_ICON_PATH
from media import audio, images
from models import (
    AudioAsset,
    CanonicalSense,
    CanonicalSenseRequest,
    DictionaryExample,
    InputItem,
    ProviderEntry,
    ProviderSense,
    ResolvedCanonicalContent,
    ResolvedContentField,
    WordPronunciation,
)


def provider_entry(
    source: str,
    native_id: str,
    definition: str,
    *,
    example: str = "",
    example_audio: str = "",
    image: str = "",
    ipa: str = "",
    word_audio: str = "",
    synonyms=None,
    thesaurus_terms=None,
) -> ProviderEntry:
    examples = (
        [DictionaryExample(text=example, audio_url=example_audio)]
        if example
        else []
    )
    return ProviderEntry(
        source=source,
        dataset="fixture",
        native_id=f"entry-{source}",
        word="trunk",
        pos="noun",
        pronunciation=WordPronunciation(
            source=source,
            pos="noun",
            ipa_uk=ipa,
            audio_uk_url=word_audio,
        ),
        senses=[
            ProviderSense(
                source=source,
                native_id=native_id,
                pos="noun",
                definition=definition,
                examples=examples,
                image_url=image,
                synonyms=list(synonyms or []),
                thesaurus_terms=list(thesaurus_terms or []),
            )
        ],
    )


def request(entries) -> CanonicalSenseRequest:
    return CanonicalSenseRequest(
        item=InputItem("en_word", "trunk noun 1"),
        word="trunk",
        pos="noun",
        canonical_sense=CanonicalSense(
            word="trunk",
            pos="noun",
            index=1,
            backbone_source="cambridge",
            backbone_sense_id="C1",
            cambridge_sense_id="C1",
            longman_sense_ids=["L1"],
            oxford_sense_ids=["O1"],
            canonical_key="canonical::trunk-1",
        ),
        provider_entries=entries,
    )


class SenseContentAndMediaTests(unittest.TestCase):
    def test_example_audio_icon_is_a_bundled_svg(self):
        self.assertEqual("audio_bre_initial.svg", EXAMPLE_AUDIO_ICON_FILENAME)
        self.assertTrue(EXAMPLE_AUDIO_ICON_PATH.is_file())

    def test_each_field_uses_its_own_source_and_records_provenance(self):
        longman_audio = (
            "https://www.ldoceonline.com/media/english/exaProns/example.mp3"
        )
        entries = [
            provider_entry(
                "cambridge",
                "C1",
                "Cambridge definition",
                example="Cambridge example.",
                image="https://dictionary.cambridge.org/images/full/trunk.jpg",
                ipa="cambridge-ipa",
                word_audio="https://dictionary.cambridge.org/cambridge.mp3",
            ),
            provider_entry(
                "longman",
                "L1",
                "Longman definition",
                example="Longman example.",
                example_audio=longman_audio,
                ipa="longman-ipa",
                word_audio="https://www.ldoceonline.com/longman.mp3",
            ),
            provider_entry(
                "oxford",
                "O1",
                "Oxford definition",
                example="Oxford example.",
                ipa="oxford-ipa",
                word_audio="https://www.oxfordlearnersdictionaries.com/oxford.mp3",
            ),
        ]
        content = resolve_canonical_content(request(entries))
        self.assertEqual(
            ("Longman definition", "longman", "L1"),
            (
                content.definition.value,
                content.definition.source,
                content.definition.sense_id,
            ),
        )
        self.assertEqual(
            ("Longman example.", "longman", "L1"),
            (content.example.value, content.example.source, content.example.sense_id),
        )
        self.assertEqual(
            (longman_audio, "longman", "L1"),
            (
                content.example_audio.value,
                content.example_audio.source,
                content.example_audio.sense_id,
            ),
        )
        self.assertEqual(("oxford-ipa", "oxford"), (content.ipa.value, content.ipa.source))
        self.assertEqual(
            ["oxford", "cambridge", "longman"],
            [field.source for field in content.word_audio_urls],
        )
        self.assertEqual(
            ("cambridge", "C1"),
            (content.image.source, content.image.sense_id),
        )

    def test_non_longman_example_never_inherits_longman_audio(self):
        entries = [
            provider_entry("cambridge", "C1", "Cambridge", example="Cambridge example."),
            provider_entry(
                "longman",
                "L1",
                "Longman",
                example="",
                example_audio="https://www.ldoceonline.com/media/english/exaProns/unused.mp3",
            ),
            provider_entry("oxford", "O1", "Oxford", example="Oxford example."),
        ]
        content = resolve_canonical_content(request(entries))
        self.assertEqual("cambridge", content.example.source)
        self.assertEqual("", content.example_audio.value)

    def test_longman_definition_enrichment_is_rendered_without_changing_definition(self):
        entries = [
            provider_entry("cambridge", "C1", "Cambridge definition"),
            provider_entry(
                "longman",
                "L1",
                "base definition",
                synonyms=["give way", "surrender"],
                thesaurus_terms=["amount", "return"],
            ),
        ]
        content = resolve_canonical_content(request(entries))
        self.assertEqual("base definition", content.definition.value)
        self.assertEqual(["give way", "surrender"], content.definition_synonyms)
        self.assertEqual(["amount", "return"], content.definition_thesaurus_terms)

        card = build_canonical_en_word_card(content, [], None, None)
        self.assertIn(
            "<b>Definition (EN):</b> base definition "
            "SYN give way, surrender amount, return<br>",
            card.back,
        )

    def test_definition_and_example_fallbacks_are_independent(self):
        cases = (
            (
                [
                    provider_entry("cambridge", "C1", "Cambridge", example="Cambridge example."),
                    provider_entry("longman", "L1", "Longman"),
                    provider_entry("oxford", "O1", "Oxford", example="Oxford example."),
                ],
                ("longman", "cambridge"),
            ),
            (
                [
                    provider_entry("cambridge", "C1", ""),
                    provider_entry("longman", "L1", ""),
                    provider_entry("oxford", "O1", "Oxford", example="Oxford example."),
                ],
                ("oxford", "oxford"),
            ),
            (
                [
                    provider_entry("cambridge", "C1", ""),
                    provider_entry("longman", "L1", ""),
                    provider_entry("oxford", "O1", ""),
                ],
                ("", ""),
            ),
        )
        for entries, expected in cases:
            with self.subTest(expected=expected):
                content = resolve_canonical_content(request(entries))
                self.assertEqual(
                    expected,
                    (content.definition.source, content.example.source),
                )

    def test_example_audio_accepts_only_real_longman_example_urls(self):
        item = InputItem("en_word", "trunk noun 1")
        valid = "https://www.ldoceonline.com/media/english/exaProns/example.mp3"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.object(audio, "_try_download_english_mp3_from_urls", return_value=valid) as download:
                asset = audio.ensure_example_audio(root, item, valid)
            download.assert_called_once()
            self.assertEqual(("example_audio", "longman", valid), (
                asset.source_type, asset.source, asset.source_url
            ))

            with patch.object(audio, "_try_download_english_mp3_from_urls") as rejected:
                self.assertIsNone(audio.ensure_example_audio(
                    root,
                    item,
                    "https://dictionary.cambridge.org/media/example.mp3",
                ))
                self.assertIsNone(audio.ensure_example_audio(
                    root,
                    item,
                    "https://www.ldoceonline.com/media/english/word.mp3",
                ))
            rejected.assert_not_called()

    def test_canonical_image_never_uses_page_level_fallback(self):
        item = InputItem("en_word", "trunk noun 1")
        with tempfile.TemporaryDirectory() as tmp, patch.object(
            images, "fetch_dictionary_image_url"
        ) as fallback:
            self.assertIsNone(
                images.ensure_noun_image(
                    Path(tmp),
                    item,
                    preferred_image_url="",
                    preferred_image_source="cambridge",
                    allow_fallback=False,
                )
            )
            self.assertIsNone(
                images.ensure_noun_image(
                    Path(tmp),
                    item,
                    preferred_image_url="https://example.test/not-dictionary.jpg",
                    preferred_image_source="cambridge",
                    allow_fallback=False,
                )
            )
        fallback.assert_not_called()

    def test_renderer_labels_sense_and_example_audio_is_click_only(self):
        content = ResolvedCanonicalContent(
            word="trunk & box",
            pos="noun",
            index=2,
            canonical_key="canonical::two",
            status="active",
            definition=ResolvedContentField("a box", "longman", "L2"),
            example=ResolvedContentField("Use <it>.", "longman", "L2"),
            example_audio=ResolvedContentField("url", "longman", "L2"),
            ipa=ResolvedContentField("trʌŋk", "oxford"),
        )
        word_audio = AudioAsset("word.mp3", Path("word.mp3"))
        example_audio = AudioAsset("example.mp3", Path("example.mp3"))
        image = AudioAsset("image.jpg", Path("image.jpg"))
        card = build_canonical_en_word_card(
            content, [word_audio], example_audio, image
        )
        self.assertIn("noun 2", card.front)
        self.assertNotIn("<b>Sense:</b>", card.back)
        self.assertTrue(card.back.startswith("[sound:word.mp3]<br><b>Definition (EN):</b>"))
        self.assertIn("[sound:word.mp3]", card.back)
        self.assertNotIn("[sound:example.mp3]", card.back)
        self.assertNotIn("▶ Play example", card.back)
        self.assertIn('src="audio_bre_initial.svg"', card.back)
        self.assertIn('aria-label="Play example audio"', card.back)
        self.assertIn('onclick="this.nextElementSibling.play()"', card.back)
        self.assertIn('preload="none" src="example.mp3"', card.back)
        self.assertIn("Use &lt;it&gt;.", card.back)
        self.assertEqual("en_word::canonical::two", card.guid_seed)

    def test_renderer_omits_example_audio_control_without_example_audio(self):
        content = ResolvedCanonicalContent(
            word="trunk",
            pos="noun",
            index=1,
            canonical_key="canonical::one",
            status="active",
            definition=ResolvedContentField("the main stem", "cambridge", "C1"),
            example=ResolvedContentField("A thick trunk.", "cambridge", "C1"),
            example_audio=ResolvedContentField(),
            ipa=ResolvedContentField("trʌŋk", "oxford"),
        )
        card = build_canonical_en_word_card(content, [], None, None)
        self.assertNotIn("audio_bre_initial.svg", card.back)
        self.assertNotIn("<audio", card.back)
        self.assertNotIn("<button", card.back)


if __name__ == "__main__":
    unittest.main()
