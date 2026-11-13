from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
import re
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.parse import urljoin

from anki_generator.dictionary import cambridge, longman, oxford
from anki_generator.models import (
    ProviderSense,
    ResolvedCanonicalContent,
    ResolvedContentField,
)
from tests.dictionary_page_snapshot import read_page
from tests.support import DictionaryFixtureHTTP, GOLDEN_ROOT, load_json


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

    def test_signpost_fields_default_to_empty_for_existing_constructors(self):
        sense = ProviderSense("longman", "L1", "noun", "a definition")
        self.assertEqual("", sense.signpost)
        content = ResolvedCanonicalContent(
            word="territory",
            pos="noun",
            index=1,
            canonical_key="territory-1",
            status="active",
            definition=ResolvedContentField(),
            example=ResolvedContentField(),
            example_audio=ResolvedContentField(),
            ipa=ResolvedContentField(),
        )
        self.assertEqual("", content.definition_signpost)

    def test_territory_signposts_and_thesaurus_stay_with_native_senses(self):
        page = (
            Path(__file__).resolve().parents[1]
            / "docs" / "dictionary" / "territory" / "territory_longman.md"
        )
        entries = longman.parse_longman_provider_entries(
            page.read_text(encoding="utf-8"),
            "https://www.ldoceonline.com/dictionary/territory",
            "territory",
        )
        self.assertEqual(["ldoce", "business"], [entry.dataset for entry in entries])
        senses = {sense.native_id: sense for entry in entries for sense in entry.senses}
        self.assertEqual(
            {
                "territory__1": "government/military",
                "territory__2": "type of land",
                "territory__3": "animal",
                "territory__4": "new or familiar experience",
                "territory__5": "business",
                "territory__7": "land that is not a state",
                "territory__9": "",
                "territory__10": "",
                "territory__11": "",
                "territory__12": "",
            },
            {native_id: sense.signpost for native_id, sense in senses.items()},
        )
        self.assertEqual(["land"], senses["territory__1"].thesaurus_terms)
        for native_id, sense in senses.items():
            if native_id != "territory__1":
                self.assertEqual([], sense.thesaurus_terms, native_id)
        self.assertEqual(["territory__6"], entries[0].cross_reference_ids)
        self.assertNotIn("territory__6", senses)
        self.assertEqual(
            "The plane was flying over enemy territory.",
            senses["territory__1"].examples[0].text,
        )

    def test_longman_signpost_cleaning_and_missing_values_in_each_dataset(self):
        for entry_class in ("ldoceEntry", "bussdictEntry"):
            with self.subTest(entry_class=entry_class):
                body = f'''
                    <span class="{entry_class} Entry">
                      <span class="Head">
                        <span class="HWD">territory</span>
                        <span class="POS">noun</span>
                      </span>
                      <span class="Sense" id="L1">
                        <span class="SIGNPOST">
                          Government/<span>Military</span> &amp; &lt;Land&gt;
                        </span>
                        <span class="DEF">first definition</span>
                      </span>
                      <span class="Sense" id="L2">
                        <span class="GRAM">countable</span>
                        <span class="COLLO">sales territory</span>
                        <span class="LEXUNIT">negative territory</span>
                        <span class="DEF">second definition</span>
                      </span>
                      <span class="Sense" id="L3">
                        <span class="SIGNPOST"> \n &nbsp; </span>
                        <span class="DEF">third definition</span>
                      </span>
                    </span>
                '''
                entries = longman.parse_longman_provider_entries(
                    body, "https://www.ldoceonline.com/dictionary/territory", "territory"
                )
                self.assertEqual(1, len(entries))
                self.assertEqual(
                    ["Government/Military & <Land>", "", ""],
                    [sense.signpost for sense in entries[0].senses],
                )
                self.assertEqual(
                    ["first definition", "second definition", "third definition"],
                    [sense.definition for sense in entries[0].senses],
                )

    def test_yield_longman_enrichment_stays_with_its_native_sense(self):
        entries = longman.parse_longman_provider_entries(
            (Path(__file__).resolve().parents[1] / "docs" / "dictionary" / "yield" / "yield_longman.md").read_text(
                encoding="utf-8"
            ),
            "https://www.ldoceonline.com/dictionary/yield",
            "yield",
        )
        enrichment = {
            sense.native_id: (sense.synonyms, sense.thesaurus_terms)
            for entry in entries
            for sense in entry.senses
            if sense.synonyms or sense.thesaurus_terms
        }
        self.assertEqual(
            {
                "yield__4": (["give way"], []),
                "yield__5": (["give"], []),
                "yield__6": (["surrender"], []),
                "yield__8": ([], ["amount"]),
            },
            enrichment,
        )

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
                expected_urls = [url]
                if provider == "oxford":
                    expected_urls.append(
                        "https://www.oxfordlearnersdictionaries.com/definition/english/nail_2"
                    )
                self.assertEqual(expected_urls, [call.args[0] for call in get.call_args_list])
                for call in get.call_args_list:
                    self.assertEqual(12, call.kwargs["timeout"])
                    self.assertEqual(
                        {"User-Agent": "anki-batch-generator/2.0"},
                        call.kwargs["headers"],
                    )

    def test_oxford_fetch_discovers_nail_noun_and_verb_pronunciations(self):
        fixture_http = DictionaryFixtureHTTP()
        with (
            patch.object(oxford.requests, "get", side_effect=fixture_http.get),
            patch.object(oxford, "retry_call", side_effect=lambda fn, **_: fn()),
        ):
            entries = oxford.fetch_oxford_provider_entries("nail")

        self.assertEqual(["noun", "verb"], [entry.pos for entry in entries])
        self.assertEqual(
            [
                "https://www.oxfordlearnersdictionaries.com/media/english/uk_pron/"
                "n/nai/nail_/nail__gb_1.mp3",
                "https://www.oxfordlearnersdictionaries.com/media/english/uk_pron/"
                "n/nai/nail_/nail__gb_1.mp3",
            ],
            [entry.pronunciation.audio_uk_url for entry in entries],
        )
        self.assertEqual(
            [
                "https://www.oxfordlearnersdictionaries.com/definition/english/nail",
                "https://www.oxfordlearnersdictionaries.com/definition/english/nail_2",
            ],
            [call["url"] for call in fixture_http.calls],
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
