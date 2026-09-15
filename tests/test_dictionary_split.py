from __future__ import annotations

import unittest
from dataclasses import asdict
from unittest.mock import Mock, patch

from tests.support import DictionaryFixtureHTTP, FixtureResponse, GOLDEN_ROOT, load_json

from anki_generator.compat import dictionary_sources
from anki_generator.dictionary import cambridge, longman, oxford, service
from anki_generator.models import DictionaryEntryResult, DictionarySenseCandidate


class DictionarySplitTests(unittest.TestCase):
    maxDiff = None

    def test_provider_entry_parsers_match_recorded_goldens(self):
        golden = load_json(GOLDEN_ROOT / "dictionary_results.json")
        cases = (
            (cambridge, cambridge._cambridge_entry_result, "nail", "noun", "nail noun", "cambridge"),
            (oxford, oxford._oxford_entry_result, "nail", "verb", "nail verb", "oxford"),
            (longman, longman._longman_entry_result, "play hooky", "", "play hooky", "longman"),
        )
        for module, parser, word, pos, raw_term, source in cases:
            with self.subTest(source=source, term=raw_term):
                http = DictionaryFixtureHTTP()
                expected = next(
                    entry
                    for entry in golden[raw_term]["provider_entries"]
                    if entry["source"] == source
                )
                with patch.object(module.requests, "get", http.get):
                    actual = parser(word, pos)
                self.assertIsNotNone(actual)
                self.assertEqual(expected, asdict(actual))

    def test_provider_sense_and_image_parsers_replay_recorded_pages(self):
        cases = (
            (
                cambridge,
                cambridge.fetch_cambridge_sense_candidates,
                cambridge.fetch_cambridge_image_url,
                5,
                "past simple of rise",
            ),
            (
                oxford,
                oxford.fetch_oxford_sense_candidates,
                oxford.fetch_oxford_image_url,
                4,
                "a flower with a sweet smell that grows on a bush with thorns (= sharp points) on its stems",
            ),
            (
                longman,
                longman.fetch_longman_sense_candidates,
                longman.fetch_longman_image_url,
                5,
                "a flower that often has a pleasant smell, and is usually red, pink, white, or yellow, "
                "or the bush that this flower grows on",
            ),
        )
        for module, sense_parser, image_parser, count, first_definition in cases:
            with self.subTest(provider=module.__name__):
                http = DictionaryFixtureHTTP()
                with patch.object(module.requests, "get", http.get):
                    senses = sense_parser("rose")
                    image_url = image_parser("rose")
                self.assertEqual(count, len(senses))
                self.assertEqual(first_definition, senses[0].definition)
                self.assertTrue(all(candidate.source == module.__name__.rsplit(".", 1)[-1] for candidate in senses))
                self.assertEqual("", image_url)
                expected_calls = 4 if module is oxford else 2
                self.assertEqual(expected_calls, len(http.calls))

    def test_provider_rejection_paths_remain_unchanged(self):
        unavailable = Mock(
            return_value=FixtureResponse(text="unavailable", url="https://example.test/", status=503)
        )
        with patch.object(cambridge.requests, "get", unavailable):
            self.assertIsNone(cambridge._cambridge_entry_result("nail", "noun"))
        self.assertEqual(1, unavailable.call_count)

        fixture_http = DictionaryFixtureHTTP()
        recorded = fixture_http.get("https://dictionary.cambridge.org/dictionary/english/nail")
        redirected = Mock(
            return_value=FixtureResponse(
                text=recorded.text,
                url="https://dictionary.cambridge.org/dictionary/english/hammer",
                status=200,
            )
        )
        with patch.object(cambridge.requests, "get", redirected):
            self.assertIsNone(cambridge._cambridge_entry_result("nail", "noun"))
        self.assertEqual(1, redirected.call_count)

        misspelling = Mock(
            return_value=FixtureResponse(
                text="<title>Did you spell the word correctly?</title>",
                url="https://www.oxfordlearnersdictionaries.com/definition/english/missing",
                status=200,
            )
        )
        with patch.object(oxford.requests, "get", misspelling):
            self.assertIsNone(oxford._oxford_entry_result("missing"))
        self.assertEqual(5, misspelling.call_count)

        spellcheck = Mock(
            return_value=FixtureResponse(
                text="<html></html>",
                url="https://www.ldoceonline.com/spellcheck/english/?q=missing",
                status=200,
            )
        )
        with patch.object(longman.requests, "get", spellcheck):
            self.assertIsNone(longman._longman_entry_result("missing"))
        self.assertEqual(1, spellcheck.call_count)

    def test_service_calls_fixed_provider_order_and_merges_field_by_field(self):
        calls = []

        def entry(source, **values):
            return DictionaryEntryResult(source=source, **values)

        provider_results = {
            "cambridge": entry(
                "cambridge",
                word="rose",
                actual_pos="noun",
                definition="cambridge definition",
                image_url="https://dictionary.cambridge.org/images/full/rose.jpg",
                image_alt="rose",
                sense_id="cambridge:rose:1",
            ),
            "oxford": entry(
                "oxford",
                word="rose",
                ipa_uk="/rəʊz/",
                audio_uk_url="https://example.test/oxford.mp3",
                definition="oxford definition",
                image_url="https://example.test/oxford.jpg",
                sense_id="oxford:rose:1",
            ),
            "longman": entry(
                "longman",
                ipa_uk="longman-ipa",
                audio_uk_url="https://example.test/longman.mp3",
                definition="longman definition",
                sense_id="longman:rose:1",
            ),
        }

        def provider(source):
            def fetch(word, requested_pos):
                calls.append((source, word, requested_pos))
                return provider_results[source]
            return fetch

        with (
            patch.object(service, "_cambridge_entry_result", provider("cambridge")),
            patch.object(service, "_oxford_entry_result", provider("oxford")),
            patch.object(service, "_longman_entry_result", provider("longman")),
        ):
            entries = service.fetch_dictionary_entries("rose", "noun")

        self.assertEqual(
            [
                ("cambridge", "rose", "noun"),
                ("oxford", "rose", "noun"),
                ("longman", "rose", "noun"),
            ],
            calls,
        )
        merged = service.merge_dictionary_entries(entries)
        self.assertEqual("longman definition", merged.definition)
        self.assertEqual("longman", merged.definition_source)
        self.assertEqual("/rəʊz/", merged.ipa_uk)
        self.assertEqual("oxford", merged.ipa_source)
        self.assertEqual("https://example.test/oxford.mp3", merged.audio_uk_url)
        self.assertEqual("oxford", merged.audio_source)
        self.assertEqual("https://dictionary.cambridge.org/images/full/rose.jpg", merged.image_url)
        self.assertEqual("cambridge", merged.image_source)

    def test_best_sense_and_compatibility_exports_keep_fixed_functions(self):
        candidate = DictionarySenseCandidate(
            source="oxford",
            word="rose",
            pos="noun",
            sense_id="oxford:rose:1",
            definition="a flower",
        )
        calls = []
        with (
            patch.object(service, "fetch_cambridge_sense_candidates", side_effect=lambda term: calls.append("cambridge") or []),
            patch.object(service, "fetch_oxford_sense_candidates", side_effect=lambda term: calls.append("oxford") or [candidate]),
            patch.object(service, "fetch_longman_sense_candidates", side_effect=lambda term: calls.append("longman") or []),
        ):
            self.assertIs(candidate, service.fetch_best_dictionary_sense("rose"))
        self.assertEqual(["cambridge", "longman", "oxford"], calls)

        self.assertIs(dictionary_sources.fetch_dictionary_entries, service.fetch_dictionary_entries)
        self.assertIs(dictionary_sources.merge_dictionary_entries, service.merge_dictionary_entries)
        self.assertIs(dictionary_sources.dictionary_result_preview, service.dictionary_result_preview)
        self.assertIs(dictionary_sources.fetch_english_from_cambridge, cambridge.fetch_english_from_cambridge)
        self.assertIs(dictionary_sources.fetch_english_from_oxford, oxford.fetch_english_from_oxford)
        self.assertIs(dictionary_sources.fetch_english_from_longman, longman.fetch_english_from_longman)


if __name__ == "__main__":
    unittest.main()
