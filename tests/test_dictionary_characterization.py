from __future__ import annotations

import unittest
from dataclasses import asdict
from unittest.mock import patch

from tests.support import DictionaryFixtureHTTP, GOLDEN_ROOT, load_json, normalized_calls

from anki_generator.compat import dictionary_sources
from anki_generator.dictionary import cambridge, longman, oxford
from anki_generator.inputs.english import parse_english_term


TERMS = ("nail noun", "nail verb", "rose", "play hooky")


class DictionaryCharacterizationTests(unittest.TestCase):
    maxDiff = None

    def test_recorded_dictionary_results_and_request_trace(self):
        expected = load_json(GOLDEN_ROOT / "dictionary_results.json")

        for raw_term in TERMS:
            with self.subTest(term=raw_term):
                http = DictionaryFixtureHTTP()
                with (
                    patch.object(cambridge.requests, "get", http.get),
                    patch.object(oxford.requests, "get", http.get),
                    patch.object(longman.requests, "get", http.get),
                ):
                    parsed = parse_english_term(raw_term)
                    entries = dictionary_sources.fetch_dictionary_entries(
                        parsed.word, parsed.requested_pos
                    )
                    merged = dictionary_sources.merge_dictionary_entries(entries)
                    preview = dictionary_sources.dictionary_result_preview(raw_term)

                actual = {
                    "parse": asdict(parsed),
                    "provider_entries": [asdict(entry) for entry in entries],
                    "merged": asdict(merged),
                    "preview": preview,
                    "requests": normalized_calls(http.calls),
                }
                self.assertEqual(expected[raw_term], actual)


if __name__ == "__main__":
    unittest.main()
