from __future__ import annotations

import argparse
import unittest

from anki_generator.inputs.english import parse_english_term
from anki_generator.models import ParsedEnglishTerm
from anki_generator.inputs.loader import load_items


class EnglishTermSenseInputTests(unittest.TestCase):
    def test_word_pos_and_pos_index_are_distinct_states(self):
        cases = {
            "nail": ParsedEnglishTerm("nail", "nail"),
            "trunk noun": ParsedEnglishTerm("trunk noun", "trunk", "noun"),
            "record noun 2": ParsedEnglishTerm("record noun 2", "record", "noun", 2),
            "record n 2": ParsedEnglishTerm("record n 2", "record", "noun", 2),
            "record (noun) 2": ParsedEnglishTerm("record (noun) 2", "record", "noun", 2),
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(expected, parse_english_term(raw))

    def test_numbers_without_a_preceding_pos_remain_part_of_the_word(self):
        cases = {
            "formula 1": ParsedEnglishTerm("formula 1", "formula 1"),
            "catch 22": ParsedEnglishTerm("catch 22", "catch 22"),
            "level 2 thinking": ParsedEnglishTerm("level 2 thinking", "level 2 thinking"),
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(expected, parse_english_term(raw))

    def test_phrases_and_non_numeric_suffixes_keep_legacy_parsing(self):
        cases = {
            "play hooky": ParsedEnglishTerm("play hooky", "play hooky"),
            "take off verb": ParsedEnglishTerm("take off verb", "take off", "verb"),
            "record noun two": ParsedEnglishTerm("record noun two", "record two", "noun"),
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(expected, parse_english_term(raw))

    def test_zero_and_negative_sense_indexes_are_rejected(self):
        for raw in ("record noun 0", "record noun -1"):
            with self.subTest(raw=raw):
                with self.assertRaisesRegex(ValueError, "positive integer"):
                    parse_english_term(raw)

    def test_input_deduplication_preserves_distinct_sense_requests(self):
        args = argparse.Namespace(
            terms_json='["record noun 1", "record noun 2", "record noun 1"]',
            terms_file="",
            mode="en_word",
            hint="",
            tags=[],
        )
        self.assertEqual(
            ["record noun 1", "record noun 2"],
            [item.term for item in load_items(args)],
        )


if __name__ == "__main__":
    unittest.main()
