from __future__ import annotations

from dataclasses import asdict
import re
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.parse import urljoin

from dictionary import cambridge, longman, oxford
from tests.dictionary_page_snapshot import read_page
from tests.support import GOLDEN_ROOT, load_json


PROVIDERS = {
    "cambridge": (
        cambridge,
        cambridge.parse_cambridge_provider_entries,
        cambridge.fetch_cambridge_provider_entries,
        "https://dictionary.cambridge.org/dictionary/english/{term}",
    ),
    "longman": (
        longman,
        longman.parse_longman_provider_entries,
        longman.fetch_longman_provider_entries,
        "https://www.ldoceonline.com/dictionary/{term}",
    ),
    "oxford": (
        oxford,
        oxford.parse_oxford_provider_entries,
        oxford.fetch_oxford_provider_entries,
        "https://www.oxfordlearnersdictionaries.com/definition/english/{term}",
    ),
}


def normalized_snapshot_text(value: str) -> str:
    value = re.sub(r"\s+([.,!?;:)])", r"\1", value)
    value = re.sub(r"([(])\s+", r"\1", value)
    return value.rstrip(":")


class ProviderSenseModelTests(unittest.TestCase):
    maxDiff = None

    def test_real_pages_match_structured_object_golden(self):
        expected = load_json(GOLDEN_ROOT / "provider_entries.json")
        actual = {}
        for term in ("nail", "trunk"):
            actual[term] = {}
            for provider, (_, parser, _, url_template) in PROVIDERS.items():
                actual[term][provider] = [
                    asdict(entry)
                    for entry in parser(
                        read_page(term, provider),
                        url_template.format(term=term),
                        term,
                    )
                ]
        self.assertEqual(expected, actual)

    def test_structured_fields_match_independent_dom_snapshot(self):
        snapshot = load_json(GOLDEN_ROOT / "real_dictionary_pages.json")["pages"]
        for term in ("nail", "trunk"):
            with self.subTest(term=term, provider="cambridge"):
                entries = cambridge.parse_cambridge_provider_entries(
                    read_page(term, "cambridge"),
                    f"https://dictionary.cambridge.org/dictionary/english/{term}",
                    term,
                )
                actual = [
                    {
                        "native_id": sense.native_id,
                        "pos": sense.pos,
                        "definition": sense.definition,
                        "examples": [example.text for example in sense.examples],
                        "image_path": sense.image_url,
                    }
                    for entry in entries
                    for sense in entry.senses
                ]
                expected = [
                    {
                        "native_id": sense["native_id"],
                        "pos": sense["pos"],
                        "definition": normalized_snapshot_text(sense["definition"]),
                        "examples": [
                            normalized_snapshot_text(example)
                            for example in sense["examples"]
                        ],
                        "image_path": urljoin(
                            "https://dictionary.cambridge.org",
                            sense["image_path"],
                        ) if sense["image_path"] else "",
                    }
                    for sense in snapshot[term]["cambridge"]["cald_senses"]
                ]
                for sense in actual:
                    sense["definition"] = normalized_snapshot_text(sense["definition"])
                    sense["examples"] = [
                        normalized_snapshot_text(example) for example in sense["examples"]
                    ]
                self.assertEqual(expected, actual)

            with self.subTest(term=term, provider="longman"):
                entries = longman.parse_longman_provider_entries(
                    read_page(term, "longman"),
                    f"https://www.ldoceonline.com/dictionary/{term}",
                    term,
                )
                actual = [
                    {
                        "entry_type": entry.dataset,
                        "headword": entry.word,
                        "pos": entry.pos,
                        "senses": [
                            {
                                "native_id": sense.native_id,
                                "definition": normalized_snapshot_text(sense.definition),
                                "example_audio_urls": [
                                    example.audio_url for example in sense.examples
                                ],
                            }
                            for sense in entry.senses
                        ],
                        "cross_reference_ids": entry.cross_reference_ids,
                        "other_sense_ids": entry.other_sense_ids,
                    }
                    for entry in entries
                ]
                expected = [
                    {
                        "entry_type": entry["entry_type"],
                        "headword": entry["headword"],
                        "pos": entry["pos"],
                        "senses": [
                            {
                                "native_id": sense["native_id"],
                                "definition": normalized_snapshot_text(sense["definition"]),
                                "example_audio_urls": [
                                    example["audio_url"] for example in sense["examples"]
                                ],
                            }
                            for sense in entry["senses"]
                        ],
                        "cross_reference_ids": entry["cross_reference_ids"],
                        "other_sense_ids": entry["other_sense_ids"],
                    }
                    for entry in snapshot[term]["longman"]["entries"]
                ]
                self.assertEqual(expected, actual)

            with self.subTest(term=term, provider="oxford"):
                entry = oxford.parse_oxford_provider_entries(
                    read_page(term, "oxford"),
                    f"https://www.oxfordlearnersdictionaries.com/definition/english/{term}",
                    term,
                )[0]
                expected = snapshot[term]["oxford"]
                self.assertEqual(expected["headword"], entry.word)
                self.assertEqual(expected["pos"], entry.pos)
                self.assertEqual(expected["uk_ipa"], entry.pronunciation.ipa_uk)
                self.assertEqual(expected["uk_audio_url"], entry.pronunciation.audio_uk_url)
                self.assertEqual(
                    [sense["native_id"] for sense in expected["lexical_senses"]],
                    [sense.native_id for sense in entry.senses],
                )
                for expected_sense, actual_sense in zip(
                    expected["lexical_senses"], entry.senses
                ):
                    self.assertTrue(
                        actual_sense.definition.startswith(expected_sense["definition"])
                    )
                self.assertEqual(expected["idiom_ids"], entry.idiom_ids)

    def test_fetchers_keep_exact_request_contract(self):
        for provider, (module, _, fetcher, url_template) in PROVIDERS.items():
            with self.subTest(provider=provider):
                term = "nail"
                url = url_template.format(term=term)
                response = SimpleNamespace(ok=True, text=read_page(term, provider), url=url)
                with (
                    patch.object(module.requests, "get", return_value=response) as get,
                    patch.object(module, "retry_call", side_effect=lambda fn, **_: fn()),
                ):
                    entries = fetcher(term)
                self.assertTrue(entries)
                get.assert_called_once_with(
                    url,
                    timeout=12,
                    headers={"User-Agent": "anki-batch-generator/2.0"},
                )

    def test_phase_four_structural_acceptance_counts(self):
        trunk_cambridge = cambridge.parse_cambridge_provider_entries(
            read_page("trunk", "cambridge"),
            "https://dictionary.cambridge.org/dictionary/english/trunk",
            "trunk",
        )
        cambridge_senses = [
            sense for entry in trunk_cambridge for sense in entry.senses
        ]
        self.assertEqual(8, len(cambridge_senses))
        for sense in cambridge_senses:
            if sense.image_url:
                self.assertIn(sense.native_id, sense.image_dom_path)

        trunk_longman = longman.parse_longman_provider_entries(
            read_page("trunk", "longman"),
            "https://www.ldoceonline.com/dictionary/trunk",
            "trunk",
        )
        longman_senses = [sense for entry in trunk_longman for sense in entry.senses]
        self.assertEqual(6, len(longman_senses))
        recorded_examples = [
            example for sense in longman_senses for example in sense.examples
        ]
        self.assertEqual(3, len(recorded_examples))
        self.assertTrue(all(example.audio_url for example in recorded_examples))
        self.assertTrue(
            all(sense.native_id in example.dom_path for sense in longman_senses for example in sense.examples)
        )

        nail_oxford = oxford.parse_oxford_provider_entries(
            read_page("nail", "oxford"),
            "https://www.oxfordlearnersdictionaries.com/definition/english/nail",
            "nail",
        )[0]
        self.assertEqual(2, len(nail_oxford.senses))
        self.assertEqual(6, len(nail_oxford.idiom_ids))
        self.assertTrue(
            set(nail_oxford.idiom_ids).isdisjoint(
                sense.native_id for sense in nail_oxford.senses
            )
        )
        self.assertIn("/uk_pron/", nail_oxford.pronunciation.audio_uk_url)

    def test_fetchers_preserve_rejection_and_exception_swallowing(self):
        rejected = {
            "cambridge": SimpleNamespace(
                ok=True,
                text=read_page("nail", "cambridge"),
                url="https://dictionary.cambridge.org/dictionary/english/not-nail",
            ),
            "longman": SimpleNamespace(
                ok=True,
                text=read_page("nail", "longman"),
                url="https://www.ldoceonline.com/spellcheck/english/?q=nail",
            ),
            "oxford": SimpleNamespace(
                ok=True,
                text="<title>Did you spell it correctly?</title>",
                url="https://www.oxfordlearnersdictionaries.com/definition/english/nail",
            ),
        }
        for provider, (module, _, fetcher, _) in PROVIDERS.items():
            with self.subTest(provider=provider, case="rejected"):
                with (
                    patch.object(module.requests, "get", return_value=rejected[provider]),
                    patch.object(module, "retry_call", side_effect=lambda fn, **_: fn()),
                ):
                    self.assertEqual([], fetcher("nail"))
            with self.subTest(provider=provider, case="exception"):
                with patch.object(module, "retry_call", side_effect=RuntimeError("offline")):
                    self.assertEqual([], fetcher("nail"))


if __name__ == "__main__":
    unittest.main()
