from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from tests.dictionary_page_snapshot import (
    cambridge_snapshot,
    fixture_metadata,
    longman_snapshot,
    oxford_snapshot,
    read_page,
)
from tests.support import GOLDEN_ROOT, load_json

from dictionary import cambridge, longman, oxford


class RealDictionaryPageTests(unittest.TestCase):
    maxDiff = None

    def setUp(self):
        self.golden = load_json(GOLDEN_ROOT / "real_dictionary_pages.json")

    def test_recorded_fixture_checksums_match_golden(self):
        actual = [
            fixture_metadata(term, provider)
            for term in ("nail", "trunk")
            for provider in ("cambridge", "longman", "oxford")
        ]
        self.assertEqual(self.golden["fixtures"], actual)

    def test_cambridge_datasets_senses_and_image_relationships_match_golden(self):
        for term in ("nail", "trunk"):
            with self.subTest(term=term):
                self.assertEqual(
                    self.golden["pages"][term]["cambridge"],
                    cambridge_snapshot(term),
                )

    def test_longman_entries_examples_audio_and_crossrefs_match_golden(self):
        for term in ("nail", "trunk"):
            with self.subTest(term=term):
                self.assertEqual(
                    self.golden["pages"][term]["longman"],
                    longman_snapshot(term),
                )

    def test_oxford_lexical_senses_idioms_and_uk_pronunciation_match_golden(self):
        for term in ("nail", "trunk"):
            with self.subTest(term=term):
                self.assertEqual(
                    self.golden["pages"][term]["oxford"],
                    oxford_snapshot(term),
                )

    def test_current_provider_parser_behavior_is_characterized(self):
        expected = load_json(GOLDEN_ROOT / "current_real_parser.json")
        providers = (
            ("cambridge", cambridge, cambridge.fetch_cambridge_sense_candidates),
            ("longman", longman, longman.fetch_longman_sense_candidates),
            ("oxford", oxford, oxford.fetch_oxford_sense_candidates),
        )
        actual = {}
        for provider, module, fetcher in providers:
            actual[provider] = {}
            for term in ("nail", "trunk"):
                response = SimpleNamespace(
                    ok=True,
                    text=read_page(term, provider),
                    url={
                        "cambridge": f"https://dictionary.cambridge.org/dictionary/english/{term}",
                        "longman": f"https://www.ldoceonline.com/dictionary/{term}",
                        "oxford": f"https://www.oxfordlearnersdictionaries.com/definition/english/{term}",
                    }[provider],
                )
                with patch.object(module.requests, "get", return_value=response):
                    senses = fetcher(term)
                actual[provider][term] = {
                    "sense_ids": [sense.sense_id for sense in senses],
                    "pos": [sense.pos for sense in senses],
                    "example_counts": [len(sense.examples) for sense in senses],
                    "image_urls": [sense.image_url for sense in senses if sense.image_url],
                }
        self.assertEqual(expected, actual)


if __name__ == "__main__":
    unittest.main()
