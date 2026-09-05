from __future__ import annotations

import unittest

from dictionary import canonical, pronunciation, service
from models import DictionaryEntryResult


def entry(source: str, **values) -> DictionaryEntryResult:
    return DictionaryEntryResult(source=source, **values)


class DictionaryFieldPriorityTests(unittest.TestCase):
    def test_each_field_has_its_own_priority(self):
        entries = [
            entry(
                "longman",
                word="rose",
                actual_pos="noun",
                ipa_uk="longman-ipa",
                audio_uk_url="https://www.ldoceonline.com/longman.mp3",
                definition="longman definition",
                sense_id="longman:1",
                image_url="https://www.ldoceonline.com/image.jpg",
            ),
            entry(
                "cambridge",
                word="rose",
                actual_pos="noun",
                ipa_uk="cambridge-ipa",
                audio_uk_url="https://dictionary.cambridge.org/cambridge.mp3",
                definition="cambridge definition",
                sense_id="cambridge:1",
                image_url="https://dictionary.cambridge.org/images/full/rose.jpg",
            ),
            entry(
                "oxford",
                word="rose",
                actual_pos="noun",
                ipa_uk="oxford-ipa",
                audio_uk_url=(
                    "https://www.oxfordlearnersdictionaries.com/oxford.mp3"
                ),
                definition="oxford definition",
                sense_id="oxford:1",
                image_url="https://www.oxfordlearnersdictionaries.com/image.jpg",
            ),
        ]

        merged = service.merge_dictionary_entries(entries)
        self.assertEqual(("oxford-ipa", "oxford"), (merged.ipa_uk, merged.ipa_source))
        self.assertEqual("oxford", merged.audio_source)
        self.assertEqual(("longman definition", "longman", "longman:1"), (
            merged.definition,
            merged.definition_source,
            merged.sense_id,
        ))
        self.assertEqual("cambridge", merged.image_source)

        self.assertEqual(
            ["oxford", "cambridge", "longman"],
            [source for _url, source in pronunciation.ordered_word_audio_urls(entries)],
        )
        self.assertEqual(
            ("longman", "cambridge", "oxford", "llm"),
            canonical.DEFINITION_PRIORITY,
        )
        self.assertEqual(("longman", "cambridge", "oxford", "llm"), canonical.EXAMPLE_PRIORITY)
        self.assertEqual("longman", canonical.EXAMPLE_AUDIO_SOURCE)
        self.assertEqual("cambridge", canonical.IMAGE_SOURCE)
        self.assertEqual(
            ("oxford", "cambridge", "longman"),
            pronunciation.WORD_IPA_PRIORITY,
        )
        self.assertEqual(
            ("oxford", "cambridge", "longman"),
            pronunciation.WORD_AUDIO_PRIORITY,
        )
        self.assertFalse(hasattr(service, "PROVIDER_ORDER"))

    def test_ipa_and_audio_are_selected_independently(self):
        entries = [
            entry("cambridge", ipa_uk="cambridge-ipa"),
            entry(
                "oxford",
                audio_uk_url="https://www.oxfordlearnersdictionaries.com/word.mp3",
            ),
            entry("longman", ipa_uk="longman-ipa"),
        ]
        merged = service.merge_dictionary_entries(entries)
        self.assertEqual(("cambridge-ipa", "cambridge"), (merged.ipa_uk, merged.ipa_source))
        self.assertEqual("oxford", merged.audio_source)

    def test_priority_fallback_matrix(self):
        cases = (
            ({"oxford", "cambridge", "longman"}, "oxford", "longman"),
            ({"cambridge", "longman"}, "cambridge", "longman"),
            ({"longman"}, "longman", "longman"),
            (set(), "", ""),
        )
        for available, pronunciation_source, definition_source in cases:
            with self.subTest(available=available):
                entries = [
                    entry(
                        source,
                        ipa_uk=f"{source}-ipa",
                        audio_uk_url=f"https://example.test/{source}.mp3",
                        definition=f"{source} definition",
                        sense_id=f"{source}:1",
                    )
                    for source in ("cambridge", "oxford", "longman")
                    if source in available
                ]
                merged = service.merge_dictionary_entries(entries)
                self.assertEqual(pronunciation_source, merged.ipa_source)
                self.assertEqual(pronunciation_source, merged.audio_source)
                self.assertEqual(definition_source, merged.definition_source)

    def test_requested_pos_excludes_other_pos_fields(self):
        entries = [
            entry(
                "longman",
                requested_pos="noun",
                actual_pos="verb",
                definition="wrong verb definition",
                ipa_uk="wrong-verb-ipa",
            ),
            entry(
                "cambridge",
                requested_pos="noun",
                actual_pos="noun",
                definition="noun definition",
                ipa_uk="noun-ipa",
                sense_id="cambridge:noun:1",
            ),
        ]
        merged = service.merge_dictionary_entries(entries)
        self.assertEqual("noun definition", merged.definition)
        self.assertEqual("noun-ipa", merged.ipa_uk)
        self.assertEqual("noun", merged.actual_pos)


if __name__ == "__main__":
    unittest.main()
