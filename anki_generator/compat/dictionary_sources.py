#!/usr/bin/env python3
"""Deprecated compatibility exports for the split dictionary package."""

from __future__ import annotations

from anki_generator.dictionary.cambridge import (
    fetch_cambridge_image_url,
    fetch_cambridge_sense_candidates,
    fetch_english_from_cambridge,
)
from anki_generator.dictionary.common import (
    is_candidate_dictionary_image_url,
    is_cross_reference_definition,
    is_plausible_dictionary_image,
    normalize_dictionary_url,
)
from anki_generator.dictionary.images import (
    dictionary_source_from_url,
    fetch_dictionary_image_url,
    fetch_wikipedia_image_url,
    is_common_concrete_noun,
    should_attach_noun_image,
)
from anki_generator.dictionary.longman import (
    fetch_english_from_longman,
    fetch_longman_image_url,
    fetch_longman_sense_candidates,
)
from anki_generator.dictionary.oxford import (
    fetch_english_from_oxford,
    fetch_oxford_image_url,
    fetch_oxford_sense_candidates,
)
from anki_generator.dictionary.service import (
    dictionary_result_preview,
    fetch_best_dictionary_sense,
    fetch_dictionary_entries,
    fetch_english_pronunciation,
    fetch_english_pronunciation_two_words,
    merge_dictionary_entries,
)


__all__ = [
    "is_cross_reference_definition",
    "fetch_english_from_oxford",
    "fetch_english_from_cambridge",
    "fetch_english_from_longman",
    "fetch_english_pronunciation",
    "fetch_english_pronunciation_two_words",
    "should_attach_noun_image",
    "is_common_concrete_noun",
    "fetch_dictionary_entries",
    "merge_dictionary_entries",
    "dictionary_result_preview",
    "fetch_cambridge_sense_candidates",
    "fetch_oxford_sense_candidates",
    "fetch_longman_sense_candidates",
    "fetch_best_dictionary_sense",
    "fetch_cambridge_image_url",
    "fetch_longman_image_url",
    "fetch_oxford_image_url",
    "fetch_dictionary_image_url",
    "fetch_wikipedia_image_url",
    "dictionary_source_from_url",
    "normalize_dictionary_url",
    "is_plausible_dictionary_image",
    "is_candidate_dictionary_image_url",
]
