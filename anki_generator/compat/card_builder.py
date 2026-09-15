#!/usr/bin/env python3
"""Compatibility exports for the split cards package."""

from __future__ import annotations

from anki_generator.cards.builder import build_card, build_cards, collect_audio_fallback_urls
from anki_generator.cards.english import (
    build_canonical_sense_card,
    expand_english_item,
    resolve_canonical_content,
)
from anki_generator.cards.preview import run_dictionary_test_only, write_dict_preview_json, write_preview_json
from anki_generator.cards.renderers import build_en_word_card, build_ja_word_card, build_knowledge_card
from anki_generator.llm.content import call_openai_json
from anki_generator.llm.prompts import (
    PROMPT_MAP,
    build_en_word_prompt,
    build_interest_prompt,
    build_interview_prompt,
    build_ja_word_prompt,
    build_paper_prompt,
    build_user_payload,
)


__all__ = [
    "build_card",
    "build_cards",
    "build_canonical_sense_card",
    "expand_english_item",
    "resolve_canonical_content",
    "call_openai_json",
    "collect_audio_fallback_urls",
    "build_en_word_card",
    "build_ja_word_card",
    "build_knowledge_card",
    "write_preview_json",
    "write_dict_preview_json",
    "run_dictionary_test_only",
    "PROMPT_MAP",
    "build_en_word_prompt",
    "build_ja_word_prompt",
    "build_interview_prompt",
    "build_paper_prompt",
    "build_interest_prompt",
    "build_user_payload",
]
