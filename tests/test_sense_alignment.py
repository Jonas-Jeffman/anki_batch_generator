from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cache import CacheStore, build_alignment_cache_key
from dictionary import canonical
from models import ProviderEntry, ProviderSense, WordPronunciation


def sense(source: str, native_id: str, definition: str, pos: str = "noun") -> ProviderSense:
    return ProviderSense(
        source=source,
        native_id=native_id,
        pos=pos,
        definition=definition,
        image_url=f"https://images.invalid/{native_id}.jpg",
    )


def alignment(
    cambridge_id: str,
    longman_ids=None,
    oxford_ids=None,
    relation: str = "equivalent",
    confidence: float = 0.95,
):
    return {
        "cambridge_sense_id": cambridge_id,
        "longman_sense_ids": longman_ids or [],
        "oxford_sense_ids": oxford_ids or [],
        "relation": relation,
        "confidence": confidence,
    }


def unmatched(source: str, sense_id: str, decision: str, confidence: float = 0.9):
    return {
        "source": source,
        "sense_id": sense_id,
        "decision": decision,
        "related_cambridge_sense_ids": [],
        "confidence": confidence,
    }


class SenseAlignmentTests(unittest.TestCase):
    def run_alignment(self, response, cambridge, longman, oxford=(), cache=None):
        if cache is None:
            self.temp_dir = tempfile.TemporaryDirectory()
            self.addCleanup(self.temp_dir.cleanup)
            cache = CacheStore(Path(self.temp_dir.name) / "cache.json")
        with patch.object(canonical, "generate_json", return_value=response) as generate:
            result = canonical.align_canonical_senses(
                client=None,
                word="nail",
                pos="noun",
                cambridge_senses=cambridge,
                longman_senses=longman,
                oxford_senses=oxford,
                model="fixture-model",
                reasoning_effort="medium",
                cache=cache,
            )
        return result, generate, cache

    def test_reversed_provider_order_matches_by_id_not_index_and_caches(self):
        cambridge = [
            sense("cambridge", "C-metal", "a pointed metal fastener"),
            sense("cambridge", "C-finger", "the hard end of a finger"),
        ]
        longman = [
            sense("longman", "L-finger", "the hard layer on a finger"),
            sense("longman", "L-metal", "a pointed piece of metal"),
        ]
        response = {
            "alignments": [
                alignment("C-metal", ["L-metal"]),
                alignment("C-finger", ["L-finger"]),
            ],
            "unmatched": [],
        }
        with tempfile.TemporaryDirectory() as tmp:
            cache = CacheStore(Path(tmp) / "cache.json")
            result, generate, _ = self.run_alignment(response, cambridge, longman, cache=cache)
            self.assertEqual(
                [["L-metal"], ["L-finger"]],
                [item.longman_sense_ids for item in result],
            )
            payload = json.loads(generate.call_args.kwargs["user_prompt"])
            self.assertEqual(["L-finger", "L-metal"], [x["sense_id"] for x in payload["longman_senses"]])
            self.assertTrue(
                all(
                    set(record) == {"sense_id", "definition"}
                    for field in (
                        "cambridge_senses",
                        "longman_senses",
                        "oxford_senses",
                    )
                    for record in payload[field]
                )
            )
            self.assertNotIn("image", generate.call_args.kwargs["user_prompt"])

            with patch.object(canonical, "generate_json") as cached_generate:
                cached = canonical.align_canonical_senses(
                    client=None,
                    word="nail",
                    pos="noun",
                    cambridge_senses=cambridge,
                    longman_senses=list(reversed(longman)),
                    oxford_senses=[],
                    model="fixture-model",
                    reasoning_effort="medium",
                    cache=cache,
                )
            cached_generate.assert_not_called()
            self.assertEqual(result, cached)

    def test_one_to_many_and_unmatched_append_decisions(self):
        cambridge = [sense("cambridge", "C1", "a storage box")]
        longman = [
            sense("longman", "L1", "a large storage box"),
            sense("longman", "L2", "a travelling storage box"),
            sense("longman", "L3", "an independent common meaning"),
            sense("longman", "L4", "a rare duplicate meaning"),
        ]
        response = {
            "alignments": [alignment("C1", ["L1", "L2"], relation="one_to_many")],
            "unmatched": [
                unmatched("longman", "L3", "append", 0.91),
                unmatched("longman", "L4", "ignore", 0.98),
            ],
        }
        result, _, _ = self.run_alignment(response, cambridge, longman)
        self.assertEqual(2, len(result))
        self.assertEqual(["L1", "L2"], result[0].longman_sense_ids)
        self.assertEqual("one_to_many", result[0].relation)
        self.assertEqual(("longman", "L3", 2), (
            result[1].backbone_source,
            result[1].backbone_sense_id,
            result[1].index,
        ))

    def test_invalid_ids_and_incomplete_results_are_not_cached(self):
        cambridge = [sense("cambridge", "C1", "definition")]
        longman = [sense("longman", "L1", "definition")]
        invalid_responses = (
            {"alignments": [alignment("missing", ["L1"])], "unmatched": []},
            {"alignments": [alignment("C1", ["missing"])], "unmatched": []},
            {"alignments": [alignment("C1")], "unmatched": []},
        )
        for response in invalid_responses:
            with self.subTest(response=response), tempfile.TemporaryDirectory() as tmp:
                cache = CacheStore(Path(tmp) / "cache.json")
                with patch.object(canonical, "generate_json", return_value=response):
                    with self.assertRaises(ValueError):
                        canonical.align_canonical_senses(
                            client=None,
                            word="nail",
                            pos="noun",
                            cambridge_senses=cambridge,
                            longman_senses=longman,
                            oxford_senses=[],
                            model="fixture-model",
                            reasoning_effort="medium",
                            cache=cache,
                        )
                self.assertEqual({}, cache.data)

    def test_low_confidence_links_are_not_applied(self):
        response = {
            "alignments": [alignment("C1", ["L1"], confidence=0.74)],
            "unmatched": [],
        }
        result, _, _ = self.run_alignment(
            response,
            [sense("cambridge", "C1", "definition")],
            [sense("longman", "L1", "definition")],
        )
        self.assertEqual([], result[0].longman_sense_ids)
        self.assertEqual("unmatched", result[0].relation)

    def test_pos_scope_excludes_other_pos_before_prompt_and_validation(self):
        cambridge = [
            sense("cambridge", "C-noun", "noun definition"),
            sense("cambridge", "C-verb", "verb definition", pos="verb"),
        ]
        longman = [sense("longman", "L-noun", "noun definition")]
        response = {
            "alignments": [alignment("C-noun", ["L-noun"])],
            "unmatched": [],
        }
        result, generate, _ = self.run_alignment(response, cambridge, longman)
        payload = json.loads(generate.call_args.kwargs["user_prompt"])
        self.assertEqual(["C-noun"], [item["sense_id"] for item in payload["cambridge_senses"]])
        self.assertEqual(["C-noun"], [item.cambridge_sense_id for item in result])

    def test_alignment_cache_key_is_order_independent_and_golden(self):
        senses = [
            {"source": "longman", "sense_id": "L1", "definition": "longman"},
            {"source": "cambridge", "sense_id": "C1", "definition": "cambridge"},
        ]
        key = build_alignment_cache_key(
            word="nail",
            pos="noun",
            model="fixture-model",
            schema_version=canonical.SENSE_ALIGNMENT_SCHEMA_VERSION,
            senses=senses,
        )
        reversed_key = build_alignment_cache_key(
            word="nail",
            pos="noun",
            model="fixture-model",
            schema_version=canonical.SENSE_ALIGNMENT_SCHEMA_VERSION,
            senses=list(reversed(senses)),
        )
        self.assertEqual(key, reversed_key)
        self.assertEqual("5d9fa79c70495e2cd5444ecf44b38df59ed256e8", key)

    def test_canonical_service_fetches_each_provider_once_for_one_pos(self):
        provider_senses = {
            "cambridge": [sense("cambridge", "C1", "definition")],
            "longman": [sense("longman", "L1", "definition")],
            "oxford": [sense("oxford", "O1", "definition")],
        }

        def provider_entry(source):
            return [
                ProviderEntry(
                    source=source,
                    dataset="fixture",
                    native_id="",
                    word="nail",
                    pos="noun",
                    pronunciation=WordPronunciation(source=source),
                    senses=provider_senses[source],
                )
            ]

        response = {
            "alignments": [alignment("C1", ["L1"], ["O1"])],
            "unmatched": [],
        }
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.object(
                canonical,
                "fetch_cambridge_provider_entries",
                return_value=provider_entry("cambridge"),
            ) as cambridge_fetch,
            patch.object(
                canonical,
                "fetch_longman_provider_entries",
                return_value=provider_entry("longman"),
            ) as longman_fetch,
            patch.object(
                canonical,
                "fetch_oxford_provider_entries",
                return_value=provider_entry("oxford"),
            ) as oxford_fetch,
            patch.object(canonical, "generate_json", return_value=response) as generate,
        ):
            result = canonical.fetch_and_align_canonical_senses(
                client=None,
                word="nail",
                pos="noun",
                model="fixture-model",
                reasoning_effort="medium",
                cache=CacheStore(Path(tmp) / "cache.json"),
            )
        cambridge_fetch.assert_called_once_with("nail")
        longman_fetch.assert_called_once_with("nail")
        oxford_fetch.assert_called_once_with("nail")
        generate.assert_called_once()
        self.assertEqual(["L1"], result[0].longman_sense_ids)
        self.assertEqual(["O1"], result[0].oxford_sense_ids)


if __name__ == "__main__":
    unittest.main()
