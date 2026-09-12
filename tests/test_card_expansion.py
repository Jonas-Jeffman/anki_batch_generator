from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests import support  # noqa: F401 - installs offline dependency stubs

from cache import CacheStore
from canonical_store import CanonicalStore
from cards import builder
from models import (
    AudioAsset,
    BuiltCard,
    CanonicalSense,
    CanonicalSenseRequest,
    InputItem,
    ProviderEntry,
    ProviderSense,
    WordPronunciation,
)
from dictionary.cambridge import parse_cambridge_provider_entries
from dictionary.longman import parse_longman_provider_entries
from dictionary.oxford import parse_oxford_provider_entries
from tests.dictionary_page_snapshot import read_page


def entry(source: str, word: str, *senses: tuple[str, str, str]) -> ProviderEntry:
    provider_senses = [
        ProviderSense(source=source, native_id=native_id, pos=pos, definition=definition)
        for native_id, pos, definition in senses
    ]
    return ProviderEntry(
        source=source,
        dataset="fixture",
        native_id=f"{source}:{word}",
        word=word,
        pos=provider_senses[0].pos if provider_senses else "",
        pronunciation=WordPronunciation(source=source),
        senses=provider_senses,
    )


def canonical(word: str, pos: str, index: int) -> CanonicalSense:
    native_id = f"C-{word}-{pos}-{index}"
    return CanonicalSense(
        word=word,
        pos=pos,
        index=index,
        backbone_source="cambridge",
        backbone_sense_id=native_id,
        cambridge_sense_id=native_id,
        canonical_key=f"canonical::{word}-{pos}-{index}",
        fingerprint=f"fingerprint-{index}",
    )


class CardExpansionTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        root = Path(self.temp_dir.name)
        self.cache = CacheStore(root / "cache.json")
        self.manifest = CanonicalStore(root / "canonical.json")

    def _expand(self, term: str, word: str, pos_counts: dict[str, int]):
        cambridge = entry(
            "cambridge",
            word,
            *[
                (f"C-{word}-{pos}-{index}", pos, f"definition {pos} {index}")
                for pos, count in pos_counts.items()
                for index in range(1, count + 1)
            ],
        )

        def aligned(**kwargs):
            pos = kwargs["pos"]
            return [canonical(word, pos, index) for index in range(1, pos_counts[pos] + 1)]

        with (
            patch.object(builder, "fetch_cambridge_provider_entries", return_value=[cambridge]),
            patch.object(builder, "fetch_longman_provider_entries", return_value=[]),
            patch.object(builder, "fetch_oxford_provider_entries", return_value=[]),
            patch.object(builder, "align_canonical_senses", side_effect=aligned) as align,
        ):
            requests = builder.expand_english_item(
                client=None,
                item=InputItem("en_word", term),
                model="fixture-model",
                reasoning_effort="medium",
                cache=self.cache,
                canonical_store=self.manifest,
            )
        return requests, align

    def test_trunk_discovers_pos_and_expands_all_manifest_senses(self):
        requests, align = self._expand("trunk", "trunk", {"noun": 3, "verb": 2})
        self.assertEqual(
            [("noun", 1), ("noun", 2), ("noun", 3), ("verb", 1), ("verb", 2)],
            [(request.pos, request.canonical_sense.index) for request in requests],
        )
        self.assertEqual(["noun", "verb"], [call.kwargs["pos"] for call in align.call_args_list])

    def test_trunk_noun_expands_only_noun_and_explicit_index_filters_after_alignment(self):
        all_nouns, align_all = self._expand(
            "trunk noun", "trunk", {"noun": 3, "verb": 2}
        )
        selected, align_selected = self._expand(
            "trunk noun 2", "trunk", {"noun": 3, "verb": 2}
        )
        self.assertEqual([1, 2, 3], [item.canonical_sense.index for item in all_nouns])
        self.assertEqual([2], [item.canonical_sense.index for item in selected])
        self.assertEqual(["noun"], [call.kwargs["pos"] for call in align_all.call_args_list])
        self.assertEqual(["noun"], [call.kwargs["pos"] for call in align_selected.call_args_list])

    def test_nail_verb_never_includes_noun(self):
        requests, _align = self._expand("nail verb", "nail", {"noun": 2, "verb": 3})
        self.assertEqual(
            [("verb", 1), ("verb", 2), ("verb", 3)],
            [(request.pos, request.canonical_sense.index) for request in requests],
        )

    def test_real_trunk_and_nail_fixtures_expand_and_filter_exactly(self):
        parsers = {
            "cambridge": (
                parse_cambridge_provider_entries,
                "https://dictionary.cambridge.org/dictionary/english/",
            ),
            "longman": (
                parse_longman_provider_entries,
                "https://www.ldoceonline.com/dictionary/",
            ),
            "oxford": (
                parse_oxford_provider_entries,
                "https://www.oxfordlearnersdictionaries.com/definition/english/",
            ),
        }

        def entries(source, word):
            parser, base_url = parsers[source]
            return parser(read_page(word, source), base_url + word, word)

        def aligned(**kwargs):
            transient = [
                CanonicalSense(
                    word=kwargs["word"],
                    pos=kwargs["pos"],
                    index=index,
                    backbone_source="cambridge",
                    backbone_sense_id=sense.native_id,
                    cambridge_sense_id=sense.native_id,
                )
                for index, sense in enumerate(kwargs["cambridge_senses"], start=1)
            ]
            return kwargs["canonical_store"].reconcile(
                word=kwargs["word"],
                pos=kwargs["pos"],
                canonical_senses=transient,
                provider_senses=[
                    *kwargs["cambridge_senses"],
                    *kwargs["longman_senses"],
                    *kwargs["oxford_senses"],
                ],
            )

        with (
            patch.object(
                builder,
                "fetch_cambridge_provider_entries",
                side_effect=lambda word: entries("cambridge", word),
            ),
            patch.object(
                builder,
                "fetch_longman_provider_entries",
                side_effect=lambda word: entries("longman", word),
            ),
            patch.object(
                builder,
                "fetch_oxford_provider_entries",
                side_effect=lambda word: entries("oxford", word),
            ),
            patch.object(builder, "align_canonical_senses", side_effect=aligned),
        ):
            results = {}
            for term in ("trunk", "trunk noun", "trunk noun 2", "nail verb"):
                results[term] = builder.expand_english_item(
                    client=None,
                    item=InputItem("en_word", term),
                    model="fixture-model",
                    reasoning_effort="medium",
                    cache=self.cache,
                    canonical_store=self.manifest,
                )

        self.assertEqual(8, len(results["trunk"]))
        self.assertEqual(list(range(1, 9)), [
            request.canonical_sense.index for request in results["trunk noun"]
        ])
        self.assertEqual([2], [
            request.canonical_sense.index for request in results["trunk noun 2"]
        ])
        self.assertEqual(
            [("verb", 1), ("verb", 2), ("verb", 3), ("verb", 4)],
            [
                (request.pos, request.canonical_sense.index)
                for request in results["nail verb"]
            ],
        )

    def test_one_sense_failure_does_not_stop_remaining_senses(self):
        requests = [
            CanonicalSenseRequest(
                item=InputItem("en_word", "trunk noun"),
                word="trunk",
                pos="noun",
                canonical_sense=canonical("trunk", "noun", index),
            )
            for index in range(1, 4)
        ]
        first = BuiltCard("one", "back", [], "one")
        third = BuiltCard("three", "back", [], "three")
        with (
            patch.object(builder, "expand_english_item", return_value=requests),
            patch.object(
                builder,
                "build_canonical_sense_card",
                side_effect=[
                    (first, [], None),
                    RuntimeError("broken"),
                    (third, [], None),
                ],
            ),
        ):
            result = builder.build_cards(
                client=None,
                item=InputItem("en_word", "trunk noun"),
                model="fixture-model",
                tts_model="fixture-tts",
                audio_dir=Path(self.temp_dir.name) / "audio",
                image_dir=Path(self.temp_dir.name) / "image",
                tts_voice_en="alloy",
                tts_voice_ja="alloy",
                cache=self.cache,
                reasoning_effort="medium",
                canonical_store=self.manifest,
            )
        self.assertEqual([first, third], result.cards)
        self.assertEqual(["trunk noun 2: broken"], result.errors)

    def test_same_filename_from_multiple_senses_is_returned_once(self):
        requests = [
            CanonicalSenseRequest(
                item=InputItem("en_word", "trunk noun"),
                word="trunk",
                pos="noun",
                canonical_sense=canonical("trunk", "noun", index),
            )
            for index in (1, 2)
        ]
        shared_audio = AudioAsset("audio_en_trunk_noun.mp3", Path("shared.mp3"))
        cards = [
            BuiltCard("one", "back", [], "one"),
            BuiltCard("two", "back", [], "two"),
        ]
        with (
            patch.object(builder, "expand_english_item", return_value=requests),
            patch.object(
                builder,
                "build_canonical_sense_card",
                side_effect=[
                    (cards[0], [shared_audio], None),
                    (cards[1], [shared_audio], None),
                ],
            ),
        ):
            result = builder.build_cards(
                client=None,
                item=InputItem("en_word", "trunk noun"),
                model="fixture-model",
                tts_model="fixture-tts",
                audio_dir=Path(self.temp_dir.name) / "audio",
                image_dir=Path(self.temp_dir.name) / "image",
                tts_voice_en="alloy",
                tts_voice_ja="alloy",
                cache=self.cache,
                reasoning_effort="medium",
                canonical_store=self.manifest,
            )
        self.assertEqual(cards, result.cards)
        self.assertEqual([shared_audio], result.assets)


if __name__ == "__main__":
    unittest.main()
