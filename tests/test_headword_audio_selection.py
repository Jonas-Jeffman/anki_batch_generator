from __future__ import annotations

import unittest

from tests import support  # noqa: F401 - installs offline dependency stubs

from anki_generator.dictionary.pronunciation import (
    headword_audio_selection_scope,
    ordered_headword_audio_urls,
)
from anki_generator.models import ProviderEntry, WordPronunciation


def entry(source: str, word: str, pos: str, audio_url: str) -> ProviderEntry:
    return ProviderEntry(
        source=source,
        dataset="fixture",
        native_id=f"{source}:{word}:{pos}:{audio_url}",
        word=word,
        pos=pos,
        pronunciation=WordPronunciation(
            source=source,
            pos=pos,
            audio_uk_url=audio_url,
        ),
    )


class HeadwordAudioSelectionTests(unittest.TestCase):
    def test_priority_and_sharing_matrix(self):
        oxford_noun = "https://oxford.test/word-noun.mp3"
        oxford_verb = "https://oxford.test/word-verb.mp3"
        oxford_shared = "https://oxford.test/word.mp3"
        cambridge_noun = "https://dictionary.cambridge.org/word-noun.mp3"
        longman_noun = "https://www.ldoceonline.com/word-noun.mp3"
        cases = {
            "exact Oxford wins": {
                "pos": "noun",
                "entries": [
                    entry("oxford", "word", "noun", oxford_noun),
                    entry("cambridge", "word", "noun", cambridge_noun),
                    entry("longman", "word", "noun", longman_noun),
                ],
                "expected": [
                    (oxford_noun, "oxford", "exact_pos"),
                    (cambridge_noun, "cambridge", "exact_pos"),
                    (longman_noun, "longman", "exact_pos"),
                ],
            },
            "unique Oxford headword audio is shared": {
                "pos": "noun",
                "entries": [
                    entry("oxford", "word", "adjective", oxford_shared),
                    entry("cambridge", "word", "noun", cambridge_noun),
                ],
                "expected": [
                    (oxford_shared, "oxford", "headword_shared"),
                    (cambridge_noun, "cambridge", "exact_pos"),
                ],
            },
            "ambiguous Oxford falls back to Cambridge": {
                "pos": "adjective",
                "entries": [
                    entry("oxford", "word", "noun", oxford_noun),
                    entry("oxford", "word", "verb", oxford_verb),
                    entry("cambridge", "word", "adjective", cambridge_noun),
                    entry("longman", "word", "adjective", longman_noun),
                ],
                "expected": [
                    (cambridge_noun, "cambridge", "exact_pos"),
                    (longman_noun, "longman", "exact_pos"),
                ],
            },
            "missing Oxford falls back to Cambridge then Longman": {
                "pos": "noun",
                "entries": [
                    entry("cambridge", "word", "noun", cambridge_noun),
                    entry("longman", "word", "noun", longman_noun),
                ],
                "expected": [
                    (cambridge_noun, "cambridge", "exact_pos"),
                    (longman_noun, "longman", "exact_pos"),
                ],
            },
            "only Longman remains usable": {
                "pos": "noun",
                "entries": [
                    entry("longman", "word", "adjective", longman_noun),
                ],
                "expected": [
                    (longman_noun, "longman", "headword_shared"),
                ],
            },
            "no dictionary audio": {
                "pos": "noun",
                "entries": [entry("oxford", "word", "noun", "")],
                "expected": [],
            },
        }

        for name, case in cases.items():
            with self.subTest(name=name):
                candidates = ordered_headword_audio_urls(
                    case["entries"], "word", case["pos"]
                )
                actual = [
                    (
                        url,
                        source,
                        headword_audio_selection_scope(
                            case["entries"],
                            "word",
                            case["pos"],
                            source,
                            url,
                        ),
                    )
                    for url, source in candidates
                ]
                self.assertEqual(case["expected"], actual)

    def test_unique_oxford_headword_audio_is_shared_across_missing_pos(self):
        oxford = "https://www.oxfordlearnersdictionaries.com/mutual.mp3"
        cambridge = "https://dictionary.cambridge.org/mutual-noun.mp3"
        longman = "https://www.ldoceonline.com/mutual-noun.mp3"
        entries = [
            entry("oxford", "mutual", "adjective", oxford),
            entry("cambridge", "mutual", "noun", cambridge),
            entry("longman", "mutual", "noun", longman),
        ]
        self.assertEqual(
            [(oxford, "oxford"), (cambridge, "cambridge"), (longman, "longman")],
            ordered_headword_audio_urls(entries, "mutual", "noun"),
        )

    def test_exact_pos_keeps_distinct_homograph_pronunciations(self):
        noun = "https://www.oxfordlearnersdictionaries.com/record-noun.mp3"
        verb = "https://www.oxfordlearnersdictionaries.com/record-verb.mp3"
        entries = [
            entry("oxford", "record", "noun", noun),
            entry("oxford", "record", "verb", verb),
        ]
        self.assertEqual(
            [(noun, "oxford")],
            ordered_headword_audio_urls(entries, "record", "noun"),
        )
        self.assertEqual(
            [(verb, "oxford")],
            ordered_headword_audio_urls(entries, "record", "verb"),
        )

    def test_same_url_across_pos_is_safe_for_an_unlisted_pos(self):
        shared = "https://www.oxfordlearnersdictionaries.com/nail.mp3"
        entries = [
            entry("oxford", "nail", "noun", shared),
            entry("oxford", "nail", "verb", shared),
        ]
        self.assertEqual(
            [(shared, "oxford")],
            ordered_headword_audio_urls(entries, "nail", "adjective"),
        )

    def test_ambiguous_oxford_audio_without_exact_pos_is_not_guessed(self):
        cambridge = "https://dictionary.cambridge.org/record-adjective.mp3"
        entries = [
            entry("oxford", "record", "noun", "https://oxford.test/noun.mp3"),
            entry("oxford", "record", "verb", "https://oxford.test/verb.mp3"),
            entry("cambridge", "record", "adjective", cambridge),
        ]
        self.assertEqual(
            [(cambridge, "cambridge")],
            ordered_headword_audio_urls(entries, "record", "adjective"),
        )

    def test_different_headwords_are_not_merged(self):
        lower = "https://oxford.test/rose.mp3"
        entries = [
            entry("oxford", "rose", "noun", lower),
            entry("oxford", "Rose", "noun", "https://oxford.test/name.mp3"),
            entry("oxford", "rosé", "noun", "https://oxford.test/drink.mp3"),
        ]
        self.assertEqual(
            [(lower, "oxford")],
            ordered_headword_audio_urls(entries, "rose", "noun"),
        )

    def test_empty_headword_never_selects_an_unowned_pronunciation(self):
        entries = [
            entry("oxford", "", "noun", "https://oxford.test/unknown.mp3"),
        ]
        self.assertEqual([], ordered_headword_audio_urls(entries, "", "noun"))

    def test_urls_are_normalized_and_stably_deduplicated(self):
        cambridge = "https://dictionary.cambridge.org/media/mutual.mp3"
        entries = [
            entry("cambridge", "mutual", "adjective", f"  {cambridge}  "),
            entry("cambridge", "mutual", "noun", cambridge),
            entry("longman", "mutual", "noun", ""),
        ]
        self.assertEqual(
            [(cambridge, "cambridge")],
            ordered_headword_audio_urls(entries, "mutual", "noun"),
        )


if __name__ == "__main__":
    unittest.main()
