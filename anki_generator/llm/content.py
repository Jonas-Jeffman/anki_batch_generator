"""Build mode-specific prompts and request card content from the LLM."""

from __future__ import annotations

from typing import Dict

from anki_generator.llm.client import LLMClient, generate_json
from anki_generator.llm.prompts import PROMPT_MAP, build_user_payload
from anki_generator.models import InputItem


def call_openai_json(
    client: LLMClient,
    model: str,
    item: InputItem,
    phonetic_hint: str,
    term_for_pronunciation: str,
    reasoning_effort: str,
    phonetic_source: str = "",
    dictionary_definition: str = "",
    requested_pos: str = "",
    parsed_word: str = "",
) -> Dict:
    system_prompt = PROMPT_MAP[item.mode]()
    user_prompt = build_user_payload(
        item,
        phonetic_hint,
        term_for_pronunciation,
        phonetic_source,
        dictionary_definition=dictionary_definition,
        requested_pos=requested_pos,
        parsed_word=parsed_word,
    )
    return generate_json(
        client,
        model=model,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        reasoning_effort=reasoning_effort,
    )


__all__ = ["call_openai_json"]
