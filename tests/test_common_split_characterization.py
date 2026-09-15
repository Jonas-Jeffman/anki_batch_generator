from __future__ import annotations

import argparse
import tempfile
import unittest
from pathlib import Path

from tests.support import GOLDEN_ROOT, load_json

from anki_generator.llm import cache
from anki_generator.compat import common
from anki_generator import config, models, utils
from anki_generator.inputs import english as english_terms
from anki_generator.inputs import loader as terms


class CommonSplitCharacterizationTests(unittest.TestCase):
    maxDiff = None

    def test_common_reexports_focused_module_objects(self):
        expected = {
            "CacheStore": cache.CacheStore,
            "build_cache_key": cache.build_cache_key,
            "InputItem": models.InputItem,
            "ParsedEnglishTerm": models.ParsedEnglishTerm,
            "BuiltCard": models.BuiltCard,
            "AudioAsset": models.AudioAsset,
            "EnglishPronunciationInfo": models.EnglishPronunciationInfo,
            "DictionarySenseCandidate": models.DictionarySenseCandidate,
            "DictionaryEntryResult": models.DictionaryEntryResult,
            "load_items": terms.load_items,
            "read_terms_from_json_string": terms.read_terms_from_json_string,
            "read_terms_from_json_file": terms.read_terms_from_json_file,
            "read_terms_from_txt_file": terms.read_terms_from_txt_file,
            "read_terms_from_path": terms.read_terms_from_path,
            "normalize_pos_tag": english_terms.normalize_pos_tag,
            "parse_english_term": english_terms.parse_english_term,
            "extract_pos_tags": english_terms.extract_pos_tags,
            "strip_pos_labels_from_term": english_terms.strip_pos_labels_from_term,
            "is_single_word_term": english_terms.is_single_word_term,
            "lexical_word_count": english_terms.lexical_word_count,
            "is_two_word_term": english_terms.is_two_word_term,
            "en_word_uses_dictionary_lookup": english_terms.en_word_uses_dictionary_lookup,
            "stable_anki_id": utils.stable_anki_id,
            "stable_guid": utils.stable_guid,
            "slugify": utils.slugify,
            "html_escape": utils.html_escape,
            "retry_call": utils.retry_call,
            "resolve_openai_api_key": config.resolve_openai_api_key,
            "resolve_openai_base_url": config.resolve_openai_base_url,
        }
        for name, value in expected.items():
            with self.subTest(name=name):
                self.assertIn(name, common.__all__)
                self.assertIs(getattr(common, name), value)
                self.assertNotEqual(getattr(common, name).__module__, "common")

    def test_term_parsing_and_deduplication_remain_identical(self):
        args = argparse.Namespace(
            terms_json='[" nail noun ", "rose", "rose", "play hooky"]',
            terms_file="",
            mode="en_word",
            hint="fixture hint",
            tags=["one", "two words"],
        )
        expected = [
            models.InputItem("en_word", "nail noun", "fixture hint", ["one", "two words"]),
            models.InputItem("en_word", "rose", "fixture hint", ["one", "two words"]),
            models.InputItem("en_word", "play hooky", "fixture hint", ["one", "two words"]),
        ]
        self.assertEqual(expected, common.load_items(args))
        self.assertEqual(expected, terms.load_items(args))

        expected_parses = [
            models.ParsedEnglishTerm("nail noun", "nail", "noun"),
            models.ParsedEnglishTerm("nail verb", "nail", "verb"),
            models.ParsedEnglishTerm("rose", "rose", ""),
            models.ParsedEnglishTerm("play hooky", "play hooky", ""),
        ]
        self.assertEqual(
            expected_parses,
            [common.parse_english_term(value.raw) for value in expected_parses],
        )

    def test_cache_tolerance_and_serialization_are_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "nested" / "cache.json"
            path.parent.mkdir(parents=True)
            path.write_text("not json", encoding="utf-8")
            store = common.CacheStore(path)
            self.assertEqual({}, store.data)
            self.assertIsNone(store.get("missing"))

            store.set("unicode", {"word": "勉強"})
            store.save()
            self.assertEqual(
                '{\n  "unicode": {\n    "word": "勉強"\n  }\n}',
                path.read_text(encoding="utf-8"),
            )

            path.write_text('["not", "a", "mapping"]', encoding="utf-8")
            self.assertEqual(["not", "a", "mapping"], common.CacheStore(path).data)

    def test_cache_key_guid_and_ids_match_fixed_goldens(self):
        expected = load_json(GOLDEN_ROOT / "split_primitives.json")
        actual = {
            "cache_key": common.build_cache_key(
                mode="en_word",
                term="nail noun",
                hint="",
                model="gpt-5.4",
                llm_schema="no_zh_v5_safe_ipa",
                dict_phonetic="/neɪl/",
                dict_phonetic_source="oxford",
                parsed_word="nail",
                requested_pos="noun",
                dictionary_definition=(
                    "a small, thin piece of metal with one pointed end and one flat end that you hit "
                    "into something with a hammer, especially in order to fasten or join it to something else:"
                ),
            ),
            "deck_id": common.stable_anki_id("deck::Regression::Deck"),
            "model_id": common.stable_anki_id("model::anki_batch_generator::basic_v2"),
            "guid": common.stable_guid("Regression::Deck", "en_word::nail noun"),
        }
        self.assertEqual(expected, actual)


if __name__ == "__main__":
    unittest.main()
