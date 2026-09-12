from __future__ import annotations

import unittest

from tests import support  # noqa: F401 - installs offline dependency stubs

from cards.builder import resolve_canonical_content
from models import (
    CanonicalSense,
    CanonicalSenseRequest,
    InputItem,
    ProviderEntry,
    ProviderSense,
    WordPronunciation,
)
from tests.support import GOLDEN_ROOT, load_json
from utils import slugify, stable_guid


OXFORD_MUTUAL = (
    "https://www.oxfordlearnersdictionaries.com/media/english/uk_pron/"
    "m/mut/mutua/mutual__gb_1.mp3"
)
OXFORD_NAIL = (
    "https://www.oxfordlearnersdictionaries.com/media/english/uk_pron/"
    "n/nai/nail_/nail__gb_1.mp3"
)


def provider_entry(
    source: str,
    word: str,
    pos: str,
    ipa: str,
    audio_url: str,
) -> ProviderEntry:
    native_id = f"{source}:{word}:{pos}"
    return ProviderEntry(
        source=source,
        dataset="fixture",
        native_id=native_id,
        word=word,
        pos=pos,
        pronunciation=WordPronunciation(
            source=source,
            pos=pos,
            ipa_uk=ipa,
            audio_uk_url=audio_url,
        ),
        senses=[
            ProviderSense(
                source=source,
                native_id=f"{native_id}:sense",
                pos=pos,
                definition=f"{word} {pos} definition",
            )
        ],
    )


def request(word: str, pos: str, entries: list[ProviderEntry]) -> CanonicalSenseRequest:
    sense_ids = {
        entry.source: entry.senses[0].native_id
        for entry in entries
        if entry.pos == pos
    }
    backbone_id = sense_ids.get("cambridge") or next(iter(sense_ids.values()))
    return CanonicalSenseRequest(
        item=InputItem("en_word", f"{word} {pos}"),
        word=word,
        pos=pos,
        canonical_sense=CanonicalSense(
            word=word,
            pos=pos,
            index=1,
            backbone_source="cambridge",
            backbone_sense_id=backbone_id,
            cambridge_sense_id=sense_ids.get("cambridge", ""),
            longman_sense_ids=[sense_ids["longman"]] if "longman" in sense_ids else [],
            oxford_sense_ids=[sense_ids["oxford"]] if "oxford" in sense_ids else [],
            canonical_key=f"canonical::{word}:{pos}:1",
        ),
        provider_entries=entries,
    )


def snapshot(word: str, pos: str, entries: list[ProviderEntry]) -> dict:
    content = resolve_canonical_content(request(word, pos, entries))
    media_term = f"{word} {pos}"
    return {
        "ipa": {"value": content.ipa.value, "source": content.ipa.source},
        "audio_candidates": [
            {
                "source": field.source,
                "url": field.value,
                "selection_scope": field.selection_scope,
            }
            for field in content.word_audio_urls
        ],
        "media_term": media_term,
        "media_filename": (
            f"audio_en_{slugify(media_term)}_"
            f"{stable_guid('en_word', media_term)[:8]}.mp3"
        ),
    }


class HeadwordAudioBaselineTests(unittest.TestCase):
    maxDiff = None

    def test_headword_audio_selection_matches_golden(self):
        mutual_entries = [
            provider_entry("oxford", "mutual", "adjective", "/ˈmjuːtʃuəl/", OXFORD_MUTUAL),
            provider_entry(
                "cambridge",
                "mutual",
                "adjective",
                "/ˈmjuː.tʃu.əl/",
                "https://dictionary.cambridge.org/media/english/uk_pron/m/mut/mutual-adjective.mp3",
            ),
            provider_entry(
                "cambridge",
                "mutual",
                "noun",
                "/ˈmjuː.tʃu.əl/",
                "https://dictionary.cambridge.org/media/english/uk_pron/m/mut/mutual-noun.mp3",
            ),
            provider_entry(
                "longman",
                "mutual",
                "adjective",
                "ˈmjuːtʃuəl",
                "https://www.ldoceonline.com/media/english/breProns/mutual-adjective.mp3",
            ),
            provider_entry(
                "longman",
                "mutual",
                "noun",
                "ˈmjuːtʃuəl",
                "https://www.ldoceonline.com/media/english/breProns/mutual-noun.mp3",
            ),
        ]
        nail_entries = [
            provider_entry("oxford", "nail", "noun", "/neɪl/", OXFORD_NAIL),
            provider_entry("oxford", "nail", "verb", "/neɪl/", OXFORD_NAIL),
        ]
        record_entries = [
            provider_entry(
                "oxford",
                "record",
                "noun",
                "/ˈrekɔːd/",
                "https://www.oxfordlearnersdictionaries.com/media/english/uk_pron/r/rec/recor/record__gb_1.mp3",
            ),
            provider_entry(
                "oxford",
                "record",
                "verb",
                "/rɪˈkɔːd/",
                "https://www.oxfordlearnersdictionaries.com/media/english/uk_pron/r/rec/recor/record__gb_2.mp3",
            ),
        ]

        actual = {
            "mutual adjective": snapshot("mutual", "adjective", mutual_entries),
            "mutual noun": snapshot("mutual", "noun", mutual_entries),
            "nail noun": snapshot("nail", "noun", nail_entries),
            "nail verb": snapshot("nail", "verb", nail_entries),
            "record noun": snapshot("record", "noun", record_entries),
            "record verb": snapshot("record", "verb", record_entries),
        }
        expected = load_json(GOLDEN_ROOT / "headword_audio_baseline.json")
        self.assertEqual(expected, actual)

        self.assertEqual("oxford", actual["mutual noun"]["audio_candidates"][0]["source"])
        mutual_noun_content = resolve_canonical_content(
            request("mutual", "noun", mutual_entries)
        )
        self.assertEqual("longman", mutual_noun_content.definition.source)
        self.assertEqual("cambridge", mutual_noun_content.ipa.source)
        self.assertEqual(
            actual["nail noun"]["audio_candidates"][0]["url"],
            actual["nail verb"]["audio_candidates"][0]["url"],
        )
        self.assertNotEqual(
            actual["record noun"]["audio_candidates"][0]["url"],
            actual["record verb"]["audio_candidates"][0]["url"],
        )


if __name__ == "__main__":
    unittest.main()
