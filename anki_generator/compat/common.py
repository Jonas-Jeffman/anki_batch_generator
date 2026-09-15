#!/usr/bin/env python3
"""Deprecated compatibility exports for the former shared module."""

from __future__ import annotations

from anki_generator.llm.cache import CacheStore, build_cache_key
from anki_generator.config import (
    DEFAULT_SLEEP,
    DEFAULT_TERMS_JSON,
    DEFAULT_TERMS_TXT,
    DEFAULT_TEXT_MODEL,
    DEFAULT_TTS_MODEL,
    LLM_SCHEMA_VERSION,
    LOCAL_OPENAI_KEY_FILE,
    SCRIPT_DIR,
    SUPPORTED_MODES,
    read_optional_local_openai_key,
    resolve_openai_api_key,
    resolve_openai_base_url,
)
from anki_generator.inputs.english import (
    POS_CANONICAL,
    POS_PATTERN,
    en_word_uses_dictionary_lookup,
    extract_pos_tags,
    is_single_word_term,
    is_two_word_term,
    lexical_word_count,
    normalize_pos_tag,
    parse_english_term,
    strip_pos_labels_from_term,
)
from anki_generator.models import (
    AudioAsset,
    BuiltCard,
    DictionaryEntryResult,
    DictionarySenseCandidate,
    EnglishPronunciationInfo,
    InputItem,
    ParsedEnglishTerm,
)
from anki_generator.inputs.loader import (
    load_items,
    read_terms_from_json_file,
    read_terms_from_json_string,
    read_terms_from_path,
    read_terms_from_txt_file,
)
from anki_generator.utils import html_escape, retry_call, slugify, stable_anki_id, stable_guid


__all__ = [
    "SUPPORTED_MODES",
    "DEFAULT_TEXT_MODEL",
    "DEFAULT_TTS_MODEL",
    "DEFAULT_SLEEP",
    "LLM_SCHEMA_VERSION",
    "SCRIPT_DIR",
    "DEFAULT_TERMS_JSON",
    "DEFAULT_TERMS_TXT",
    "LOCAL_OPENAI_KEY_FILE",
    "read_optional_local_openai_key",
    "resolve_openai_api_key",
    "resolve_openai_base_url",
    "InputItem",
    "ParsedEnglishTerm",
    "BuiltCard",
    "AudioAsset",
    "EnglishPronunciationInfo",
    "DictionarySenseCandidate",
    "DictionaryEntryResult",
    "CacheStore",
    "build_cache_key",
    "stable_anki_id",
    "stable_guid",
    "slugify",
    "html_escape",
    "retry_call",
    "read_terms_from_json_string",
    "read_terms_from_json_file",
    "read_terms_from_txt_file",
    "read_terms_from_path",
    "load_items",
    "POS_CANONICAL",
    "POS_PATTERN",
    "normalize_pos_tag",
    "parse_english_term",
    "extract_pos_tags",
    "strip_pos_labels_from_term",
    "is_single_word_term",
    "lexical_word_count",
    "is_two_word_term",
    "en_word_uses_dictionary_lookup",
]
